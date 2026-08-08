"""Tests for the meetups router.

Covers: create, list, get, list guests, check-in, undo check-in, finalize/unfinalize.
Sync endpoint tests live in test_sync.py.

All routes are org-scoped: /organizations/{org_id}/meetups/...
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, MeetupRsvp, Organization, OrgRole, User
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_meetup, make_org_member, make_rsvp

# -- Shared fixture -----------------------------------------------------------


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    """
    Add the staff_user to the default org with OrgRole.STAFF.

    Request this fixture alongside staff_headers in any test where
    staff_headers needs to access org-scoped endpoints.
    """
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


# -- Create meetup ------------------------------------------------------------


def test_create_meetup_returns_201_with_meetup_data(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that a valid meetup creation request returns 201 with the created meetup.

    WHY: Happy path - admin provides a Mazmo URL, we fetch the date from Mazmo
    and persist the meetup.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Alter Cordoba #5", "mazmo_meetup_url": "https://mazmo.net/test/alter-5"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["name"] == "Alter Cordoba #5"
    assert data["mazmo_meetup_url"] == "https://mazmo.net/test/alter-5"
    assert "id" in data
    assert "date" in data


def test_create_meetup_accessible_by_org_staff(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that org members (not just site admins) can create meetups.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Test Meetup", "mazmo_meetup_url": "https://mazmo.net/test/meetup-999"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED


def test_create_meetup_persists_requires_payment_flag(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that requires_payment=true is persisted when creating a meetup.

    WHY: Paid events need this flag set at creation time so check-in can
    later enforce that guests paid before being let in.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={
            "name": "Alter Paid Event",
            "mazmo_meetup_url": "https://mazmo.net/test/alter-paid-1",
            "requires_payment": True,
        },
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["requires_payment"] is True


def test_create_meetup_defaults_requires_payment_to_false(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that requires_payment defaults to false when omitted.

    WHY: Most events are free; the field must not need to be set explicitly.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Alter Free Event", "mazmo_meetup_url": "https://mazmo.net/test/alter-free-1"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["requires_payment"] is False


def test_create_meetup_returns_409_for_duplicate_url(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that creating a meetup with a duplicate Mazmo URL returns 409.

    WHY: Each Mazmo event should only be tracked once. A duplicate URL
    means someone already created this meetup.
    """
    make_meetup(session, org=org, mazmo_meetup_url="https://mazmo.net/test/duplicate-123")

    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Duplicate", "mazmo_meetup_url": "https://mazmo.net/test/duplicate-123"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_meetup_returns_504_when_mazmo_unreachable(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that a Mazmo network error returns 504.

    WHY: If we can't reach Mazmo to validate and fetch the event date,
    we can't create the meetup. 504 signals "upstream unreachable".
    """
    mock_mazmo_for_meetups.fetch_meetup_date.side_effect = MazmoNetworkError("timeout")

    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/unreachable-1"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_create_meetup_returns_502_when_mazmo_returns_error(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that Mazmo API errors return 502.

    WHY: If Mazmo returns a 4xx/5xx (bad URL, deleted event), we return 502
    to indicate "upstream returned an error".
    """
    mock_mazmo_for_meetups.fetch_meetup_date.side_effect = MazmoAPIError("404")

    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/bad-url-1"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


