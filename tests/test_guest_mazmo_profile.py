"""
Tests for the guest Mazmo profile: the GuestMazmoProfile table, its
exposure via GuestPublic.mazmo_profile, and how link-mazmo/unlink-mazmo/
sync populate and clear it.

Sync-specific tests live in tests/test_sync.py (integration, via
TestClient), matching where the rest of the sync test suite already
lives - the same convention tests/test_guest_displayname_history.py's
own docstring documents for that sibling feature.

Unit tests for MazmoUserEntry's new fields and
MazmoClient.fetch_user_by_username()'s unified parsing live in
tests/test_mazmo.py, alongside the rest of the Mazmo client test suite.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.models.models import GuestMazmoProfile, Meetup, Organization
from tests.conftest import make_guest

# -- GuestMazmoProfile table -------------------------------------------------------


def test_guest_mazmo_profile_round_trips_through_the_database(session: Session):
    """
    Sanity check: a GuestMazmoProfile row can be created, flushed, and
    read back with all fields intact, keyed by guest_id as its own PK.
    """
    guest = make_guest(session, mazmo_user_id=999, mazmo_handle="roundtrip")
    now = datetime.now(UTC)
    profile = GuestMazmoProfile(
        guest_id=guest.id,
        avatar_url="https://cdn.mazmo.net/avatars/999/default.jpg",
        age=42,
        gender="nonbinary",
        pronoun="they/them",
        mazmo_suspended=True,
        mazmo_banned=False,
        synced_at=now,
    )
    session.add(profile)
    session.flush()

    fetched = session.get(GuestMazmoProfile, guest.id)
    assert fetched is not None
    assert fetched.guest_id == guest.id
    assert fetched.avatar_url == "https://cdn.mazmo.net/avatars/999/default.jpg"
    assert fetched.age == 42
    assert fetched.gender == "nonbinary"
    assert fetched.pronoun == "they/them"
    assert fetched.mazmo_suspended is True
    assert fetched.mazmo_banned is False


# -- PATCH /guests/{id}/link-mazmo populates GuestMazmoProfile -------------------


def test_link_mazmo_creates_guest_mazmo_profile_from_lookup_response(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """
    Verify link-mazmo populates GuestMazmoProfile from the lookup
    response already in memory, with no second call to Mazmo.
    """
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")

    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    profile = session.get(GuestMazmoProfile, guest.id)
    assert profile is not None
    assert profile.avatar_url == "https://cdn.mazmo.net/avatars/39119/default.jpg"
    assert profile.age == 29
    assert profile.gender == "female"
    assert profile.pronoun == "she/her"
    assert profile.mazmo_suspended is False
    assert profile.mazmo_banned is False
    mock_mazmo_for_guests.fetch_user_by_username.assert_called_once()


# -- POST /guests/mazmo creates GuestMazmoProfile ---------------------------------


def test_create_guest_from_mazmo_creates_guest_mazmo_profile(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """
    Verify creating a guest from a Mazmo username (POST /guests/mazmo)
    also creates GuestMazmoProfile in the same commit as the Guest and
    EventLog(GUEST_CREATED, ...) rows, using the same lookup response
    already in memory - no extra call to Mazmo.
    """
    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_201_CREATED
    guest_id = uuid.UUID(resp.json()["id"])

    profile = session.get(GuestMazmoProfile, guest_id)
    assert profile is not None
    assert profile.avatar_url == "https://cdn.mazmo.net/avatars/39119/default.jpg"
    assert profile.age == 29
    assert profile.gender == "female"
    assert profile.pronoun == "she/her"
    assert profile.mazmo_suspended is False
    assert profile.mazmo_banned is False
    mock_mazmo_for_guests.fetch_user_by_username.assert_called_once()


# -- PATCH /guests/{id}/unlink-mazmo deletes GuestMazmoProfile -------------------


def test_unlink_mazmo_deletes_guest_mazmo_profile(client: TestClient, staff_headers: dict, session: Session):
    """Verify unlink-mazmo deletes the guest's GuestMazmoProfile row."""
    guest = make_guest(session, mazmo_user_id=555, mazmo_handle="tobeunlinked")
    session.add(GuestMazmoProfile(guest_id=guest.id, age=40))
    session.flush()

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert session.get(GuestMazmoProfile, guest.id) is None


def test_unlink_mazmo_on_guest_without_profile_row_succeeds(client: TestClient, staff_headers: dict, session: Session):
    """
    Verify unlinking a guest that was linked but never synced (so it has
    no GuestMazmoProfile row yet) does not fail.
    """
    guest = make_guest(session, mazmo_user_id=556, mazmo_handle="neversynced")

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert session.get(GuestMazmoProfile, guest.id) is None


# -- Exposure in GuestPublic ------------------------------------------------------


