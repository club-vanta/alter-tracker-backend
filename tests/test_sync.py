"""Tests for the meetup sync endpoint.

These tests verify the guest sync flow: fetching RSVPs from Mazmo and
upserting them into the local database. The Mazmo HTTP client is always
mocked - we're testing our logic, not the external API.

Key invariant: sync NEVER overwrites check-in data (has_arrived, arrival_order).
This protects against data loss if someone syncs during an active event.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import Guest, Meetup, MeetupRsvp, Organization, OrgRole
from tests.conftest import make_guest, make_org_member, make_rsvp

# -- Sync behaviour ------------------------------------------------------------


def test_sync_inserts_all_rsvpd_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync creates Guest records for all RSVPed users.

    WHY: This is the happy path. When staff clicks "sync", we fetch the
    RSVP list from Mazmo and create local Guest records so we can track
    check-ins offline.
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 2
    assert data["total_in_db"] == 2
    handles = {g.mazmo_handle for g in session.exec(select(Guest)).all()}
    assert handles == {"alice", "bob"}


def test_sync_creates_rsvp_records_for_meetup(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync creates MeetupRsvp records linking guests to the meetup.

    WHY: The MeetupRsvp table tracks RSVP state per-meetup. A guest can RSVP
    to multiple meetups, so we need these association records.
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    rsvps = session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id)).all()
    assert len(rsvps) == 2
    guest_ids = {r.guest_id for r in rsvps}
    expected_guest_ids = {
        g.id
        for g in session.exec(select(Guest).where(Guest.mazmo_user_id.in_({111, 222}))).all()  # type: ignore[union-attr]
    }
    assert guest_ids == expected_guest_ids


def test_sync_updates_existing_rsvp_on_re_rsvp(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that re-RSVPing reactivates a cancelled RSVP.

    WHY: Users can cancel and then re-RSVP on Mazmo. When they come back,
    we should flip cancelled_rsvp back to False so they appear in the
    guest list again.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")
    rsvp = make_rsvp(session, meetup=meetup, guest=alice)
    rsvp.cancelled_rsvp = True
    session.add(rsvp)
    session.flush()

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.cancelled_rsvp is False


def test_sync_does_not_overwrite_checkin_data_of_arrived_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync preserves check-in data (has_arrived, arrival_order).

    WHY: CRITICAL INVARIANT. If someone checks in at 8pm, then staff syncs
    at 8:30pm, we must NOT lose the check-in. The upsert only updates
    rsvp_time and cancelled_rsvp, never the check-in fields.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")
    rsvp = make_rsvp(
        session,
        meetup=meetup,
        guest=alice,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime(2026, 3, 17, 22, 0, tzinfo=UTC),
    )

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.has_arrived is True
    assert rsvp.arrival_order == 1


def test_sync_does_not_overwrite_payment_data_of_paid_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync preserves payment data (has_paid, paid_at, paid_by_id).

    WHY: Same invariant as check-in data. If an admin marks a guest as paid,
    then staff syncs later, we must NOT lose that payment mark. The upsert
    only updates rsvp_time and cancelled_rsvp, never the payment fields.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")
    rsvp = make_rsvp(
        session,
        meetup=meetup,
        guest=alice,
        has_paid=True,
        paid_at=datetime(2026, 3, 17, 21, 0, tzinfo=UTC),
    )

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.has_paid is True
    assert rsvp.paid_at is not None


def test_sync_with_empty_rsvp_list_returns_200_ok_with_zero_counts(
    client: TestClient, admin_headers: dict, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync handles empty RSVP lists gracefully.

    WHY: Edge case - a new event might have zero RSVPs. The endpoint should
    return successfully with zero counts, not error.
    """
    mock_mazmo.fetch_rsvps.return_value = {}

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 0
    assert data["total_in_db"] == 0


def test_sync_is_accessible_by_regular_staff_returns_200_ok(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup: Meetup,
    org: Organization,
    staff_user,
    mock_mazmo: AsyncMock,
):
    """
    Verify that regular staff (not just admins) can trigger sync.

    WHY: Door staff need to sync without waiting for an admin. This is a
    read-heavy operation that doesn't modify sensitive data, so we allow
    all approved staff to trigger it.
    """
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK


def test_sync_returns_404_for_nonexistent_meetup(
    client: TestClient, admin_headers: dict, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that syncing a nonexistent meetup returns 404.

    WHY: Clear error handling. If someone passes a wrong meetup ID (typo,
    deleted meetup), we should return 404 not a confusing server error.
    """
    import uuid

    fake_id = uuid.uuid4()
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{fake_id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Auth guards ---------------------------------------------------------------


def test_sync_without_token_returns_401_unauthorized(client: TestClient, meetup: Meetup):
    """
    Verify that unauthenticated sync attempts are rejected.

    WHY: Only logged-in staff should be able to sync. Anonymous users
    shouldn't be able to trigger API calls to Mazmo.
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Mazmo API error handling --------------------------------------------------


def test_sync_when_mazmo_returns_503_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that Mazmo HTTP errors are translated to 502 Bad Gateway.

    WHY: If Mazmo returns a 5xx error, that's their problem, not ours.
    We return 502 to indicate "upstream server error" so the frontend
    can show an appropriate message.
    """
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("503", request=request, response=response)
    mock_mazmo.fetch_rsvps.side_effect = error

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


def test_sync_when_mazmo_is_unreachable_responds_with_504_gateway_timeout(
    client: TestClient, admin_headers: dict, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that network errors are translated to 504 Gateway Timeout.

    WHY: If we can't reach Mazmo at all (DNS failure, connection refused),
    that's different from a 5xx error. 504 indicates "couldn't connect"
    so staff knows to check their network or try again later.
    """
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    error = httpx.ConnectError("Connection refused", request=request)

    mock_mazmo.fetch_rsvps.side_effect = error

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_sync_when_mazmo_returns_unexpected_shape_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that malformed Mazmo responses are handled gracefully.

    WHY: If Mazmo changes their API format, we'll fail to parse. Rather
    than crashing with a 500, we return 502 to indicate "upstream gave
    us bad data". This helps debugging.
    """
    mock_mazmo.fetch_rsvps.side_effect = ValueError("Missing 'joinedAt' key in response")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


# -- Check-in attribution ------------------------------------------------------


def test_checkin_stores_staff_id_who_performed_checkin(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup: Meetup,
    org: Organization,
    staff_user,
):
    """
    Verify that check-in records which staff member performed the check-in.

    WHY: Audit trail requirement. We need to know who checked in each guest
    for accountability and troubleshooting. If there's a dispute about
    whether someone attended, we can verify who performed the check-in.
    """
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    guest = make_guest(session, mazmo_user_id=999, mazmo_handle="checkintest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    # Verify the response includes who checked them in
    assert "checked_in_by" in data
    assert data["checked_in_by"]["id"] == staff_user.id
    assert data["checked_in_by"]["username"] == staff_user.username

    # Verify it's stored in the database
    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).first()
    assert rsvp is not None
    assert rsvp.checked_in_by_id == staff_user.id
