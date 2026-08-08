"""Tests for the /guests router and org-scoped ban endpoints.

Guest identity endpoints (/guests/*) are global.
Ban management is org-scoped: /organizations/{org_id}/guests/{id}/ban|unban
and /organizations/{org_id}/guests/banned.
"""

import uuid

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, Guest, OrgRole
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_ban, make_guest, make_org, make_org_member

# -- List guests ---------------------------------------------------------------


def test_list_guests_returns_200_ok(client: TestClient, staff_headers: dict, session: Session):
    """Verify that staff can list all guests."""
    make_guest(session, mazmo_user_id=1, mazmo_handle="alice")
    make_guest(session, mazmo_user_id=2, mazmo_handle="bob")
    resp = client.get("/guests/", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 2
    handles = [g["mazmo_handle"] for g in data["guests"]]
    assert "alice" in handles
    assert "bob" in handles


def test_list_guests_without_token_returns_401_unauthorized(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_guests_search_filters_by_displayname(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that ?q= filters by displayname (case-insensitive substring).

    WHY: Manual guests have no mazmo_handle, so displayname is the only
    thing staff can search by.
    """
    make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Juan Perez")
    make_guest(session, mazmo_user_id=1, mazmo_handle="other", displayname="Someone Else")
    resp = client.get("/guests/?q=perez", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 1
    assert data["guests"][0]["displayname"] == "Juan Perez"


def test_list_guests_search_filters_by_mazmo_handle(client: TestClient, staff_headers: dict, session: Session):
    """Verify that ?q= also matches on mazmo_handle."""
    make_guest(session, mazmo_user_id=1, mazmo_handle="cindydark", displayname="Cindy")
    make_guest(session, mazmo_user_id=2, mazmo_handle="other", displayname="Someone Else")
    resp = client.get("/guests/?q=cindy", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 1


# -- Get single guest ----------------------------------------------------------


def test_get_guest_returns_200_ok(client: TestClient, staff_headers: dict, session: Session):
    """Verify that staff can get a single guest."""
    guest = make_guest(session, mazmo_user_id=123, mazmo_handle="testguest")
    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["mazmo_handle"] == "testguest"


def test_get_nonexistent_guest_returns_404_not_found(client: TestClient, staff_headers: dict):
    """Verify that getting a nonexistent guest returns 404."""
    resp = client.get("/guests/00000000-0000-0000-0000-000000000000", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Get guest by Mazmo handle --------------------------------------------------


def test_get_guest_by_mazmo_handle_returns_200_with_guest_data(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that staff can look up a guest by their Mazmo handle.

    WHY: Staff at the door may know the handle but not the internal id.
    """
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")
    resp = client.get("/guests/by-mazmo-handle/cindydark", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["mazmo_handle"] == "cindydark"
    assert data["id"] == str(guest.id)


def test_get_guest_by_mazmo_handle_returns_404_when_not_in_system(client: TestClient, staff_headers: dict):
    """
    Verify that a 404 is returned when the handle doesn't exist in our system.

    WHY: The guest may exist on Mazmo but not have RSVPed to any tracked
    meetup yet, or may not have a Mazmo account at all.
    """
    resp = client.get("/guests/by-mazmo-handle/nobody", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "nobody" in resp.json()["detail"]


def test_get_guest_by_mazmo_handle_never_matches_manual_guests(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify that a manual (no-Mazmo) guest is never returned by this lookup."""
    make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Sin Mazmo")
    resp = client.get("/guests/by-mazmo-handle/nobody", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_get_guest_by_mazmo_handle_without_token_returns_401(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.get("/guests/by-mazmo-handle/someuser")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Create guest by Mazmo username ----------------------------------------------


def test_create_guest_by_mazmo_returns_201_with_mazmo_profile_data(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """
    Verify that the endpoint looks up Mazmo and returns the profile data.

    WHY: The whole point is that staff don't need to know the numeric ID --
    the endpoint fetches it from Mazmo and registers the guest automatically.
    """
    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["mazmo_user_id"] == 39119
    assert data["mazmo_handle"] == "cindydark"
    assert data["displayname"] == "⚜️Lissandra⚜️"
    assert data["instagram_username"] is None
    assert "id" in data


def test_create_guest_by_mazmo_stores_instagram_username(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """Verify that instagram_username is stored when provided."""
    resp = client.post(
        "/guests/mazmo",
        json={"username": "cindydark", "instagram_username": "@cindy.dark"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["instagram_username"] == "cindy.dark"


def test_create_guest_by_mazmo_writes_guest_created_event_log(
    client: TestClient, staff_headers: dict, session: Session, staff_user, mock_mazmo_for_guests
):
    """
    Verify that a GUEST_CREATED audit log entry is written with the correct actor.
    """
    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)
    guest_id = resp.json()["id"]

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest_id).where(EventLog.event_type == EventType.GUEST_CREATED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_create_guest_by_mazmo_returns_404_when_mazmo_says_user_not_found(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """Verify that a 404 from Mazmo surfaces as a 404 to the caller."""
    fake_request = httpx.Request("GET", "https://prod.mazmoapi.net/users/nobody")
    fake_response = httpx.Response(404, request=fake_request)
    exc = MazmoAPIError("Mazmo returned 404")
    exc.__cause__ = httpx.HTTPStatusError("404", request=fake_request, response=fake_response)
    mock_mazmo_for_guests.fetch_user_by_username.side_effect = exc

    resp = client.post("/guests/mazmo", json={"username": "nobody"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "nobody" in resp.json()["detail"]


def test_create_guest_by_mazmo_returns_409_when_guest_already_exists(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify that creating a duplicate returns 409."""
    make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")

    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_create_guest_by_mazmo_returns_504_when_mazmo_unreachable(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """Verify that a network failure surfaces as 504."""
    mock_mazmo_for_guests.fetch_user_by_username.side_effect = MazmoNetworkError("Connection timed out")

    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_create_guest_by_mazmo_returns_401_without_token(client: TestClient, mock_mazmo_for_guests):
    """Verify that unauthenticated requests are rejected."""
    resp = client.post("/guests/mazmo", json={"username": "cindydark"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_guest_by_mazmo_returns_422_when_username_is_empty(
    client: TestClient, staff_headers: dict, mock_mazmo_for_guests
):
    """Verify that an empty username string is rejected before hitting Mazmo."""
    resp = client.post("/guests/mazmo", json={"username": ""}, headers=staff_headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# -- Create guest without a Mazmo account -----------------------------------------


def test_create_manual_guest_returns_201(client: TestClient, staff_headers: dict):
    """Verify that a guest can be created with just a displayname."""
    resp = client.post("/guests/manual", json={"displayname": "Sin Mazmo"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["displayname"] == "Sin Mazmo"
    assert data["mazmo_user_id"] is None
    assert data["mazmo_handle"] is None


def test_create_manual_guest_stores_instagram_username(client: TestClient, staff_headers: dict):
    """Verify that instagram_username is stored when provided."""
    resp = client.post(
        "/guests/manual",
        json={"displayname": "Sin Mazmo", "instagram_username": "@sin.mazmo"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["instagram_username"] == "sin.mazmo"


def test_create_manual_guest_allows_duplicate_displaynames(client: TestClient, staff_headers: dict):
    """
    Verify that two manual guests can share a displayname.

    WHY: There is no external identifier to deduplicate against for
    manual guests - dedup/merge is explicitly out of scope.
    """
    first = client.post("/guests/manual", json={"displayname": "Same Name"}, headers=staff_headers)
    second = client.post("/guests/manual", json={"displayname": "Same Name"}, headers=staff_headers)
    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert first.json()["id"] != second.json()["id"]


def test_create_manual_guest_writes_guest_created_event_log(
    client: TestClient, staff_headers: dict, session: Session, staff_user
):
    """Verify that a GUEST_CREATED audit log entry is written."""
    resp = client.post("/guests/manual", json={"displayname": "Sin Mazmo"}, headers=staff_headers)
    guest_id = resp.json()["id"]

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest_id).where(EventLog.event_type == EventType.GUEST_CREATED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_create_manual_guest_returns_401_without_token(client: TestClient):
    """Verify that unauthenticated requests are rejected."""
    resp = client.post("/guests/manual", json={"displayname": "Sin Mazmo"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_manual_guest_returns_422_when_displayname_is_empty(client: TestClient, staff_headers: dict):
    """Verify that an empty displayname is rejected."""
    resp = client.post("/guests/manual", json={"displayname": ""}, headers=staff_headers)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# -- Link a guest to Mazmo --------------------------------------------------------


def test_link_mazmo_returns_200_and_updates_identity(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify that linking sets mazmo_user_id/mazmo_handle/displayname from Mazmo."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")

    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["mazmo_user_id"] == 39119
    assert data["mazmo_handle"] == "cindydark"
    assert data["displayname"] == "⚜️Lissandra⚜️"


def test_link_mazmo_preserves_instagram_username(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify that linking does not touch instagram_username."""
    guest = make_guest(
        session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual", instagram_username="handle"
    )
    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["instagram_username"] == "handle"


def test_link_mazmo_writes_guest_mazmo_linked_event(
    client: TestClient, staff_headers: dict, session: Session, staff_user, mock_mazmo_for_guests
):
    """Verify the GUEST_MAZMO_LINKED audit log entry."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")
    client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest.id).where(EventLog.event_type == EventType.GUEST_MAZMO_LINKED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_link_mazmo_returns_409_when_already_linked(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify that linking an already-linked guest returns 409."""
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="already_linked")
    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_409_CONFLICT


def test_link_mazmo_returns_409_when_mazmo_user_id_belongs_to_another_guest(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify that linking to an already-claimed mazmo_user_id returns 409, no merge."""
    make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")
    manual = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Otro Guest")

    resp = client.patch(f"/guests/{manual.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_link_mazmo_returns_404_for_nonexistent_guest(client: TestClient, staff_headers: dict, mock_mazmo_for_guests):
    """Verify that linking a nonexistent guest returns 404."""
    resp = client.patch(
        "/guests/00000000-0000-0000-0000-000000000000/link-mazmo",
        json={"username": "cindydark"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_link_mazmo_without_token_returns_401(client: TestClient, session: Session, mock_mazmo_for_guests):
    """Verify that unauthenticated requests are rejected."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")
    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Unlink a guest's Mazmo account ------------------------------------------------


def test_unlink_mazmo_returns_200_and_clears_identity(client: TestClient, staff_headers: dict, session: Session):
    """Verify that unlinking clears mazmo_user_id and mazmo_handle."""
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark", displayname="Cindy")

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["mazmo_user_id"] is None
    assert data["mazmo_handle"] is None
    assert data["displayname"] == "Cindy"  # not reverted, no name history kept


def test_unlink_mazmo_writes_guest_mazmo_unlinked_event(
    client: TestClient, staff_headers: dict, session: Session, staff_user
):
    """Verify the GUEST_MAZMO_UNLINKED audit log entry."""
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")
    client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_MAZMO_UNLINKED)
    ).first()
    assert event is not None
    assert event.actor_id == staff_user.id


def test_unlink_mazmo_returns_409_when_not_linked(client: TestClient, staff_headers: dict, session: Session):
    """Verify that unlinking an already-unlinked guest returns 409."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Sin Mazmo")
    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)
    assert resp.status_code == status.HTTP_409_CONFLICT


def test_unlink_then_relink_succeeds(client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests):
    """
    Verify that a freed mazmo_user_id can be linked again.

    WHY: Unlinking should not leave the mazmo_user_id permanently stuck
    on the UNIQUE constraint.
    """
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")
    client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["mazmo_user_id"] == 39119


def test_unlink_mazmo_returns_404_for_nonexistent_guest(client: TestClient, staff_headers: dict):
    """Verify that unlinking a nonexistent guest returns 404."""
    resp = client.patch("/guests/00000000-0000-0000-0000-000000000000/unlink-mazmo", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_unlink_mazmo_without_token_returns_401(client: TestClient, session: Session):
    """Verify that unauthenticated requests are rejected."""
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark")
    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_unlink_mazmo_returns_409_when_guest_has_active_ban(
    client: TestClient, staff_headers: dict, session: Session, admin_user
):
    """
    Verify that unlinking a banned guest's Mazmo account returns 409.

    WHY: Unlinking a banned guest would free their Mazmo handle for
    POST /guests/mazmo to re-register as a brand-new, unbanned guest -
    a ban evasion vector. This must be blocked regardless of which
    organization issued the ban.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=39119, mazmo_handle="cindydark", displayname="Cindy")
    make_ban(session, org=org, guest=guest, banned_by=admin_user, reason="Violated community guidelines")

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT

    session.expire_all()
    unchanged = session.get(Guest, guest.id)
    assert unchanged is not None
    assert unchanged.mazmo_user_id == 39119
    assert unchanged.mazmo_handle == "cindydark"


# -- Edit guest -------------------------------------------------------------------


def test_update_guest_changes_displayname(client: TestClient, staff_headers: dict, session: Session):
    """Verify that displayname can be edited."""
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="typo_name", displayname="Typo Nmae")
    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "Typo Name"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["displayname"] == "Typo Name"


def test_update_guest_changes_instagram_username(client: TestClient, staff_headers: dict, session: Session):
    """Verify that instagram_username can be edited."""
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="someone")
    resp = client.patch(f"/guests/{guest.id}", json={"instagram_username": "@new.handle"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["instagram_username"] == "new.handle"


def test_update_guest_can_clear_instagram_username_with_explicit_null(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that sending instagram_username: null clears it.

    WHY: A guest's Instagram handle can become wrong or unwanted after
    being set - staff need a way to remove it, not just overwrite it.
    """
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="someone", instagram_username="old.handle")
    resp = client.patch(f"/guests/{guest.id}", json={"instagram_username": None}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["instagram_username"] is None


def test_update_guest_omitting_instagram_username_leaves_it_unchanged(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that NOT sending instagram_username at all leaves it untouched.

    WHY: This is the key distinction from explicit null - omitted means
    "don't touch", explicit null means "clear it".
    """
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="someone", instagram_username="keep.this")
    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "New Name"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["instagram_username"] == "keep.this"


def test_update_guest_with_explicit_null_displayname_leaves_it_unchanged(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that {"displayname": null} is accepted (200), not rejected (422).

    WHY: UpdateGuestRequest.displayname is typed str | None, so Pydantic
    accepts an explicit null - it does NOT get rejected by the schema.
    The router's own guard silently ignores it instead, since
    Guest.displayname is non-nullable and cannot actually be cleared.
    """
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="someone", displayname="Original Name")
    resp = client.patch(f"/guests/{guest.id}", json={"displayname": None}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["displayname"] == "Original Name"


def test_update_guest_cannot_change_mazmo_fields(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify that mazmo_user_id/mazmo_handle are not part of the update schema.

    WHY: Those fields only change via link-mazmo/unlink-mazmo/sync.
    """
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="original")
    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "New Name"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["mazmo_handle"] == "original"


def test_update_guest_returns_404_for_nonexistent_guest(client: TestClient, staff_headers: dict):
    """Verify that editing a nonexistent guest returns 404."""
    resp = client.patch(
        "/guests/00000000-0000-0000-0000-000000000000",
        json={"displayname": "Doesn't Matter"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_update_guest_without_token_returns_401(client: TestClient, session: Session):
    """Verify that unauthenticated requests are rejected."""
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="someone")
    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "New Name"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# -- Ban guest (org-scoped) ----------------------------------------------------


def test_ban_guest_as_site_admin_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that a SITE_ADMIN can ban guests within an org.

    WHY: SITE_ADMIN bypasses org admin checks, so they can manage bans
    across all orgs without needing an explicit org membership.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="troublemaker")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="troublemaker")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="innocent")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="innocent")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="troublemaker")
    # First ban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "First offense"},
        headers=admin_headers,
    )
    # Try to ban again
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
        f"/organizations/{org.id}/guests/{uuid.uuid4()}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="needsreason")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="shortreason")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "abc"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_ban_guest_without_token_returns_401_unauthorized(client: TestClient, session: Session):
    """Verify that unauthenticated ban attempts are rejected."""
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="notoken")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="reformed")
    # First ban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Temporary ban"},
        headers=admin_headers,
    )
    # Then unban
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/unban",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    # Unban returns GuestPublic (identity only, no is_banned field)
    assert data["id"] == str(guest.id)
    assert data["mazmo_user_id"] == guest.mazmo_user_id
    assert data["mazmo_handle"] == "reformed"
    assert "is_banned" not in data


def test_unban_clears_ban_record(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that unbanning removes the ban record from the org.

    WHY: Clean slate - once unbanned, the guest should no longer appear
    in the org's banned list.
    """
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="cleared")
    # Ban then unban
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Temporary"},
        headers=admin_headers,
    )
    unban_resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/unban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="notbanned")
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/unban",
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
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="staffcantunban")
    # Admin bans
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Org STAFF tries to unban
    resp = client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/unban",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_unban_nonexistent_guest_returns_404_not_found(client: TestClient, admin_headers: dict, session: Session):
    """Verify that unbanning a nonexistent guest returns 404."""
    org = make_org(session)
    resp = client.patch(
        f"/organizations/{org.id}/guests/{uuid.uuid4()}/unban",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_unban_guest_without_token_returns_401_unauthorized(client: TestClient, admin_headers: dict, session: Session):
    """Verify that unauthenticated unban attempts are rejected."""
    org = make_org(session)
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="unbannotoken")
    # Admin bans
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Unauthenticated tries to unban
    resp = client.patch(f"/organizations/{org.id}/guests/{guest.id}/unban")
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
        guest1 = make_guest(session, mazmo_user_id=1, mazmo_handle="banned_one")
        make_guest(session, mazmo_user_id=2, mazmo_handle="not_banned")

        client.patch(
            f"/organizations/{org.id}/guests/{guest1.id}/ban",
            json={"reason": "Banned for testing"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org.id}/guests/banned", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert data["guests"][0]["mazmo_handle"] == "banned_one"

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
        make_guest(session, mazmo_user_id=1, mazmo_handle="innocent")

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
        guest = make_guest(session, mazmo_user_id=1, mazmo_handle="banned_details")
        client.patch(
            f"/organizations/{org.id}/guests/{guest.id}/ban",
            json={"reason": "Aggressive behavior"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org.id}/guests/banned", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        banned_guest = data["guests"][0]
        assert banned_guest["id"] == str(guest.id)
        assert banned_guest["mazmo_user_id"] == guest.mazmo_user_id
        assert banned_guest["mazmo_handle"] == "banned_details"
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
        guest = make_guest(session, mazmo_user_id=1, mazmo_handle="banned_in_a")

        client.patch(
            f"/organizations/{org_a.id}/guests/{guest.id}/ban",
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
