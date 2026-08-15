"""Tests for the meetup sync endpoint.

These tests verify the guest sync flow: fetching RSVPs from Mazmo and
upserting them into the local database. The Mazmo HTTP client is always
mocked - we're testing our logic, not the external API.

Key invariant: sync NEVER overwrites check-in data (has_arrived, arrival_order).
This protects against data loss if someone syncs during an active event.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
    Organization,
    OrgRole,
)
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


def test_new_rsvp_defaults_to_guest_type_normal_via_sync(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that a brand-new RSVP created by sync defaults to guest_type=NORMAL.

    WHY: The sync path builds MeetupRsvp() without ever setting guest_type,
    so it must rely on the model's default rather than needing explicit
    sync logic to write "NORMAL".
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup.id)
        .where(
            MeetupRsvp.guest_id.in_(  # type: ignore[attr-defined]
                select(Guest.id).where(Guest.mazmo_user_id == 111)
            )
        )
    ).one()
    assert rsvp.guest_type == "NORMAL"


def test_sync_never_overwrites_existing_guest_type(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that re-syncing a meetup preserves a guest's existing guest_type.

    WHY: Same invariant as has_paid/has_arrived - if an admin classified a
    guest as VENDOR, then staff syncs again later, the classification must
    survive. The upsert's ON CONFLICT DO UPDATE set_={} dict only touches
    rsvp_time and cancelled_rsvp, so this should already hold with zero
    production code changes; this test proves it.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")
    rsvp = make_rsvp(session, meetup=meetup, guest=alice, guest_type="VENDOR")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.guest_type == "VENDOR"


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


# -- Displayname history via sync -----------------------------------------------


def test_sync_updates_displayname_when_mazmo_reports_different_value(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync updates Guest.displayname when Mazmo reports a
    different value than what we have stored.

    WHY: Before this change, sync used ON CONFLICT DO NOTHING and never
    reflected a Mazmo displayname change after the guest's first sync -
    see the Guest model's own docstring, which called this out.

    Also pins that the upsert's set_={...} touches displayname only:
    mazmo_handle, instagram_username, and the guest's internal id must
    all survive unchanged. Guards against a future widening of set_
    silently starting to overwrite user-entered data.
    """
    seeded = make_guest(
        session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name", instagram_username="alice.ig"
    )

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    assert guest.displayname == "Alice"
    assert guest.id == seeded.id
    assert guest.mazmo_handle == "alice"
    assert guest.instagram_username == "alice.ig"


def test_sync_does_not_update_displayname_when_unchanged(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync creates no history row or event when the displayname is
    already up to date.

    WHY: Regression guard - this is the noisy case the atomic
    IS DISTINCT FROM WHERE clause exists to avoid.
    """
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Alice")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []
    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert events == []


def test_sync_creates_history_row_with_source_sync_on_change_to_existing_guest(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """Verify the GuestDisplaynameHistory row written for a changed existing guest."""
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.SYNC
    assert history[0].displayname == "Alice"
    assert history[0].actor_id is None


def test_sync_creates_eventlog_for_change_to_existing_guest_with_org_id_null(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """Verify the GUEST_DISPLAYNAME_CHANGED event for a changed existing guest has org_id=None."""
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert event.org_id is None


def test_sync_new_guest_creates_history_row_but_no_eventlog(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a brand-new guest created by sync gets a history row but no
    GUEST_DISPLAYNAME_CHANGED event.

    WHY: A first value is not a "change" - logging it as one would flood
    the org timeline with noise on every large sync.
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.SYNC

    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert events == []


def test_sync_batch_with_mixed_new_and_changed_guests(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a single sync with one changed existing guest and one brand
    new guest gives each the correct treatment.

    WHY: mock_mazmo's FAKE data always returns both user 111 (alice) and
    222 (bob) in one batch - pre-seeding only 111 makes 111 the "changed"
    case and 222 the "new" case within the same sync call, exercising
    both branches of the RETURNING loop in one statement.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    bob = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()

    alice_history = session.exec(
        select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == alice.id)
    ).all()
    bob_history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == bob.id)).all()
    assert len(alice_history) == 1
    assert len(bob_history) == 1

    alice_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == alice.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    bob_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == bob.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(alice_events) == 1
    assert bob_events == []


# -- GuestMazmoProfile via sync --------------------------------------------------


def test_sync_creates_guest_mazmo_profile_for_new_guest(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync creates a GuestMazmoProfile row for a brand-new guest,
    populated from the same Mazmo user details used to create the guest.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/111/default.jpg"),
            age=25,
            gender="female",
            pronoun="she/her",
            suspended=False,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob",
            displayname="Bob",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    alice = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    profile = session.get(GuestMazmoProfile, alice.id)
    assert profile is not None
    assert profile.avatar_url == "https://cdn.mazmo.net/avatars/111/default.jpg"
    assert profile.age == 25
    assert profile.gender == "female"
    assert profile.pronoun == "she/her"
    assert profile.mazmo_suspended is False
    assert profile.mazmo_banned is False


def test_sync_updates_guest_mazmo_profile_on_subsequent_sync(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a second sync overwrites the same GuestMazmoProfile row (no
    duplicate) when Mazmo reports different age/mazmo_suspended values,
    and synced_at advances.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=None,
            age=25,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob",
            displayname="Bob",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
    }
    resp1 = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp1.status_code == status.HTTP_200_OK

    alice = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    first_profile = session.get(GuestMazmoProfile, alice.id)
    assert first_profile is not None
    first_synced_at = first_profile.synced_at

    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=None,
            age=26,
            gender=None,
            pronoun=None,
            suspended=True,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob",
            displayname="Bob",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
    }
    resp2 = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp2.status_code == status.HTTP_200_OK

    all_profiles = session.exec(select(GuestMazmoProfile).where(GuestMazmoProfile.guest_id == alice.id)).all()
    assert len(all_profiles) == 1
    updated_profile = all_profiles[0]
    assert updated_profile.age == 26
    assert updated_profile.mazmo_suspended is True
    assert updated_profile.synced_at >= first_synced_at


def test_sync_updates_guest_mazmo_profile_even_when_displayname_unchanged(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Regression guard: unlike GuestDisplaynameHistory, GuestMazmoProfile
    has no "only if changed" gate - a guest whose displayname is already
    up to date (and therefore produces no row in _upsert_guests's
    RETURNING set) must still get mazmo_suspended/age/etc. refreshed if
    Mazmo reports a new value for them.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Alice")
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=True,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob",
            displayname="Bob",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    session.refresh(alice)
    assert alice.displayname == "Alice"
    profile = session.get(GuestMazmoProfile, alice.id)
    assert profile is not None
    assert profile.mazmo_suspended is True


def test_sync_handles_guest_with_missing_optional_profile_fields(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync does not fail when Mazmo omits age/gender for one guest
    in the batch - that guest's profile row gets NULLs, not an error.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/111/default.jpg"),
            age=25,
            gender="female",
            pronoun="she/her",
            suspended=False,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob",
            displayname="Bob",
            avatar=None,
            age=None,
            gender=None,
            pronoun=None,
            suspended=False,
            banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    bob = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()
    profile = session.get(GuestMazmoProfile, bob.id)
    assert profile is not None
    assert profile.avatar_url is None
    assert profile.age is None
    assert profile.gender is None
    assert profile.pronoun is None
