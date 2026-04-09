"""Tests for the /guests router.

These tests verify guest listing and ban management functionality.
Staff can view guests and banned list, but only admins can ban/unban.
"""

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest

# ── List guests ───────────────────────────────────────────────────────────────


def test_list_guests_returns_200_ok(client: TestClient, staff_headers: dict, session: Session):
    """Verify that staff can list all guests."""
    make_guest(session, mazmo_user_id=1, username="alice")
    make_guest(session, mazmo_user_id=2, username="bob")
    resp = client.get("/guests/", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    usernames = [g["username"] for g in data["guests"]]
    assert "alice" in usernames
    assert "bob" in usernames


def test_list_guests_without_token_returns_401_unauthorized(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Get single guest ──────────────────────────────────────────────────────────


def test_get_guest_returns_200_ok(client: TestClient, staff_headers: dict, session: Session):
    """Verify that staff can get a single guest."""
    guest = make_guest(session, mazmo_user_id=123, username="testguest")
    resp = client.get(f"/guests/{guest.mazmo_user_id}", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["username"] == "testguest"


def test_get_nonexistent_guest_returns_404_not_found(client: TestClient, staff_headers: dict):
    """Verify that getting a nonexistent guest returns 404."""
    resp = client.get("/guests/99999", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# ── Get guest by username ─────────────────────────────────────────────────────


def test_get_guest_by_username_returns_200_with_guest_data(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that staff can look up a guest by their Mazmo username.

    WHY: Staff at the door may know the handle but not the numeric ID.
    This endpoint avoids having to list all guests and search manually.
    """
    make_guest(session, mazmo_user_id=39119, username="cindydark")
    resp = client.get("/guests/by-username/cindydark", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["username"] == "cindydark"
    assert data["mazmo_user_id"] == 39119


def test_get_guest_by_username_returns_all_guest_public_fields(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify the response shape matches GuestPublic."""
    make_guest(session, mazmo_user_id=1, username="someuser")
    resp = client.get("/guests/by-username/someuser", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "mazmo_user_id" in data
    assert "username" in data
    assert "displayname" in data
    assert "is_banned" in data


def test_get_guest_by_username_returns_404_when_not_in_system(client: TestClient, staff_headers: dict):
    """
    Verify that a 404 is returned when the username doesn't exist in our system.

    WHY: The guest may exist on Mazmo but not have RSVPed to any tracked meetup.
    The error should tell staff to use POST /guests/ to register them.
    """
    resp = client.get("/guests/by-username/nobody", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "nobody" in resp.json()["detail"]


def test_get_guest_by_username_404_detail_mentions_post_guests(client: TestClient, staff_headers: dict):
    """
    Verify the 404 message points staff to POST /guests/ as the next step.

    WHY: Staff need actionable guidance — if they can't find a guest by username
    it's likely they need to register them first.
    """
    resp = client.get("/guests/by-username/nobody", headers=staff_headers)
    assert "POST /guests/" in resp.json()["detail"]


def test_get_guest_by_username_without_token_returns_401(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/by-username/someuser")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Guest public includes is_banned field ─────────────────────────────────────


def test_guest_public_includes_is_banned_field(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that GuestPublic schema includes is_banned field.

    WHY: Frontend needs this to render banned guests differently (e.g., red name).
    """
    guest = make_guest(session, mazmo_user_id=100, username="testguest")
    resp = client.get(f"/guests/{guest.mazmo_user_id}", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "is_banned" in data
    assert data["is_banned"] is False


# ── Ban guest ─────────────────────────────────────────────────────────────────


def test_ban_guest_as_admin_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that admins can ban guests.

    WHY: Core admin workflow - admins need to ban problematic guests
    to prevent them from attending events.
    """
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Violated community guidelines"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_banned"] is True
    assert data["banned_reason"] == "Violated community guidelines"


def test_ban_guest_stores_audit_trail(client: TestClient, admin_headers: dict, session: Session, admin_user):
    """
    Verify that banning a guest stores the audit trail.

    WHY: We need to know who banned a guest and when for accountability.
    """
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Violated community guidelines"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["banned_at"] is not None
    assert data["banned_by_id"] == admin_user.id
    assert data["banned_reason"] == "Violated community guidelines"


def test_ban_guest_as_staff_returns_403_forbidden(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that regular staff cannot ban guests.

    WHY: Banning is an admin-only action. Staff shouldn't be able to
    ban guests without proper authorization.
    """
    guest = make_guest(session, mazmo_user_id=1, username="innocent")
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing unauthorized"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_ban_already_banned_guest_returns_409_conflict(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that banning an already-banned guest returns 409.

    WHY: Idempotency check - if the guest is already banned, return
    a conflict rather than silently succeeding.
    """
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    # First ban
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "First offense"},
        headers=admin_headers,
    )
    # Try to ban again
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Second offense"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "already banned" in resp.json()["detail"].lower()


def test_ban_nonexistent_guest_returns_404_not_found(client: TestClient, admin_headers: dict):
    """
    Verify that banning a nonexistent guest returns 404.

    WHY: Clear error handling - if the guest ID doesn't exist,
    return 404 not a confusing error.
    """
    resp = client.patch(
        "/guests/99999/ban",
        json={"reason": "Testing nonexistent"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_ban_requires_reason(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that banning a guest requires a reason.

    WHY: Audit trail requirement - we need to know why someone was banned
    for accountability and potential unbanning decisions.
    """
    guest = make_guest(session, mazmo_user_id=1, username="needsreason")
    # Missing reason field
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_ban_reason_too_short_returns_422_unprocessable_entity(
    client: TestClient, admin_headers: dict, session: Session
):
    """
    Verify that ban reason must be at least 5 characters.

    WHY: Enforce meaningful reasons - "ok" is not a useful audit trail.
    """
    guest = make_guest(session, mazmo_user_id=1, username="shortreason")
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "abc"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_ban_guest_without_token_returns_401_unauthorized(client: TestClient, session: Session):
    """Verify that unauthenticated ban attempts are rejected."""
    guest = make_guest(session, mazmo_user_id=1, username="notoken")
    resp = client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing unauthorized"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Unban guest ───────────────────────────────────────────────────────────────


def test_unban_guest_as_admin_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that admins can unban guests.

    WHY: Reversible action - if someone was banned by mistake or their
    ban period is over, admins can restore their access.
    """
    guest = make_guest(session, mazmo_user_id=1, username="reformed")
    # First ban
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Temporary ban"},
        headers=admin_headers,
    )
    # Then unban
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_banned"] is False


def test_unban_clears_ban_fields(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that unbanning clears all ban-related fields.

    WHY: Clean slate - once unbanned, the guest shouldn't have any
    lingering ban data.
    """
    guest = make_guest(session, mazmo_user_id=1, username="cleared")
    # Ban then unban
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Temporary"},
        headers=admin_headers,
    )
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    # Verify ban fields are cleared by fetching the guest
    get_resp = client.get(f"/guests/{guest.mazmo_user_id}", headers=admin_headers)
    data = get_resp.json()
    assert data["is_banned"] is False


def test_unban_not_banned_guest_returns_409_conflict(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that unbanning an unbanned guest returns 409.

    WHY: Idempotency check - if the guest is not banned, return
    a conflict rather than silently succeeding.
    """
    guest = make_guest(session, mazmo_user_id=1, username="notbanned")
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=admin_headers)
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "not currently banned" in resp.json()["detail"].lower()


def test_unban_guest_as_staff_returns_403_forbidden(
    client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
):
    """
    Verify that regular staff cannot unban guests.

    WHY: Only admins can unban guests - staff shouldn't be able to
    restore banned guests without proper authorization.
    """
    guest = make_guest(session, mazmo_user_id=1, username="staffcantunban")
    # Admin bans
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Staff tries to unban
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_unban_nonexistent_guest_returns_404_not_found(client: TestClient, admin_headers: dict):
    """Verify that unbanning a nonexistent guest returns 404."""
    resp = client.patch("/guests/99999/unban", headers=admin_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_unban_guest_without_token_returns_401_unauthorized(client: TestClient, admin_headers: dict, session: Session):
    """Verify that unauthenticated unban attempts are rejected."""
    guest = make_guest(session, mazmo_user_id=1, username="unbannotoken")
    # Admin bans
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Unauthenticated tries to unban
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── List banned guests ────────────────────────────────────────────────────────


def test_list_banned_guests_as_staff_returns_200_ok(
    client: TestClient, staff_headers: dict, admin_headers: dict, session: Session
):
    """
    Verify that staff can view the banned guests list.

    WHY: Staff need to see who's banned so they can identify banned
    guests at the door and refuse entry.
    """
    # Create guests and ban one
    guest1 = make_guest(session, mazmo_user_id=1, username="banned_one")
    make_guest(session, mazmo_user_id=2, username="not_banned")

    client.patch(
        f"/guests/{guest1.mazmo_user_id}/ban",
        json={"reason": "Banned for testing"},
        headers=admin_headers,
    )

    resp = client.get("/guests/banned", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 1
    assert data["guests"][0]["username"] == "banned_one"
    assert data["guests"][0]["is_banned"] is True


def test_list_banned_guests_empty_returns_empty_list(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that /banned returns empty list when no guests are banned.

    WHY: Edge case - the endpoint should return an empty list, not error,
    when there are no banned guests.
    """
    # Create a guest but don't ban them
    make_guest(session, mazmo_user_id=1, username="innocent")

    resp = client.get("/guests/banned", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 0
    assert data["guests"] == []


def test_list_banned_guests_includes_ban_details(
    client: TestClient, staff_headers: dict, admin_headers: dict, session: Session, admin_user
):
    """
    Verify that the banned list includes full ban details.

    WHY: Staff need to know why someone was banned and when to make
    informed decisions at the door.
    """
    guest = make_guest(session, mazmo_user_id=1, username="banned_details")
    client.patch(
        f"/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Aggressive behavior"},
        headers=admin_headers,
    )

    resp = client.get("/guests/banned", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 1
    banned_guest = data["guests"][0]
    assert banned_guest["banned_reason"] == "Aggressive behavior"
    assert banned_guest["banned_at"] is not None
    assert banned_guest["banned_by_id"] == admin_user.id


def test_list_banned_guests_without_token_returns_401_unauthorized(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/banned")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Create guest ──────────────────────────────────────────────────────────────


def test_create_guest_by_username_returns_201_with_mazmo_profile_data(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that the endpoint looks up Mazmo and returns the profile data.

    WHY: The whole point is that staff don't need to know the numeric ID —
    the endpoint fetches it from Mazmo and registers the guest automatically.
    """
    resp = client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["mazmo_user_id"] == 39119
    assert data["username"] == "cindydark"
    assert data["displayname"] == "⚜️Lissandra⚜️"
    assert data["is_banned"] is False


def test_create_guest_by_username_persists_correct_mazmo_user_id(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """
    Verify that the mazmo_user_id from the Mazmo response is stored, not the username.

    WHY: The guest PK is the numeric ID. If we stored the wrong ID, add-walkin
    and other operations would break.
    """
    client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    resp = client.get("/guests/39119", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["username"] == "cindydark"


def test_create_guest_by_username_writes_guest_created_event_log(
    client: TestClient, staff_headers: dict, session: Session, staff_user, mock_mazmo_for_guests
):
    """
    Verify that a GUEST_CREATED audit log entry is written with the correct actor.

    WHY: Audit trail - admins need to know who registered this guest and
    that the registration came from a Mazmo username lookup.
    """
    client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == 39119).where(EventLog.event_type == EventType.GUEST_CREATED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_create_guest_by_username_returns_404_when_mazmo_says_user_not_found(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that a 404 from Mazmo surfaces as a 404 to the caller.

    WHY: If staff typo the username, they should get a clear "not found"
    error, not a confusing 500 or 502.
    """
    fake_request = httpx.Request("GET", "https://prod.mazmoapi.net/users/nobody")
    fake_response = httpx.Response(404, request=fake_request)
    mock_mazmo_for_guests.fetch_user_by_username.side_effect = MazmoAPIError(
        "Mazmo returned 404 for username 'nobody'"
    ).__class__("Mazmo returned 404 for username 'nobody'")
    # Use __cause__ so the router can inspect it
    exc = MazmoAPIError("Mazmo returned 404")
    exc.__cause__ = httpx.HTTPStatusError("404", request=fake_request, response=fake_response)
    mock_mazmo_for_guests.fetch_user_by_username.side_effect = exc

    resp = client.post("/guests/", json={"username": "nobody"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "nobody" in resp.json()["detail"]


def test_create_guest_by_username_returns_409_when_guest_already_exists(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """
    Verify that creating a duplicate returns 409.

    WHY: If Mazmo says the user ID is 39119 and we already have that ID,
    we must reject rather than silently overwrite.
    """
    make_guest(session, mazmo_user_id=39119, username="cindydark")

    resp = client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_guest_by_username_returns_504_when_mazmo_unreachable(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that a network failure surfaces as 504.

    WHY: If Mazmo is down, staff should get a clear "try again" response,
    not a 500 that would suggest a bug.
    """
    mock_mazmo_for_guests.fetch_user_by_username.side_effect = MazmoNetworkError("Connection timed out")

    resp = client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_create_guest_by_username_returns_401_without_token(client: TestClient, mock_mazmo_for_guests):
    """Verify that unauthenticated requests are rejected."""
    resp = client.post("/guests/", json={"username": "cindydark"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_guest_by_username_returns_422_when_username_is_empty(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that an empty username string is rejected before hitting Mazmo.

    WHY: min_length=1 on username — the request shouldn't even reach the
    Mazmo API if the username is blank.
    """
    resp = client.post("/guests/", json={"username": ""}, headers=staff_headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
