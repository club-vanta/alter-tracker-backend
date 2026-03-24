"""Tests for the /guests router.

These tests verify guest listing and ban management functionality.
Staff can view guests and banned list, but only admins can ban/unban.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

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
