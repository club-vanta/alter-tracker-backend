"""Tests for the meetups router.

Covers: create, list, get, list guests, check-in, undo check-in.
Sync endpoint tests live in test_sync.py.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_meetup, make_rsvp

# ── Create meetup ─────────────────────────────────────────────────────────────


def test_create_meetup_returns_201_with_meetup_data(
    client: TestClient, admin_headers: dict, mock_mazmo_for_meetups: AsyncMock
):
    """
    Verify that a valid meetup creation request returns 201 with the created meetup.

    WHY: Happy path - staff provides a Mazmo URL, we fetch the date from Mazmo
    and persist the meetup.
    """
    resp = client.post(
        "/meetups/",
        json={"name": "Alter Córdoba #5", "mazmo_meetup_url": "https://mazmo.net/test/alter-5"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["name"] == "Alter Córdoba #5"
    assert data["mazmo_meetup_url"] == "https://mazmo.net/test/alter-5"
    assert "id" in data
    assert "date" in data


def test_create_meetup_accessible_by_regular_staff(
    client: TestClient, staff_headers: dict, mock_mazmo_for_meetups: AsyncMock
):
    """
    Verify that regular staff (not just admins) can create meetups.
    """
    resp = client.post(
        "/meetups/",
        json={"name": "Test Meetup", "mazmo_meetup_url": "https://mazmo.net/test/meetup-999"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED


def test_create_meetup_returns_409_for_duplicate_url(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo_for_meetups: AsyncMock
):
    """
    Verify that creating a meetup with a duplicate Mazmo URL returns 409.

    WHY: Each Mazmo event should only be tracked once. A duplicate URL
    means someone already created this meetup.
    """
    make_meetup(session, mazmo_meetup_url="https://mazmo.net/test/duplicate-123")

    resp = client.post(
        "/meetups/",
        json={"name": "Duplicate", "mazmo_meetup_url": "https://mazmo.net/test/duplicate-123"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_meetup_returns_504_when_mazmo_unreachable(
    client: TestClient, admin_headers: dict, mock_mazmo_for_meetups: AsyncMock
):
    """
    Verify that a Mazmo network error returns 504.

    WHY: If we can't reach Mazmo to validate and fetch the event date,
    we can't create the meetup. 504 signals "upstream unreachable".
    """
    mock_mazmo_for_meetups.fetch_meetup_date.side_effect = MazmoNetworkError("timeout")

    resp = client.post(
        "/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/unreachable-1"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_create_meetup_returns_502_when_mazmo_returns_error(
    client: TestClient, admin_headers: dict, mock_mazmo_for_meetups: AsyncMock
):
    """
    Verify that Mazmo API errors return 502.

    WHY: If Mazmo returns a 4xx/5xx (bad URL, deleted event), we return 502
    to indicate "upstream returned an error".
    """
    mock_mazmo_for_meetups.fetch_meetup_date.side_effect = MazmoAPIError("404")

    resp = client.post(
        "/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/bad-url-1"},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


def test_create_meetup_requires_auth(client: TestClient, mock_mazmo_for_meetups: AsyncMock):
    """
    Verify that meetup creation requires authentication.
    """
    resp = client.post(
        "/meetups/",
        json={"name": "Test", "mazmo_meetup_url": "https://mazmo.net/test/no-auth-1"},
    )

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── List meetups ──────────────────────────────────────────────────────────────


def test_list_meetups_returns_all_meetups(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that list returns all meetups with total count.
    """
    make_meetup(session, name="Meetup A", mazmo_meetup_url="https://mazmo.net/test/a-1")
    make_meetup(session, name="Meetup B", mazmo_meetup_url="https://mazmo.net/test/b-2")

    resp = client.get("/meetups/", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    names = {m["name"] for m in data["meetups"]}
    assert names == {"Meetup A", "Meetup B"}


def test_list_meetups_returns_empty_when_none_exist(client: TestClient, staff_headers: dict):
    """
    Verify that listing meetups when none exist returns empty list.
    """
    resp = client.get("/meetups/", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 0
    assert data["meetups"] == []


def test_list_meetups_requires_auth(client: TestClient):
    resp = client.get("/meetups/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Get single meetup ─────────────────────────────────────────────────────────


def test_get_meetup_returns_meetup_by_id(client: TestClient, staff_headers: dict, meetup):
    """
    Verify that a meetup can be retrieved by its UUID.
    """
    resp = client.get(f"/meetups/{meetup.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["id"] == str(meetup.id)
    assert data["name"] == meetup.name


def test_get_meetup_returns_404_for_unknown_id(client: TestClient, staff_headers: dict):
    """
    Verify that requesting a non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.get(f"/meetups/{fake_id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# ── List meetup guests ────────────────────────────────────────────────────────


def test_list_meetup_guests_returns_all_rsvps(client: TestClient, staff_headers: dict, session: Session, meetup):
    """
    Verify that listing meetup guests returns all RSVPed guests.
    """
    alice = make_guest(session, mazmo_user_id=101, username="alice")
    bob = make_guest(session, mazmo_user_id=102, username="bob")
    make_rsvp(session, meetup=meetup, guest=alice)
    make_rsvp(session, meetup=meetup, guest=bob)

    resp = client.get(f"/meetups/{meetup.id}/guests", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    usernames = {g["guest"]["username"] for g in data["guests"]}
    assert usernames == {"alice", "bob"}


def test_list_meetup_guests_includes_rsvp_and_checkin_state(
    client: TestClient, staff_headers: dict, session: Session, meetup
):
    """
    Verify that the guest list includes RSVP and check-in state fields.

    WHY: The door staff need to know both who RSVPed and who has already
    arrived, so we include both sets of data.
    """
    alice = make_guest(session, mazmo_user_id=201, username="alice_rsvp")
    make_rsvp(session, meetup=meetup, guest=alice, has_arrived=True, arrival_order=1)

    resp = client.get(f"/meetups/{meetup.id}/guests", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    guest_entry = resp.json()["guests"][0]
    assert guest_entry["rsvp"]["has_arrived"] is True
    assert guest_entry["rsvp"]["arrival_order"] == 1


def test_list_meetup_guests_returns_404_for_unknown_meetup(client: TestClient, staff_headers: dict):
    """
    Verify that listing guests for a non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.get(f"/meetups/{fake_id}/guests", headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_list_meetup_guests_returns_empty_when_no_rsvps(client: TestClient, staff_headers: dict, meetup):
    """
    Verify that a meetup with no RSVPs returns empty list.
    """
    resp = client.get(f"/meetups/{meetup.id}/guests", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 0
    assert data["guests"] == []


# ── Check in ─────────────────────────────────────────────────────────────────


def test_checkin_marks_guest_as_arrived(client: TestClient, staff_headers: dict, session: Session, meetup):
    """
    Verify that check-in marks the guest as arrived and returns their data.

    WHY: Core functionality - door staff needs to mark guests as arrived
    and get confirmation with arrival order.
    """
    guest = make_guest(session, mazmo_user_id=301, username="checkin_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["guest"]["username"] == "checkin_guest"
    assert data["arrival_order"] is not None


def test_checkin_writes_event_log_entry(client: TestClient, staff_headers: dict, session: Session, meetup, staff_user):
    """
    Verify that check-in creates an audit log entry.

    WHY: We need an audit trail of who checked in each guest and when.
    """
    guest = make_guest(session, mazmo_user_id=302, username="audit_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    client.post(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
        headers=staff_headers,
    )

    event = session.exec(select(EventLog).where(EventLog.guest_id == guest.mazmo_user_id)).first()
    assert event is not None
    assert event.event_type == EventType.CHECK_IN
    assert event.actor_id == staff_user.id
    assert event.meetup_id == meetup.id


def test_checkin_returns_404_when_guest_not_rsvped(client: TestClient, staff_headers: dict, meetup):
    """
    Verify that checking in a non-RSVPed guest returns 404.

    WHY: Staff might try to check in a guest who hasn't RSVPed on Mazmo,
    or who needs to be synced first. Clear 404 prompts them to sync.
    """
    resp = client.post(
        f"/meetups/{meetup.id}/guests/99999/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_checkin_returns_409_when_already_checked_in(client: TestClient, staff_headers: dict, session: Session, meetup):
    """
    Verify that double check-in returns 409.

    WHY: Prevents accidentally checking in the same guest twice.
    The error message includes undo instructions.
    """
    guest = make_guest(session, mazmo_user_id=303, username="double_checkin")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.post(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_returns_404_for_unknown_meetup(client: TestClient, staff_headers: dict):
    """
    Verify that check-in on non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.post(f"/meetups/{fake_id}/guests/123/checkin", headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# ── Undo check-in ─────────────────────────────────────────────────────────────


def test_undo_checkin_clears_arrival_data(client: TestClient, staff_headers: dict, session: Session, meetup):
    """
    Verify that undo check-in clears all arrival fields.

    WHY: Staff might check in the wrong person. Undo must fully clear
    all arrival fields so the guest can be checked in again correctly.
    """
    guest = make_guest(session, mazmo_user_id=401, username="undo_guest")
    rsvp = make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.patch(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
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
    client: TestClient, staff_headers: dict, session: Session, meetup, staff_user
):
    """
    Verify that undo check-in creates an audit log entry with the reason.

    WHY: We need to know why a check-in was undone for accountability.
    """
    guest = make_guest(session, mazmo_user_id=402, username="undo_audit")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    client.patch(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
        json={"reason": "Wrong person scanned"},
        headers=staff_headers,
    )

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.mazmo_user_id)
        .where(EventLog.event_type == EventType.UNDO_CHECK_IN)
    ).first()
    assert event is not None
    assert event.reason == "Wrong person scanned"
    assert event.actor_id == staff_user.id


def test_undo_checkin_returns_404_when_guest_not_rsvped(client: TestClient, staff_headers: dict, meetup):
    """
    Verify that undo on a non-RSVPed guest returns 404.
    """
    resp = client.patch(
        f"/meetups/{meetup.id}/guests/99999/undo-checkin",
        json={"reason": "Test reason"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_undo_checkin_returns_409_when_guest_not_checked_in(
    client: TestClient, staff_headers: dict, session: Session, meetup
):
    """
    Verify that undo on a guest who hasn't checked in returns 409.

    WHY: Can't undo something that didn't happen. Clear error prevents confusion.
    """
    guest = make_guest(session, mazmo_user_id=403, username="not_arrived")
    make_rsvp(session, meetup=meetup, guest=guest, has_arrived=False)

    resp = client.patch(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
        json={"reason": "Oops!"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_undo_checkin_rejects_reason_that_is_too_short(
    client: TestClient, staff_headers: dict, session: Session, meetup
):
    """
    Verify that undo check-in requires a meaningful reason (min 5 chars).

    WHY: "ok" or "x" are not useful audit entries. We enforce a minimum
    length to ensure reasons are actually informative.
    """
    guest = make_guest(session, mazmo_user_id=404, username="short_reason")
    make_rsvp(
        session,
        meetup=meetup,
        guest=guest,
        has_arrived=True,
        arrival_order=1,
        arrival_time=datetime.now(UTC),
    )

    resp = client.patch(
        f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
        json={"reason": "no"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_undo_checkin_returns_404_for_unknown_meetup(client: TestClient, staff_headers: dict):
    """
    Verify that undo on non-existent meetup returns 404.
    """
    fake_id = uuid.uuid4()
    resp = client.patch(
        f"/meetups/{fake_id}/guests/123/undo-checkin",
        json={"reason": "Test reason"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND
