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

from datetime import UTC, datetime

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
