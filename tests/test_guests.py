"""Tests for the /guests router and org-scoped ban endpoints.

Guest identity endpoints (/guests/*) are global and unchanged.
Ban management is now org-scoped: /organizations/{org_id}/guests/{id}/ban|unban
and /organizations/{org_id}/guests/banned.
"""

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, OrgRole
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_org, make_org_member

# -- List guests ---------------------------------------------------------------


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


# -- Get single guest ----------------------------------------------------------


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


# -- Get guest by username -----------------------------------------------------


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
    """Verify the response shape matches GuestPublic (identity only, no is_banned)."""
    make_guest(session, mazmo_user_id=1, username="someuser")
    resp = client.get("/guests/by-username/someuser", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "mazmo_user_id" in data
    assert "username" in data
    assert "displayname" in data
    # is_banned is not part of GuestPublic (it is org-scoped now)
    assert "is_banned" not in data


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

    WHY: Staff need actionable guidance -- if they can't find a guest by username
    it's likely they need to register them first.
    """
    resp = client.get("/guests/by-username/nobody", headers=staff_headers)
    assert "POST /guests/" in resp.json()["detail"]


def test_get_guest_by_username_without_token_returns_401(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/by-username/someuser")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Ban guest (org-scoped) ----------------------------------------------------


def test_ban_guest_as_site_admin_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that a SITE_ADMIN can ban guests within an org.

    WHY: SITE_ADMIN bypasses org admin checks, so they can manage bans
    across all orgs without needing an explicit org membership.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Violated community guidelines"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["banned_reason"] == "Violated community guidelines"
    assert data["banned_at"] is not None


def test_ban_guest_stores_audit_trail(client: TestClient, admin_headers: dict, session: Session, admin_user):
    """
    Verify that banning a guest stores the audit trail.

    WHY: We need to know who banned a guest and when for accountability.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Violated community guidelines"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["banned_at"] is not None
    assert data["banned_by_id"] == admin_user.id
    assert data["banned_reason"] == "Violated community guidelines"


def test_ban_guest_as_staff_member_returns_403_forbidden(
    client: TestClient, staff_headers: dict, staff_user, session: Session
):
    """
    Verify that an org STAFF member cannot ban guests.

    WHY: Banning requires org ADMIN or SITE_ADMIN. Staff members at the door
    should not have the ability to ban guests.
    """
    org = make_org(session)
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
    guest = make_guest(session, mazmo_user_id=1, username="innocent")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing unauthorized"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_ban_guest_as_non_member_returns_403_forbidden(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that a user with no org membership cannot ban guests.

    WHY: Only org admins (or SITE_ADMIN) can ban guests within an org.
    A plain USER with no org role should be rejected.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="innocent")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing unauthorized"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_ban_already_banned_guest_returns_409_conflict(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that banning an already-banned guest returns 409.

    WHY: Idempotency check - if the guest is already banned in this org,
    return a conflict rather than silently succeeding.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="troublemaker")
    # First ban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "First offense"},
        headers=admin_headers,
    )
    # Try to ban again
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Second offense"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "already banned" in resp.json()["detail"].lower()