def test_guest_detail_response_includes_mazmo_profile_when_linked_and_synced(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify GET /guests/{id} includes mazmo_profile when it has been synced."""
    guest = make_guest(session, mazmo_user_id=601, mazmo_handle="synced_guest")
    session.add(
        GuestMazmoProfile(
            guest_id=guest.id,
            avatar_url="https://cdn.mazmo.net/avatars/601/default.jpg",
            age=33,
            gender="male",
            pronoun="he/him",
            mazmo_suspended=False,
            mazmo_banned=False,
        )
    )
    session.flush()

    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    profile = resp.json()["mazmo_profile"]
    assert profile is not None
    assert profile["avatar_url"] == "https://cdn.mazmo.net/avatars/601/default.jpg"
    assert profile["age"] == 33
    assert profile["gender"] == "male"
    assert profile["pronoun"] == "he/him"
    assert profile["mazmo_suspended"] is False
    assert profile["mazmo_banned"] is False


def test_guest_detail_response_mazmo_profile_null_when_not_linked_to_mazmo(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify mazmo_profile is null for a guest with no Mazmo account at all."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Sin Mazmo")

    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["mazmo_profile"] is None


def test_guest_detail_response_mazmo_profile_null_when_linked_but_not_yet_synced(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify mazmo_profile is null for a guest that IS linked
    (mazmo_user_id set) but has never gone through a sync or link-mazmo
    that would have created its GuestMazmoProfile row.

    WHY: Distinct edge case from "not linked at all" - both produce the
    same null in the API, but for different reasons.
    """
    guest = make_guest(session, mazmo_user_id=602, mazmo_handle="linked_not_synced")

    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["mazmo_profile"] is None


def test_guest_list_response_does_not_trigger_n_plus_1_for_mazmo_profile(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify GET /guests/ loads mazmo_profile for every guest via a single
    extra query (selectinload), not one query per guest.
    """
    for i in range(700, 706):
        guest = make_guest(session, mazmo_user_id=i, mazmo_handle=f"guest{i}")
        session.add(GuestMazmoProfile(guest_id=guest.id, age=i - 600))
    session.flush()

    # NOTE: deliberately not `from tests.conftest import test_engine` (as a
    # literal reading of the plan for this test might suggest) - this
    # project's tests/ has no __init__.py, so pytest's own conftest.py
    # discovery loads it as top-level module `conftest`, while an explicit
    # `tests.conftest` import (enabled by pytest.ini's `pythonpath = .`)
    # loads a second, distinct module object with its own `test_engine`
    # Engine instance and connection pool. Listening on that second engine
    # never sees traffic from the `session`/`client` fixtures, which are
    # bound to the first. session.get_bind() sidesteps the whole problem by
    # asking the session itself which Connection/Engine it is actually using.
    engine = session.get_bind().engine
    statements: list[str] = []

    def _listener(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        resp = client.get("/guests/", headers=staff_headers)
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert resp.status_code == status.HTTP_200_OK
    profile_statements = [s for s in statements if "guest_mazmo_profile" in s.lower()]
    assert len(profile_statements) == 1


# -- E2E lifecycle -----------------------------------------------------------------


def test_link_sync_unlink_mazmo_profile_lifecycle_end_to_end(
    client: TestClient,
    staff_headers: dict,
    admin_headers: dict,
    session: Session,
    org: Organization,
    meetup: Meetup,
    mock_mazmo_for_guests,
    mock_mazmo: AsyncMock,
):
    """
    Full lifecycle: manual guest -> link-mazmo populates profile -> GET
    reflects it -> sync updates it in place -> GET reflects the update
    -> unlink-mazmo deletes it -> GET reflects null, other guest fields
    untouched.
    """
    # 1. Manual guest, no Mazmo link.
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Manual Guest")

    # 2. link-mazmo against a full Mazmo profile.
    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK

    # 3. GET detail -> mazmo_profile populated from the lookup.
    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)
    profile = resp.json()["mazmo_profile"]
    assert profile is not None
    assert profile["age"] == 29
    assert profile["mazmo_suspended"] is False

    # 4. A sync reports this same Mazmo user (id 39119) with a new age
    # and suspended=True.
    mock_mazmo.fetch_rsvps.return_value = {
        39119: SimpleNamespace(userId=39119, joinedAt=datetime(2026, 4, 1, tzinfo=UTC)),
    }
    mock_mazmo.fetch_users.return_value = {
        39119: SimpleNamespace(
            username="cindydark",
            displayname="Lissandra",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/39119/default.jpg"),
            age=31,
            gender="female",
            pronoun="she/her",
            suspended=True,
            banned=False,
        ),
    }
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    all_profiles = session.exec(select(GuestMazmoProfile).where(GuestMazmoProfile.guest_id == guest.id)).all()
    assert len(all_profiles) == 1

    # 5. GET detail again -> updated values reflected, no duplicate row.
    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)
    profile = resp.json()["mazmo_profile"]
    assert profile["age"] == 31
    assert profile["mazmo_suspended"] is True

    # 6. unlink-mazmo.
    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK

    # 7. GET detail once more -> mazmo_profile is null, displayname (set
    # by the sync in step 4) is unaffected.
    resp = client.get(f"/guests/{guest.id}", headers=staff_headers)
    data = resp.json()
    assert data["mazmo_profile"] is None
    assert data["displayname"] == "Lissandra"
