"""Tests for the /events router and EventLog functionality.

These tests verify:
1. Event creation from ban/unban/checkin/undo-checkin actions
2. Event listing endpoints with proper authorization
3. Filtering by event type, timestamp, guest, meetup, actor
4. Pagination
"""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType
from tests.conftest import make_guest, make_meetup, make_rsvp, make_user

# ══════════════════════════════════════════════════════════════════════════════
# EVENT CREATION FROM ACTIONS
# ══════════════════════════════════════════════════════════════════════════════


class TestBanCreatesEvent:
    """Verify that banning a guest creates an EventLog entry."""

    def test_ban_creates_event_log_entry(self, client: TestClient, admin_headers: dict, session: Session, admin_user):
        """
        When an admin bans a guest, an EventLog entry should be created
        with the correct event_type, actor, guest, and reason.
        """
        guest = make_guest(session, mazmo_user_id=1, username="troublemaker")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Violated community guidelines"},
            headers=admin_headers,
        )

        # Check EventLog was created
        events = session.query(EventLog).all()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.BAN
        assert event.actor_id == admin_user.id
        assert event.guest_id == guest.mazmo_user_id
        assert event.reason == "Violated community guidelines"
        assert event.meetup_id is None  # Ban is not meetup-specific


class TestUnbanCreatesEvent:
    """Verify that unbanning a guest creates an EventLog entry."""

    def test_unban_creates_event_log_entry(self, client: TestClient, admin_headers: dict, session: Session, admin_user):
        """
        When an admin unbans a guest, an EventLog entry should be created.
        """
        guest = make_guest(session, mazmo_user_id=1, username="reformed")

        # First ban
        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Initial ban"},
            headers=admin_headers,
        )

        # Then unban
        client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)

        # Check EventLog has both entries
        events = session.exec(
            select(EventLog).order_by(EventLog.timestamp.asc())  # type: ignore[arg-type]
        ).all()
        assert len(events) == 2

        ban_event = events[0]
        assert ban_event.event_type == EventType.BAN

        unban_event = events[1]
        assert unban_event.event_type == EventType.UNBAN
        assert unban_event.actor_id == admin_user.id
        assert unban_event.guest_id == guest.mazmo_user_id
        assert unban_event.reason is None  # Unban doesn't require reason


class TestCheckinCreatesEvent:
    """Verify that checking in a guest creates an EventLog entry."""

    def test_checkin_creates_event_log_entry(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user
    ):
        """
        When staff checks in a guest, an EventLog entry should be created
        with the meetup_id recorded.
        """
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Check EventLog was created
        events = session.query(EventLog).all()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.CHECK_IN
        assert event.actor_id == staff_user.id
        assert event.guest_id == guest.mazmo_user_id
        assert event.meetup_id == meetup.id
        assert event.reason is None


class TestUndoCheckinCreatesEvent:
    """Verify that undoing a check-in creates an EventLog entry."""

    def test_undo_checkin_creates_event_log_entry(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user
    ):
        """
        When staff undoes a check-in, an EventLog entry should be created
        with the reason recorded.
        """
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="oops")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Check in first
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Then undo
        client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={"reason": "Wrong person checked in"},
            headers=staff_headers,
        )

        # Check EventLog has both entries
        events = session.exec(
            select(EventLog).order_by(EventLog.timestamp.asc())  # type: ignore[arg-type]
        ).all()
        assert len(events) == 2

        checkin_event = events[0]
        assert checkin_event.event_type == EventType.CHECK_IN

        undo_event = events[1]
        assert undo_event.event_type == EventType.UNDO_CHECK_IN
        assert undo_event.actor_id == staff_user.id
        assert undo_event.guest_id == guest.mazmo_user_id
        assert undo_event.meetup_id == meetup.id
        assert undo_event.reason == "Wrong person checked in"


# ══════════════════════════════════════════════════════════════════════════════
# UNDO CHECK-IN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════


