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

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import GuestMazmoProfile
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
