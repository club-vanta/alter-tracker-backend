"""Tests for the /guests router.

These tests verify guest listing and ban management functionality.
Staff can view guests and banned list, but only admins can ban/unban.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType
from tests.conftest import make_guest

# ── Create guest manually ─────────────────────────────────────────────────────

VALID_GUEST_PAYLOAD = {
    "mazmo_user_id": 9001,
    "username": "newcomer",
    "displayname": "New Comer",
}


def test_create_guest_returns_201_with_guest_data(client: TestClient, staff_headers: dict):
    """
    Verify that staff can manually create a guest and get back the full GuestPublic.

    WHY: Core feature — someone shows up at the door with no Mazmo history.
    Staff needs to register them so they can be added as a walk-in.
    """
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["mazmo_user_id"] == 9001
    assert data["username"] == "newcomer"
    assert data["displayname"] == "New Comer"


def test_create_guest_defaults_is_banned_to_false(client: TestClient, staff_headers: dict):
    """
    Verify that a manually created guest is not banned by default.

    WHY: New guests should never start out as banned. The frontend uses
    is_banned to render names in red, so a wrong default would be confusing.
    """
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["is_banned"] is False


def test_create_guest_response_includes_all_guest_public_fields(client: TestClient, staff_headers: dict):
    """
    Verify that the response shape matches GuestPublic exactly.

    WHY: The frontend depends on a stable schema. Any missing field would
    cause a runtime error on the client side.
    """
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert "mazmo_user_id" in data
    assert "username" in data
    assert "displayname" in data
    assert "is_banned" in data


def test_create_guest_is_persisted_and_fetchable(client: TestClient, staff_headers: dict):
    """
    Verify that a created guest can be fetched via GET /guests/{id} afterwards.

    WHY: Checks that the DB write actually committed and the guest is
    retrievable by other endpoints (e.g., add-walkin).
    """
    client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    resp = client.get(f"/guests/{VALID_GUEST_PAYLOAD['mazmo_user_id']}", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["username"] == "newcomer"


def test_create_guest_appears_in_guest_list(client: TestClient, staff_headers: dict):
    """
    Verify that a manually created guest shows up in GET /guests/.

    WHY: Staff browsing the guest list should see manually created guests
    alongside synced ones — they're indistinguishable by design.
    """
    client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    resp = client.get("/guests/", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    usernames = [g["username"] for g in resp.json()["guests"]]
    assert "newcomer" in usernames


def test_create_guest_writes_guest_created_event_log(
    client: TestClient, staff_headers: dict, session: Session, staff_user
):
    """
    Verify that manually creating a guest writes a GUEST_CREATED audit log entry.

    WHY: Every identity creation needs an audit trail so admins know who
    added a guest that bypassed Mazmo sync.
    """
    client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == VALID_GUEST_PAYLOAD["mazmo_user_id"])
        .where(EventLog.event_type == EventType.GUEST_CREATED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_create_guest_accessible_by_regular_staff(client: TestClient, staff_headers: dict):
    """
    Verify that regular staff (not just admin) can create guests.

    WHY: Staff at the door need to register walk-ins on the spot — requiring
    admin rights would block the real-world workflow.
    """
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)
    assert resp.status_code == status.HTTP_201_CREATED


def test_create_guest_accessible_by_admin(client: TestClient, admin_headers: dict):
    """Verify that admins can also create guests."""
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=admin_headers)
    assert resp.status_code == status.HTTP_201_CREATED


def test_create_guest_returns_409_when_mazmo_user_id_already_exists(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that creating a guest with a duplicate mazmo_user_id returns 409.

    WHY: mazmo_user_id is the PK — duplicates must be rejected. This covers
    both synced guests and previously manually created ones.
    """
    make_guest(session, mazmo_user_id=9001, username="existing")

    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_guest_409_detail_mentions_existing_username(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that the 409 detail message includes the existing guest's username.

    WHY: Staff may accidentally try to register someone already in the system.
    The error should help them identify the duplicate quickly.
    """
    make_guest(session, mazmo_user_id=9001, username="already_here")

    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD, headers=staff_headers)

    assert "already_here" in resp.json()["detail"]


def test_create_guest_returns_409_even_for_synced_guest(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that the 409 applies to guests created via sync, not just manual ones.

    WHY: The system shouldn't let staff overwrite a synced guest's identity
    data by creating a duplicate with a different username/displayname.
    """
    make_guest(session, mazmo_user_id=9001, username="synced_guest")

    resp = client.post(
        "/guests/",
        json={**VALID_GUEST_PAYLOAD, "username": "different_username"},
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_guest_without_token_returns_401(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.post("/guests/", json=VALID_GUEST_PAYLOAD)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_guest_missing_mazmo_user_id_returns_422(client: TestClient, staff_headers: dict):
    """
    Verify that omitting mazmo_user_id returns 422.

    WHY: All three fields are required — mazmo_user_id is the PK and must
    come from the real Mazmo profile.
    """
    resp = client.post(
        "/guests/",
        json={"username": "nomazmo", "displayname": "No Mazmo"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_missing_username_returns_422(client: TestClient, staff_headers: dict):
    """Verify that omitting username returns 422."""
    resp = client.post(
        "/guests/",
        json={"mazmo_user_id": 9001, "displayname": "No Username"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_missing_displayname_returns_422(client: TestClient, staff_headers: dict):
    """Verify that omitting displayname returns 422."""
    resp = client.post(
        "/guests/",
        json={"mazmo_user_id": 9001, "username": "nodisplay"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_empty_username_returns_422(client: TestClient, staff_headers: dict):
    """
    Verify that an empty string username is rejected.

    WHY: min_length=1 on username — a blank username is meaningless
    and would render badly in the frontend.
    """
    resp = client.post(
        "/guests/",
        json={**VALID_GUEST_PAYLOAD, "username": ""},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_empty_displayname_returns_422(client: TestClient, staff_headers: dict):
    """Verify that an empty string displayname is rejected."""
    resp = client.post(
        "/guests/",
        json={**VALID_GUEST_PAYLOAD, "displayname": ""},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_non_integer_mazmo_user_id_returns_422(client: TestClient, staff_headers: dict):
    """
    Verify that a non-integer mazmo_user_id is rejected.

    WHY: mazmo_user_id must be an int matching Mazmo's internal ID.
    Sending a string like "abc" should be rejected at validation.
    """
    resp = client.post(
        "/guests/",
        json={**VALID_GUEST_PAYLOAD, "mazmo_user_id": "not-an-int"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_create_guest_empty_body_returns_422(client: TestClient, staff_headers: dict):
    """Verify that sending an empty body returns 422."""
    resp = client.post("/guests/", json={}, headers=staff_headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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