def test_ban_nonexistent_guest_returns_404_not_found(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that banning a nonexistent guest returns 404.

    WHY: Clear error handling - if the guest ID doesn't exist in our system,
    return 404 rather than a confusing error.
    """
    org = make_org(session)
    resp = client.patch(
        f"/organizations/{org.id}/guests/99999/ban",
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
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="needsreason")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
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
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="shortreason")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "abc"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_ban_guest_without_token_returns_401_unauthorized(client: TestClient, session: Session):
    """Verify that unauthenticated ban attempts are rejected."""
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="notoken")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing unauthorized"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Unban guest (org-scoped) --------------------------------------------------


def test_unban_guest_as_site_admin_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that a SITE_ADMIN can unban guests within an org.

    WHY: Reversible action - if someone was banned by mistake or their
    ban period is over, admins can restore their access.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="reformed")
    # First ban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Temporary ban"},
        headers=admin_headers,
    )
    # Then unban
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/unban",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    # Unban returns GuestPublic (identity only, no is_banned field)
    assert data["mazmo_user_id"] == guest.mazmo_user_id
    assert data["username"] == "reformed"
    assert "is_banned" not in data


def test_unban_clears_ban_record(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that unbanning removes the ban record from the org.

    WHY: Clean slate - once unbanned, the guest should no longer appear
    in the org's banned list.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="cleared")
    # Ban then unban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Temporary"},
        headers=admin_headers,
    )
    unban_resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/unban",
        headers=admin_headers,
    )
    assert unban_resp.status_code == status.HTTP_200_OK

    # Verify the guest no longer appears in the banned list
    banned_resp = client.get(f"/organizations/{org.id}/guests/banned", headers=admin_headers)
    assert banned_resp.status_code == status.HTTP_200_OK
    assert banned_resp.json()["total"] == 0


def test_unban_not_banned_guest_returns_409_conflict(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that unbanning a guest who is not banned returns 409.

    WHY: Idempotency check - if the guest is not banned in this org,
    return a conflict rather than silently succeeding.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="notbanned")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/unban",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "not currently banned" in resp.json()["detail"].lower()


def test_unban_guest_as_staff_member_returns_403_forbidden(
    client: TestClient, staff_headers: dict, staff_user, admin_headers: dict, session: Session
):
    """
    Verify that an org STAFF member cannot unban guests.

    WHY: Only org admins (or SITE_ADMIN) can unban guests. Staff shouldn't be
    able to restore banned guests without proper authorization.
    """
    org = make_org(session)
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
    guest = make_guest(session, mazmo_user_id=1, username="staffcantunban")
    # Admin bans
    client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Org STAFF tries to unban
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/unban",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_unban_nonexistent_guest_returns_404_not_found(client: TestClient, admin_headers: dict, session: Session):
    """Verify that unbanning a nonexistent guest returns 404."""
    org = make_org(session)
    resp = client.patch(
        f"/organizations/{org.id}/guests/99999/unban",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_unban_guest_without_token_returns_401_unauthorized(client: TestClient, admin_headers: dict, session: Session):
    """Verify that unauthenticated unban attempts are rejected."""
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, username="unbannotoken")
    # Admin bans
    client.patch(
        f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Unauthenticated tries to unban
    resp = client.patch(f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/unban")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- List banned guests (org-scoped) -------------------------------------------


class TestListBannedGuests:
    """Tests for GET /organizations/{org_id}/guests/banned."""

    def test_returns_200_with_banned_guests(
        self,
        client: TestClient,
        staff_headers: dict,
        admin_headers: dict,
        session: Session,
        staff_user,
    ):
        """
        Verify that staff can view the banned guests list for an org.

        WHY: Staff need to see who's banned so they can identify banned
        guests at the door and refuse entry.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
        guest1 = make_guest(session, mazmo_user_id=1, username="banned_one")
        make_guest(session, mazmo_user_id=2, username="not_banned")

        client.patch(
            f"/organizations/{org.id}/guests/{guest1.mazmo_user_id}/ban",
            json={"reason": "Banned for testing"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org.id}/guests/banned", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert data["guests"][0]["username"] == "banned_one"

    def test_returns_empty_list_when_no_bans(
        self,
        client: TestClient,
        staff_headers: dict,
        session: Session,
        staff_user,
    ):
        """
        Verify that the endpoint returns an empty list when no guests are banned.

        WHY: Edge case - the endpoint should return an empty list, not error,
        when there are no banned guests in this org.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
        make_guest(session, mazmo_user_id=1, username="innocent")

        resp = client.get(f"/organizations/{org.id}/guests/banned", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 0
        assert data["guests"] == []

    def test_includes_ban_details_in_response(
        self,
        client: TestClient,
        staff_headers: dict,
        admin_headers: dict,
        session: Session,
        admin_user,
        staff_user,
    ):
        """
        Verify that the banned list includes full ban details.

        WHY: Staff need to know why someone was banned and when to make
        informed decisions at the door.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
        guest = make_guest(session, mazmo_user_id=1, username="banned_details")
        client.patch(
            f"/organizations/{org.id}/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Aggressive behavior"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org.id}/guests/banned", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        banned_guest = data["guests"][0]
        assert banned_guest["mazmo_user_id"] == guest.mazmo_user_id
        assert banned_guest["username"] == "banned_details"
        assert banned_guest["banned_reason"] == "Aggressive behavior"
        assert banned_guest["banned_at"] is not None
        assert banned_guest["banned_by_id"] == admin_user.id

    def test_is_scoped_to_org(
        self,
        client: TestClient,
        admin_headers: dict,
        session: Session,
    ):
        """
        Verify that bans in one org do not appear in another org's banned list.

        WHY: Bans are per-org. A guest banned in org A should still be
        allowed at events run by org B.
        """
        org_a = make_org(session, name="Org A", slug="org-a")
        org_b = make_org(session, name="Org B", slug="org-b")
        guest = make_guest(session, mazmo_user_id=1, username="banned_in_a")

        client.patch(
            f"/organizations/{org_a.id}/guests/{guest.mazmo_user_id}/ban",
            json={"reason": "Banned in A only"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org_b.id}/guests/banned", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["total"] == 0

    def test_without_token_returns_401_unauthorized(self, client: TestClient, session: Session):
        """Verify that unauthenticated requests are rejected."""
        org = make_org(session)
        resp = client.get(f"/organizations/{org.id}/guests/banned")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Create guest --------------------------------------------------------------


def test_create_guest_by_username_returns_201_with_mazmo_profile_data(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that the endpoint looks up Mazmo and returns the profile data.

    WHY: The whole point is that staff don't need to know the numeric ID --
    the endpoint fetches it from Mazmo and registers the guest automatically.
    """
    resp = client.post("/guests/", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["mazmo_user_id"] == 39119
    assert data["username"] == "cindydark"
    assert data["displayname"] == "⚜️Lissandra⚜️"
    # GuestPublic no longer has is_banned
    assert "is_banned" not in data


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

    WHY: min_length=1 on username -- the request shouldn't even reach the
    Mazmo API if the username is blank.
    """
    resp = client.post("/guests/", json={"username": ""}, headers=staff_headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