class TestUndoCheckin:
    """Tests for the undo check-in endpoint."""

    def test_undo_checkin_returns_200_ok(self, client: TestClient, staff_headers: dict, session: Session):
        """Staff can undo a check-in."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Check in
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Undo
        resp = client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={"reason": "Mistake"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_undo_checkin_clears_arrival_fields(self, client: TestClient, staff_headers: dict, session: Session):
        """Undoing a check-in clears has_arrived, arrival_time, arrival_order."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        rsvp = make_rsvp(session, meetup=meetup, guest=guest)

        # Check in
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Verify checked in
        session.refresh(rsvp)
        assert rsvp.has_arrived is True

        # Undo
        client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={"reason": "Mistake"},
            headers=staff_headers,
        )

        # Verify cleared
        session.refresh(rsvp)
        assert rsvp.has_arrived is False
        assert rsvp.arrival_time is None
        assert rsvp.arrival_order is None
        assert rsvp.checked_in_by_id is None

    def test_undo_checkin_not_checked_in_returns_409_conflict(
        self, client: TestClient, staff_headers: dict, session: Session
    ):
        """Cannot undo a check-in if guest isn't checked in."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        resp = client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={"reason": "Testing"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "not currently checked in" in resp.json()["detail"].lower()

    def test_undo_checkin_not_rsvped_returns_404_not_found(
        self, client: TestClient, staff_headers: dict, session: Session
    ):
        """Cannot undo check-in for guest not RSVPed to meetup."""
        meetup = make_meetup(session)
        make_guest(session, mazmo_user_id=1, username="stranger")

        resp = client.patch(
            f"/meetups/{meetup.id}/guests/1/undo-checkin",
            json={"reason": "Testing"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_undo_checkin_requires_reason(self, client: TestClient, staff_headers: dict, session: Session):
        """Undo check-in requires a reason for the audit trail."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Check in
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Try to undo without reason
        resp = client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_undo_checkin_reason_too_short_returns_422(self, client: TestClient, staff_headers: dict, session: Session):
        """Undo reason must be at least 5 characters."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Check in
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Try with short reason
        resp = client.patch(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/undo-checkin",
            json={"reason": "abc"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ══════════════════════════════════════════════════════════════════════════════
# GET /events - ADMIN ONLY
# ══════════════════════════════════════════════════════════════════════════════


class TestListAllEvents:
    """Tests for GET /events (admin only)."""

    def test_list_all_events_as_admin_returns_200_ok(self, client: TestClient, admin_headers: dict, session: Session):
        """Admins can list all events."""
        # Create some events via actions
        guest = make_guest(session, mazmo_user_id=1, username="testguest")
        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        resp = client.get("/events/", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] >= 1
        assert "events" in data
        assert "limit" in data
        assert "offset" in data

    def test_list_all_events_as_staff_returns_403_forbidden(self, client: TestClient, staff_headers: dict):
        """Staff cannot access the global events list."""
        resp = client.get("/events/", headers=staff_headers)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_list_all_events_without_token_returns_401_unauthorized(self, client: TestClient):
        """Unauthenticated requests are rejected."""
        resp = client.get("/events/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_all_events_filter_by_type(self, client: TestClient, admin_headers: dict, session: Session):
        """Can filter events by type."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        # Create ban and unban events
        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )
        client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)

        # Filter to BAN only
        resp = client.get("/events/?type=BAN", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert all(e["event_type"] == "BAN" for e in data["events"])

    def test_list_all_events_filter_by_multiple_types(self, client: TestClient, admin_headers: dict, session: Session):
        """Can filter by multiple event types."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )
        client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)

        resp = client.get("/events/?type=BAN,UNBAN", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 2

    def test_list_all_events_invalid_type_returns_400(self, client: TestClient, admin_headers: dict):
        """Invalid event type returns 400."""
        resp = client.get("/events/?type=INVALID", headers=admin_headers)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid event type" in resp.json()["detail"].lower()

    def test_list_all_events_pagination(self, client: TestClient, admin_headers: dict, session: Session):
        """Pagination works correctly."""
        # Create multiple events
        for i in range(5):
            guest = make_guest(session, mazmo_user_id=i + 1, username=f"guest{i}")
            client.patch(
                f"/guests/{guest.mazmo_user_id}/ban",
                json={"reason": f"Ban {i}"},
                headers=admin_headers,
            )

        # Get first page
        resp = client.get("/events/?limit=2&offset=0", headers=admin_headers)
        data = resp.json()
        assert data["total"] == 5
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["events"]) == 2

        # Get second page
        resp = client.get("/events/?limit=2&offset=2", headers=admin_headers)
        data = resp.json()
        assert len(data["events"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# GET /events/meetups/{id} - MEETUP EVENTS
# ══════════════════════════════════════════════════════════════════════════════


class TestListMeetupEvents:
    """Tests for GET /events/meetups/{id}."""

    def test_list_meetup_events_as_staff_returns_200_ok(
        self, client: TestClient, staff_headers: dict, session: Session
    ):
        """Staff can view events at a meetup."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Check in
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        resp = client.get(f"/events/meetups/{meetup.id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["event_type"] == "CHECK_IN"

    def test_list_meetup_events_nonexistent_meetup_returns_404(self, client: TestClient, staff_headers: dict):
        """Nonexistent meetup returns 404."""
        import uuid

        fake_id = uuid.uuid4()
        resp = client.get(f"/events/meetups/{fake_id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_list_meetup_events_only_shows_meetup_events(
        self, client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
    ):
        """Only events for the specific meetup are returned."""
        meetup1 = make_meetup(session, name="Meetup 1", mazmo_meetup_url="https://mazmo.net/m1")
        meetup2 = make_meetup(session, name="Meetup 2", mazmo_meetup_url="https://mazmo.net/m2")

        guest1 = make_guest(session, mazmo_user_id=1, username="guest1")
        guest2 = make_guest(session, mazmo_user_id=2, username="guest2")

        make_rsvp(session, meetup=meetup1, guest=guest1)
        make_rsvp(session, meetup=meetup2, guest=guest2)

        # Check in at both meetups
        client.post(
            f"/meetups/{meetup1.id}/guests/{guest1.mazmo_user_id}/checkin",
            headers=staff_headers,
        )
        client.post(
            f"/meetups/{meetup2.id}/guests/{guest2.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Only meetup1 events
        resp = client.get(f"/events/meetups/{meetup1.id}", headers=staff_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["guest"]["username"] == "guest1"


# ══════════════════════════════════════════════════════════════════════════════
# GET /events/guests/{id} - GUEST EVENTS
# ══════════════════════════════════════════════════════════════════════════════


class TestListGuestEvents:
    """Tests for GET /events/guests/{id}."""

    def test_list_guest_events_staff_sees_only_ban_events(
        self, client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
    ):
        """
        Staff can only see BAN/UNBAN events for a guest.

        WHY: Staff need to know why someone is banned, but don't need
        full check-in history across all meetups.
        """
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="testguest")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Create check-in event (as staff)
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Create ban event (as admin)
        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        # Staff query - should only see ban
        resp = client.get(f"/events/guests/{guest.mazmo_user_id}", headers=staff_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["event_type"] == "BAN"

    def test_list_guest_events_admin_sees_all_events(
        self, client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
    ):
        """Admins can see all events for a guest."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="testguest")
        make_rsvp(session, meetup=meetup, guest=guest)

        # Create check-in and ban events
        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )
        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        # Admin query - should see both
        resp = client.get(f"/events/guests/{guest.mazmo_user_id}", headers=admin_headers)
        data = resp.json()
        assert data["total"] == 2
        event_types = {e["event_type"] for e in data["events"]}
        assert event_types == {"CHECK_IN", "BAN"}

    def test_list_guest_events_nonexistent_guest_returns_404(self, client: TestClient, staff_headers: dict):
        """Nonexistent guest returns 404."""
        resp = client.get("/events/guests/99999", headers=staff_headers)
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_list_guest_events_staff_filter_checkin_returns_empty(
        self, client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
    ):
        """Staff filtering for CHECK_IN events returns empty (not authorized)."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="testguest")
        make_rsvp(session, meetup=meetup, guest=guest)

        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Staff filtering for CHECK_IN - should return empty
        resp = client.get(f"/events/guests/{guest.mazmo_user_id}?type=CHECK_IN", headers=staff_headers)
        data = resp.json()
        assert data["total"] == 0
        assert data["events"] == []


# ══════════════════════════════════════════════════════════════════════════════
# GET /events/staff/{id} - STAFF EVENTS
# ══════════════════════════════════════════════════════════════════════════════


class TestListStaffEvents:
    """Tests for GET /events/staff/{id}."""

    def test_list_staff_events_own_events_returns_200_ok(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user
    ):
        """Staff can view their own events."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        resp = client.get(f"/events/staff/{staff_user.id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["actor"]["username"] == staff_user.username

    def test_list_staff_events_other_staff_returns_403_forbidden(
        self, client: TestClient, staff_headers: dict, session: Session
    ):
        """Staff cannot view other staff members' events."""
        other_staff = make_user(session, username="otherstaff")

        resp = client.get(f"/events/staff/{other_staff.id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "only view your own" in resp.json()["detail"].lower()

    def test_list_staff_events_admin_can_view_any_staff(
        self, client: TestClient, admin_headers: dict, staff_headers: dict, session: Session
    ):
        """Admins can view any staff member's events."""
        # Staff creates an event
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        # Get staff user ID from event
        resp = client.get("/events/?limit=1", headers=admin_headers)
        staff_id = resp.json()["events"][0]["actor"]["id"]

        # Admin views staff events
        resp = client.get(f"/events/staff/{staff_id}", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["total"] >= 1

    def test_list_staff_events_nonexistent_staff_returns_404(self, client: TestClient, admin_headers: dict):
        """Nonexistent staff member returns 404."""
        resp = client.get("/events/staff/99999", headers=admin_headers)
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ══════════════════════════════════════════════════════════════════════════════
# FILTERING
# ══════════════════════════════════════════════════════════════════════════════


class TestEventFiltering:
    """Tests for event filtering capabilities."""

    def test_filter_by_since(self, client: TestClient, admin_headers: dict, session: Session):
        """Can filter events by 'since' timestamp."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        # Filter to future (should return nothing)
        # Use Z suffix instead of +00:00 for cleaner URL encoding
        future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.get(f"/events/?since={future}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Filter to past (should return the event)
        past = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = client.get(f"/events/?since={past}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_filter_by_guest_id(self, client: TestClient, admin_headers: dict, session: Session):
        """Can filter events by guest_id."""
        guest1 = make_guest(session, mazmo_user_id=1, username="guest1")
        guest2 = make_guest(session, mazmo_user_id=2, username="guest2")

        client.patch(
            f"/guests/{guest1.mazmo_user_id}/ban",
            json={"reason": "Ban 1"},
            headers=admin_headers,
        )
        client.patch(
            f"/guests/{guest2.mazmo_user_id}/ban",
            json={"reason": "Ban 2"},
            headers=admin_headers,
        )

        resp = client.get(f"/events/?guest_id={guest1.mazmo_user_id}", headers=admin_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["guest"]["username"] == "guest1"

    def test_filter_by_actor_id(self, client: TestClient, admin_headers: dict, session: Session, admin_user):
        """Can filter events by actor_id."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        resp = client.get(f"/events/?actor_id={admin_user.id}", headers=admin_headers)
        data = resp.json()
        assert data["total"] >= 1
        assert all(e["actor"]["id"] == admin_user.id for e in data["events"])


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE FORMAT
# ══════════════════════════════════════════════════════════════════════════════


class TestEventResponseFormat:
    """Tests for correct response format."""

    def test_event_includes_actor_details(self, client: TestClient, admin_headers: dict, session: Session, admin_user):
        """Events include actor username and id."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        resp = client.get("/events/", headers=admin_headers)
        event = resp.json()["events"][0]
        assert event["actor"]["id"] == admin_user.id
        assert event["actor"]["username"] == admin_user.username

    def test_event_includes_guest_details(self, client: TestClient, admin_headers: dict, session: Session):
        """Events include guest username, displayname, and mazmo_user_id."""
        guest = make_guest(session, mazmo_user_id=123, username="guestuser", displayname="Guest User")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )

        resp = client.get("/events/", headers=admin_headers)
        event = resp.json()["events"][0]
        assert event["guest"]["mazmo_user_id"] == 123
        assert event["guest"]["username"] == "guestuser"
        assert event["guest"]["displayname"] == "Guest User"

    def test_event_includes_meetup_id_for_checkin(
        self, client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
    ):
        """Check-in events include the meetup_id."""
        meetup = make_meetup(session)
        guest = make_guest(session, mazmo_user_id=1, username="attendee")
        make_rsvp(session, meetup=meetup, guest=guest)

        client.post(
            f"/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin",
            headers=staff_headers,
        )

        resp = client.get("/events/", headers=admin_headers)
        event = resp.json()["events"][0]
        assert event["meetup_id"] == str(meetup.id)

    def test_event_includes_reason_when_present(self, client: TestClient, admin_headers: dict, session: Session):
        """Events include reason when one was provided."""
        guest = make_guest(session, mazmo_user_id=1, username="testguest")

        client.patch(
            f"/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Specific reason here"},
            headers=admin_headers,
        )

        resp = client.get("/events/", headers=admin_headers)
        event = resp.json()["events"][0]
        assert event["reason"] == "Specific reason here"

    def test_events_ordered_by_timestamp_descending(self, client: TestClient, admin_headers: dict, session: Session):
        """Events are returned newest first."""
        for i in range(3):
            guest = make_guest(session, mazmo_user_id=i + 1, username=f"guest{i}")
            client.patch(
                f"/guests/{guest.mazmo_user_id}/ban",
                json={"reason": f"Ban {i}"},
                headers=admin_headers,
            )

        resp = client.get("/events/", headers=admin_headers)
        events = resp.json()["events"]

        # Verify descending order by timestamp
        for i in range(len(events) - 1):
            assert events[i]["timestamp"] >= events[i + 1]["timestamp"]