def test_create_meetup_requires_auth(
    client: TestClient,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that meetup creation requires authentication.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/no-auth-1"},
    )

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_meetup_accepts_community_with_plus_prefix(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that URLs with a '+' prefix in the community segment are accepted.

    WHY: Some Mazmo communities use '+' as a prefix (e.g. +eventos-reuniones-argentina).
    The old regex only allowed [\\w-] and rejected these URLs with a 422.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={
            "name": "Alter Tan Selmo Secret Face",
            "mazmo_meetup_url": "https://mazmo.net/+eventos-reuniones-argentina/alter-tal-selmo-secret-face-opgnjcy4d0u",
        },
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED


def test_create_meetup_accepts_alphanumeric_slug_suffix(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that thread slugs ending in an alphanumeric ID (not just numeric) are accepted.

    WHY: Newer Mazmo events use alphanumeric IDs like 'opgnjcy4d0u' instead of
    plain integers. The old regex required \\d+ at the end and rejected these.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={
            "name": "Test Alphanumeric ID",
            "mazmo_meetup_url": "https://mazmo.net/some-community/some-event-abc123xyz",
        },
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED


def test_create_meetup_rejects_url_without_thread_segment(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """
    Verify that URLs missing the thread path segment are still rejected.

    WHY: A URL like https://mazmo.net/community (no thread) would be a community
    page, not a meetup -- it should never pass validation.
    """
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Bad URL", "mazmo_meetup_url": "https://mazmo.net/just-community"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_meetup_rejects_non_mazmo_url(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
    mock_mazmo_for_meetups: AsyncMock,
):
    """Verify that URLs from other domains are rejected."""
    resp = client.post(
        f"/organizations/{org.id}/meetups/",
        json={"name": "Bad URL", "mazmo_meetup_url": "https://example.com/community/event-123"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# -- List meetups -------------------------------------------------------------


def test_list_meetups_returns_all_meetups(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that list returns all meetups in the org with total count.
    """
    make_meetup(session, org=org, name="Meetup A", mazmo_meetup_url="https://mazmo.net/test/a-1")
    make_meetup(session, org=org, name="Meetup B", mazmo_meetup_url="https://mazmo.net/test/b-2")

    resp = client.get(f"/organizations/{org.id}/meetups/", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    names = {m["name"] for m in data["meetups"]}
    assert names == {"Meetup A", "Meetup B"}


def test_list_meetups_returns_empty_when_none_exist(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
):
    """
    Verify that listing meetups when none exist returns empty list.
    """
    resp = client.get(f"/organizations/{org.id}/meetups/", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 0
    assert data["meetups"] == []


def test_list_meetups_requires_auth(client: TestClient, org: Organization):
    resp = client.get(f"/organizations/{org.id}/meetups/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Get single meetup --------------------------------------------------------


def test_get_meetup_returns_meetup_by_id(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a meetup can be retrieved by its UUID.
    """
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["id"] == str(meetup.id)
    assert data["name"] == meetup.name


def test_get_meetup_returns_404_for_unknown_id(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
):
    """
    Verify that requesting a non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.get(f"/organizations/{org.id}/meetups/{fake_id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_get_meetup_returns_404_for_meetup_from_different_org(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
    meetup,
):
    """
    Verify that requesting a meetup using the wrong org_id in the URL returns 404.

    WHY: A meetup belongs to exactly one org. Using a valid meetup ID under the
    wrong org should not leak data across org boundaries -- the caller gets 404
    as if the meetup doesn't exist in that org.
    """
    from tests.conftest import make_org

    other_org = make_org(session, name="Other Org", slug="other-org")

    # meetup belongs to `org`, but we request it under `other_org`
    resp = client.get(
        f"/organizations/{other_org.id}/meetups/{meetup.id}",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- List meetup guests -------------------------------------------------------


def test_list_meetup_guests_returns_all_rsvps(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that listing meetup guests returns all RSVPed guests.
    """
    alice = make_guest(session, mazmo_user_id=101, mazmo_handle="alice")
    bob = make_guest(session, mazmo_user_id=102, mazmo_handle="bob")
    make_rsvp(session, meetup=meetup, guest=alice)
    make_rsvp(session, meetup=meetup, guest=bob)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    handles = {g["guest"]["mazmo_handle"] for g in data["guests"]}
    assert handles == {"alice", "bob"}


def test_list_meetup_guests_includes_rsvp_and_checkin_state(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that the guest list includes RSVP and check-in state fields.

    WHY: The door staff need to know both who RSVPed and who has already
    arrived, so we include both sets of data.
    """
    alice = make_guest(session, mazmo_user_id=201, mazmo_handle="alice_rsvp")
    make_rsvp(session, meetup=meetup, guest=alice, has_arrived=True, arrival_order=1)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    guest_entry = resp.json()["guests"][0]
    assert guest_entry["rsvp"]["has_arrived"] is True
    assert guest_entry["rsvp"]["arrival_order"] == 1


def test_list_meetup_guests_includes_is_banned_field(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that each guest entry in the meetup guest list includes is_banned.

    WHY: GuestWithBanPublic exposes is_banned so the door UI can show a
    warning before a banned guest is checked in. This field must always
    be present in the response even when False.
    """
    guest = make_guest(session, mazmo_user_id=250, mazmo_handle="unbanned_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    guest_entry = resp.json()["guests"][0]
    assert "is_banned" in guest_entry["guest"]
    assert guest_entry["guest"]["is_banned"] is False


def test_list_meetup_guests_returns_404_for_unknown_meetup(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
):
    """
    Verify that listing guests for a non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.get(
        f"/organizations/{org.id}/meetups/{fake_id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_list_meetup_guests_returns_empty_when_no_rsvps(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a meetup with no RSVPs returns empty list.
    """
    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 0
    assert data["guests"] == []


# -- Check in -----------------------------------------------------------------


def test_checkin_marks_guest_as_arrived(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that check-in marks the guest as arrived and returns their data.

    WHY: Core functionality - door staff needs to mark guests as arrived
    and get confirmation with arrival order.
    """
    guest = make_guest(session, mazmo_user_id=301, mazmo_handle="checkin_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["guest"]["mazmo_handle"] == "checkin_guest"
    assert data["arrival_order"] is not None


def test_checkin_writes_event_log_entry(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    staff_user: User,
    org_staff_member,
):
    """
    Verify that check-in creates an audit log entry.

    WHY: We need an audit trail of who checked in each guest and when.
    """
    guest = make_guest(session, mazmo_user_id=302, mazmo_handle="audit_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    event = session.exec(select(EventLog).where(EventLog.guest_id == guest.id)).first()
    assert event is not None
    assert event.event_type == EventType.CHECK_IN
    assert event.actor_id == staff_user.id
    assert event.meetup_id == meetup.id


def test_checkin_returns_404_when_guest_not_rsvped(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that checking in a non-RSVPed guest returns 404.

    WHY: Staff might try to check in a guest who hasn't RSVPed on Mazmo,
    or who needs to be synced first. Clear 404 prompts them to sync.
    """
    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{uuid.uuid4()}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_checkin_returns_409_when_already_checked_in(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that double check-in returns 409.

    WHY: Prevents accidentally checking in the same guest twice.
    The error message includes undo instructions.
    """
    guest = make_guest(session, mazmo_user_id=303, mazmo_handle="double_checkin")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_returns_404_for_unknown_meetup(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
):
    """
    Verify that check-in on non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.post(
        f"/organizations/{org.id}/meetups/{fake_id}/guests/{uuid.uuid4()}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Check-in concurrency guarantees ------------------------------------------
#
# True concurrent requests can't be tested with the synchronous TestClient
# (tests share a single rolled-back transaction). Instead, these tests verify
# the observable guarantees that SELECT FOR UPDATE provides:
#
#   1. A second check-in on an already-checked-in guest returns 409 -- which is
#      exactly what the second concurrent request sees after the first commits.
#   2. Only one CHECK_IN event log entry is ever created for a given guest+meetup,
#      regardless of how many attempts were made.
#
# The SELECT FOR UPDATE comment in meetups.py explains the mechanism.


def test_checkin_second_attempt_after_first_succeeds_returns_409(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a second check-in attempt after the first succeeded returns 409.

    WHY: This is the state the second concurrent request observes after SELECT
    FOR UPDATE releases the lock -- has_arrived is True, so it gets 409.
    The first check-in won; the second must not silently succeed or 500.
    """
    guest = make_guest(session, mazmo_user_id=350, mazmo_handle="concurrent_checkin")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp1 = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp1.status_code == status.HTTP_200_OK

    resp2 = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp2.status_code == status.HTTP_409_CONFLICT


def test_checkin_produces_exactly_one_event_log_entry_on_duplicate_attempt(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    staff_user: User,
    org_staff_member,
):
    """
    Verify that only one CHECK_IN event log entry exists after two attempts.

    WHY: The audit trail must reflect reality -- only the staff member who
    actually performed the first check-in should appear in the log.
    A second concurrent request must not inject its own event log entry.
    """
    guest = make_guest(session, mazmo_user_id=351, mazmo_handle="one_event_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.CHECK_IN)
    ).all()
    assert len(events) == 1
    assert events[0].actor_id == staff_user.id


def test_checkin_event_log_records_first_staff_not_second(
    client: TestClient,
    session: Session,
    meetup,
    staff_user: User,
    admin_user: User,
    staff_headers: dict,
    admin_headers: dict,
    org_staff_member,
):
    """
    Verify that the event log records the staff member who checked in first,
    not a subsequent attempt by a different staff member.

    WHY: In a real race, two different staff members at two terminals could
    both scan the same guest. Only the first one should appear in the audit log.
    """
    guest = make_guest(session, mazmo_user_id=352, mazmo_handle="race_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    # Staff checks in first
    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    # Admin tries to check in the same guest (arrives second)
    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=admin_headers,
    )

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.CHECK_IN)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id  # first, not admin


# -- Undo check-in ------------------------------------------------------------


def test_undo_checkin_clears_arrival_data(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that undo check-in clears all arrival fields.

    WHY: Staff might check in the wrong person. Undo must fully clear
    all arrival fields so the guest can be checked in again correctly.
    """
    guest = make_guest(session, mazmo_user_id=401, mazmo_handle="undo_guest")
    rsvp = make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/undo-checkin",
        json={"reason": "Checked in by mistake"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.has_arrived is False
    assert rsvp.arrival_order is None
    assert rsvp.arrival_time is None
    assert rsvp.checked_in_by_id is None


def test_undo_checkin_writes_event_log_with_reason(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    staff_user: User,
    org_staff_member,
):
    """
    Verify that undo check-in creates an audit log entry with the reason.

    WHY: We need to know why a check-in was undone for accountability.
    """
    guest = make_guest(session, mazmo_user_id=402, mazmo_handle="undo_audit")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/undo-checkin",
        json={"reason": "Wrong person scanned"},
        headers=staff_headers,
    )

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.UNDO_CHECK_IN)
    ).first()
    assert event is not None
    assert event.reason == "Wrong person scanned"
    assert event.actor_id == staff_user.id


def test_undo_checkin_returns_404_when_guest_not_rsvped(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that undo on a non-RSVPed guest returns 404.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{uuid.uuid4()}/undo-checkin",
        json={"reason": "Test reason"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_undo_checkin_returns_409_when_guest_not_checked_in(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that undo on a guest who hasn't checked in returns 409.

    WHY: Can't undo something that didn't happen. Clear error prevents confusion.
    """
    guest = make_guest(session, mazmo_user_id=403, mazmo_handle="not_arrived")
    make_rsvp(session, meetup=meetup, guest=guest, has_arrived=False)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/undo-checkin",
        json={"reason": "Oops!"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_undo_checkin_rejects_reason_that_is_too_short(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that undo check-in requires a meaningful reason (min 5 chars).

    WHY: "ok" or "x" are not useful audit entries. We enforce a minimum
    length to ensure reasons are actually informative.
    """
    guest = make_guest(session, mazmo_user_id=404, mazmo_handle="short_reason")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/undo-checkin",
        json={"reason": "no"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_undo_checkin_returns_404_for_unknown_meetup(
    client: TestClient,
    staff_headers: dict,
    org: Organization,
    org_staff_member,
):
    """
    Verify that undo on non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{fake_id}/guests/{uuid.uuid4()}/undo-checkin",
        json={"reason": "Test reason"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Finalize / un-finalize ---------------------------------------------------


def test_finalize_meetup_sets_is_finalized_and_finalized_at(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that finalizing a meetup sets is_finalized=True and records finalized_at.

    WHY: Core requirement - once an event ends we lock it to prevent
    accidental check-ins or syncs from corrupting the historical record.
    Uses admin_headers because finalize requires org admin (SITE_ADMIN or ADMIN role).
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/finalize",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_finalized"] is True
    assert data["finalized_at"] is not None

    session.refresh(meetup)
    assert meetup.is_finalized is True
    assert meetup.finalized_at is not None


def test_finalize_meetup_writes_event_log_entry(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
    admin_user: User,
):
    """
    Verify that finalizing a meetup creates an audit log entry.

    WHY: We need to know who finalized a meetup and when, in case it was done
    in error and needs to be reversed.
    """
    client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/finalize",
        headers=admin_headers,
    )

    event = session.exec(
        select(EventLog).where(EventLog.meetup_id == meetup.id).where(EventLog.event_type == EventType.MEETUP_FINALIZED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id


def test_finalize_meetup_returns_403_for_org_staff_without_admin_role(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that an org member with STAFF role cannot finalize a meetup.

    WHY: finalize/unfinalize are admin-only operations (SITE_ADMIN or org ADMIN).
    A STAFF-role member should be rejected with 403 to prevent accidental lockouts.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/finalize",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_finalize_meetup_returns_409_when_already_finalized(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
):
    """
    Verify that finalizing an already-finalized meetup returns 409.

    WHY: Idempotency guard - calling finalize twice should surface an error
    rather than silently succeeding, so staff know the state.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Already Final",
        mazmo_meetup_url="https://mazmo.net/test/already-final-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    session.flush()

    resp = client.patch(
        f"/organizations/{org.id}/meetups/{finalized.id}/finalize",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_finalize_meetup_returns_404_for_unknown_meetup(
    client: TestClient,
    admin_headers: dict,
    org: Organization,
):
    """
    Verify that finalizing a non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{fake_id}/finalize",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_checkin_returns_409_when_meetup_is_finalized(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that check-in is blocked on a finalized meetup.

    WHY: The whole point of finalization is to freeze the meetup state.
    No new arrivals should be recorded after the event is over.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Closed Meetup",
        mazmo_meetup_url="https://mazmo.net/test/closed-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    guest = make_guest(session, mazmo_user_id=501, mazmo_handle="blocked_guest")
    make_rsvp(session, meetup=finalized, guest=guest)
    session.flush()

    resp = client.post(
        f"/organizations/{org.id}/meetups/{finalized.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "finalized" in resp.json()["detail"].lower()


def test_sync_returns_409_when_meetup_is_finalized(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
    mock_mazmo: AsyncMock,
):
    """
    Verify that sync is blocked on a finalized meetup.

    WHY: Syncing a finalized meetup could overwrite the final attendance
    record with stale or incorrect data from Mazmo.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Synced Closed",
        mazmo_meetup_url="https://mazmo.net/test/sync-closed-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    session.flush()

    resp = client.post(
        f"/organizations/{org.id}/meetups/{finalized.id}/sync",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "finalized" in resp.json()["detail"].lower()


def test_unfinalize_meetup_clears_finalization(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
):
    """
    Verify that un-finalizing a meetup restores it to active state.

    WHY: Accidents happen. Staff need a way to reverse accidental finalization
    so check-ins and syncs can resume.
    Uses admin_headers because unfinalize requires org admin.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Undo Final",
        mazmo_meetup_url="https://mazmo.net/test/undo-final-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    session.flush()

    resp = client.patch(
        f"/organizations/{org.id}/meetups/{finalized.id}/unfinalize",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_finalized"] is False
    assert data["finalized_at"] is None

    session.refresh(finalized)
    assert finalized.is_finalized is False
    assert finalized.finalized_at is None


def test_unfinalize_meetup_returns_409_when_not_finalized(
    client: TestClient,
    admin_headers: dict,
    meetup,
):
    """
    Verify that un-finalizing a non-finalized meetup returns 409.

    WHY: Prevents confusion - staff should know whether a meetup
    is actually finalized before attempting to reverse it.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/unfinalize",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_works_after_unfinalize(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that check-in works again after un-finalizing a meetup.

    WHY: Un-finalize should fully restore all operations, not just
    change a flag.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Restored Meetup",
        mazmo_meetup_url="https://mazmo.net/test/restored-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    guest = make_guest(session, mazmo_user_id=502, mazmo_handle="restored_guest")
    make_rsvp(session, meetup=finalized, guest=guest)
    session.flush()

    client.patch(
        f"/organizations/{org.id}/meetups/{finalized.id}/unfinalize",
        headers=admin_headers,
    )

    resp = client.post(
        f"/organizations/{org.id}/meetups/{finalized.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK


def test_unfinalize_meetup_writes_event_log_entry(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
    admin_user: User,
):
    """
    Verify that un-finalizing a meetup creates an audit log entry.

    WHY: We need a full trail of finalize/unfinalize actions so admins
    can audit who changed the meetup state and when.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Log Unfinalize",
        mazmo_meetup_url="https://mazmo.net/test/log-unfinalize-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    session.flush()

    client.patch(
        f"/organizations/{org.id}/meetups/{finalized.id}/unfinalize",
        headers=admin_headers,
    )

    event = session.exec(
        select(EventLog)
        .where(EventLog.meetup_id == finalized.id)
        .where(EventLog.event_type == EventType.MEETUP_UNFINALIZED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id


# -- Add walk-in guest --------------------------------------------------------


def test_add_walkin_returns_201_with_is_walkin_true(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that adding a walk-in guest returns 201 with is_walkin=True.

    WHY: Core requirement -- staff needs to add guests who have a Mazmo profile
    but didn't RSVP. The is_walkin flag must be set so the frontend can show
    the badge.
    """
    guest = make_guest(session, mazmo_user_id=601, mazmo_handle="walkin_guest")

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["guest"]["mazmo_handle"] == "walkin_guest"
    assert data["rsvp"]["is_walkin"] is True


def test_add_walkin_writes_event_log_entry(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    staff_user: User,
    org_staff_member,
):
    """
    Verify that adding a walk-in creates a WALKIN audit log entry.

    WHY: We need a full audit trail of who added each walk-in and when.
    """
    guest = make_guest(session, mazmo_user_id=602, mazmo_handle="walkin_audit")

    client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest.id).where(EventLog.event_type == EventType.WALKIN)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id
    assert event.meetup_id == meetup.id


def test_add_walkin_returns_404_when_guest_not_in_system(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that adding a walk-in for an unknown Mazmo user returns 404.

    WHY: Walk-ins must have a Mazmo profile already synced. If the guest_id
    doesn't exist at all, the request is invalid.
    """
    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{uuid.uuid4()}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_add_walkin_returns_409_when_rsvp_already_exists(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that adding a walk-in for a guest who already has an RSVP returns 409.

    WHY: Prevents duplicate RSVPs -- the guest may have RSVPed on Mazmo or
    already been added as a walk-in.
    """
    guest = make_guest(session, mazmo_user_id=603, mazmo_handle="already_rsvped")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_add_walkin_returns_409_when_meetup_is_finalized(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that adding a walk-in to a finalized meetup returns 409.

    WHY: Finalization locks the meetup -- no new arrivals (including walk-ins)
    should be added after the event ends.
    """
    finalized = make_meetup(
        session,
        org=org,
        name="Finalized Walkin",
        mazmo_meetup_url="https://mazmo.net/test/finalized-walkin-1",
    )
    finalized.is_finalized = True
    finalized.finalized_at = datetime.now(UTC)
    session.add(finalized)
    guest = make_guest(session, mazmo_user_id=604, mazmo_handle="walkin_blocked")
    session.flush()

    resp = client.post(
        f"/organizations/{org.id}/meetups/{finalized.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "finalized" in resp.json()["detail"].lower()


# -- guest_id path param validation --------------------------------------------


def test_checkin_rejects_malformed_guest_id(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a non-UUID guest_id path segment returns 422.

    WHY: guest_id is now a UUID path param (Guest.id), not the old Postgres
    INTEGER mazmo_user_id. FastAPI/Pydantic reject malformed UUIDs at the API
    boundary before the request ever reaches the database.
    """
    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/123123123123/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_undo_checkin_rejects_malformed_guest_id(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a non-UUID guest_id path segment returns 422.

    WHY: Same reason as test_checkin_rejects_malformed_guest_id -- all
    endpoints that accept guest_id as a path parameter require a valid UUID.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/123123123123/undo-checkin",
        headers=staff_headers,
        json={"reason": "test reason here"},
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_add_walkin_rejects_malformed_guest_id(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a non-UUID guest_id path segment returns 422.

    WHY: Same reason as test_checkin_rejects_malformed_guest_id -- all
    endpoints that accept guest_id as a path parameter require a valid UUID.
    """
    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/123123123123/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# -- Payment tracking (paid events) --------------------------------------------
#
# requires_payment is set on the meetup at creation time. When true, check-in
# is hard-blocked (409) until an org admin marks the guest's RSVP as paid via
# PATCH .../payment. Only org admins can mark/undo payment (get_org_admin),
# unlike check-in itself which any org member can do.


@pytest.fixture()
def paid_meetup(session: Session, org: Organization):
    """A meetup that requires payment before check-in."""
    return make_meetup(
        session,
        org=org,
        name="Alter Paid Event",
        mazmo_meetup_url="https://mazmo.net/test/alter-paid-fixture",
        requires_payment=True,
    )


def test_mark_payment_sets_has_paid_and_writes_event_log(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
    admin_user: User,
):
    """
    Verify that marking payment sets has_paid/paid_at/paid_by and logs it.

    WHY: The organizer needs a record of who confirmed each guest's payment
    and when, since the actual money changes hands outside the system.
    """
    guest = make_guest(session, mazmo_user_id=401, mazmo_handle="payer")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["guest"]["mazmo_handle"] == "payer"
    assert data["paid_at"] is not None
    assert data["paid_by"]["username"] == "admin"

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.PAYMENT_RECORDED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id
    assert event.meetup_id == paid_meetup.id


def test_mark_payment_returns_409_when_meetup_does_not_require_payment(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that marking payment on a free event returns 409.

    WHY: Payment tracking is opt-in per meetup; marking it on a free event
    would be a no-op that could confuse the audit trail.
    """
    guest = make_guest(session, mazmo_user_id=402, mazmo_handle="free_event_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_mark_payment_returns_409_when_already_paid(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
):
    """
    Verify that marking an already-paid guest returns 409.

    WHY: Prevents accidentally double-recording a payment.
    """
    guest = make_guest(session, mazmo_user_id=403, mazmo_handle="already_paid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_mark_payment_returns_404_when_guest_not_rsvped(
    client: TestClient,
    admin_headers: dict,
    paid_meetup,
):
    """
    Verify that marking payment for a non-RSVPed guest returns 404.
    """
    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{uuid.uuid4()}/payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_mark_payment_returns_403_for_org_staff_without_admin_role(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot mark payment.

    WHY: Payment confirmation is admin-only, same permission level as
    finalize/bans -- staff can check guests in but not confirm payment.
    """
    guest = make_guest(session, mazmo_user_id=404, mazmo_handle="staff_blocked")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_mark_payment_returns_409_when_meetup_finalized(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
):
    """
    Verify that marking payment on a finalized meetup returns 409.

    WHY: A finalized meetup is frozen -- same rule as check-in, sync, and
    walk-ins, which are also blocked once the event is closed out.
    """
    guest = make_guest(session, mazmo_user_id=405, mazmo_handle="finalized_event_guest")
    make_rsvp(session, meetup=paid_meetup, guest=guest)
    paid_meetup.is_finalized = True
    paid_meetup.finalized_at = datetime.now(UTC)
    session.add(paid_meetup)
    session.flush()

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


# -- Undo payment ---------------------------------------------------------


def test_undo_payment_clears_has_paid_and_writes_event_log_with_reason(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
    admin_user: User,
):
    """
    Verify that undoing payment clears has_paid/paid_at/paid_by_id and logs
    the reason.

    WHY: Same pattern as undo-checkin -- reverting a payment mark is a
    sensitive action that needs a reason for the audit trail.
    """
    guest = make_guest(session, mazmo_user_id=406, mazmo_handle="undo_payment_guest")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment/undo",
        headers=admin_headers,
        json={"reason": "Marked the wrong guest as paid"},
    )

    assert resp.status_code == status.HTTP_200_OK

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == paid_meetup.id)
        .where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.has_paid is False
    assert rsvp.paid_at is None
    assert rsvp.paid_by_id is None

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.PAYMENT_REVOKED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id
    assert event.reason == "Marked the wrong guest as paid"


def test_undo_payment_returns_409_when_not_paid(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
):
    """
    Verify that undoing payment for a guest who hasn't paid returns 409.
    """
    guest = make_guest(session, mazmo_user_id=407, mazmo_handle="never_paid")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment/undo",
        headers=admin_headers,
        json={"reason": "Trying to undo a non-existent payment"},
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_undo_payment_rejects_reason_that_is_too_short(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
):
    """
    Verify that a too-short reason is rejected with 422.

    WHY: Same 5-500 char validation as undo-checkin, enforced at the
    schema level via Field(min_length=5, max_length=500).
    """
    guest = make_guest(session, mazmo_user_id=408, mazmo_handle="short_reason_guest")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment/undo",
        headers=admin_headers,
        json={"reason": "bad"},
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_undo_payment_returns_403_for_org_staff_without_admin_role(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot undo a payment mark.
    """
    guest = make_guest(session, mazmo_user_id=409, mazmo_handle="staff_undo_blocked")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/payment/undo",
        headers=staff_headers,
        json={"reason": "Testing staff cannot undo payment"},
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


# -- Check-in enforcement of payment requirement -------------------------------


def test_checkin_returns_409_when_meetup_requires_payment_and_guest_unpaid(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that check-in is hard-blocked when the meetup requires payment
    and the guest hasn't paid.

    WHY: This is the core requirement of the feature -- a guest cannot
    physically enter a paid event without having paid first.
    """
    guest = make_guest(session, mazmo_user_id=410, mazmo_handle="unpaid_at_door")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_succeeds_when_meetup_requires_payment_and_guest_paid(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that check-in succeeds once the guest is marked as paid.
    """
    guest = make_guest(session, mazmo_user_id=411, mazmo_handle="paid_at_door")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_ignores_payment_requirement_when_meetup_does_not_require_it(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that check-in on a free event never checks has_paid.

    WHY: Regression guard -- the payment enforcement must not leak into
    the existing free-event check-in flow that every other test relies on.
    """
    guest = make_guest(session, mazmo_user_id=412, mazmo_handle="free_event_checkin")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


# -- Toggling requires_payment after creation ----------------------------------


def test_enable_payment_sets_requires_payment_and_writes_event_log(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
    admin_user: User,
):
    """
    Verify that enabling payment on a free meetup sets requires_payment=true.

    WHY: Organizers may decide an event becomes paid after it was already
    created as free (e.g. a bigger venue with a cover charge got booked).
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/enable-payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["requires_payment"] is True

    session.refresh(meetup)
    assert meetup.requires_payment is True

    event = session.exec(
        select(EventLog)
        .where(EventLog.meetup_id == meetup.id)
        .where(EventLog.event_type == EventType.PAYMENT_REQUIREMENT_ENABLED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id


def test_enable_payment_returns_409_when_already_required(
    client: TestClient,
    admin_headers: dict,
    paid_meetup,
):
    """
    Verify that enabling payment on an already-paid meetup returns 409.
    """
    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/enable-payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_enable_payment_returns_409_when_meetup_finalized(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that enabling payment on a finalized meetup returns 409.

    WHY: A finalized meetup is frozen, same rule as marking payment itself.
    """
    meetup.is_finalized = True
    meetup.finalized_at = datetime.now(UTC)
    session.add(meetup)
    session.flush()

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/enable-payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_enable_payment_returns_403_for_org_staff_without_admin_role(
    client: TestClient,
    staff_headers: dict,
    meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot enable payment.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/enable-payment",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_disable_payment_clears_requires_payment_and_writes_event_log(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
    admin_user: User,
):
    """
    Verify that disabling payment sets requires_payment=false.

    WHY: Organizers may decide to waive the entrance fee after creating
    the event as paid.
    """
    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/disable-payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["requires_payment"] is False

    session.refresh(paid_meetup)
    assert paid_meetup.requires_payment is False

    event = session.exec(
        select(EventLog)
        .where(EventLog.meetup_id == paid_meetup.id)
        .where(EventLog.event_type == EventType.PAYMENT_REQUIREMENT_DISABLED)
    ).first()
    assert event is not None
    assert event.actor_id == admin_user.id


def test_disable_payment_returns_409_when_not_required(
    client: TestClient,
    admin_headers: dict,
    meetup,
):
    """
    Verify that disabling payment on an already-free meetup returns 409.
    """
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/disable-payment",
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_disable_payment_returns_403_for_org_staff_without_admin_role(
    client: TestClient,
    staff_headers: dict,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot disable payment.
    """
    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/disable-payment",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_enable_payment_does_not_retroactively_block_already_checked_in_guest(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that switching a meetup to paid does not undo an existing check-in.

    WHY: Core design guarantee discussed with the user -- toggling
    requires_payment only affects check-ins from that point forward. A
    guest who already entered for free must not be retroactively affected.
    """
    guest = make_guest(session, mazmo_user_id=413, mazmo_handle="already_in_before_toggle")
    make_rsvp(session, meetup=meetup, guest=guest)

    checkin_resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert checkin_resp.status_code == status.HTTP_200_OK

    enable_resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/enable-payment",
        headers=admin_headers,
    )
    assert enable_resp.status_code == status.HTTP_200_OK

    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.has_arrived is True


def test_new_checkin_blocked_after_enabling_payment_on_previously_free_meetup(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a guest who hasn't checked in yet is blocked once payment
    is enabled, even though the meetup started out free.
    """
    guest = make_guest(session, mazmo_user_id=414, mazmo_handle="not_yet_in_before_toggle")
    make_rsvp(session, meetup=meetup, guest=guest)

    enable_resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/enable-payment",
        headers=admin_headers,
    )
    assert enable_resp.status_code == status.HTTP_200_OK

    checkin_resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert checkin_resp.status_code == status.HTTP_409_CONFLICT


def test_disable_payment_preserves_has_paid_data(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    paid_meetup,
):
    """
    Verify that disabling payment does not clear has_paid on existing RSVPs.

    WHY: If payment is re-enabled later, previously recorded payments
    should still count -- has_paid data is inert, not deleted, when the
    requirement is switched off.
    """
    guest = make_guest(session, mazmo_user_id=415, mazmo_handle="paid_before_disable")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.patch(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/disable-payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == paid_meetup.id)
        .where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.has_paid is True
