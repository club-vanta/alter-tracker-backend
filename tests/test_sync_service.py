"""Unit tests for the GuestSyncer service internals.

These test the sync logic directly without going through HTTP - useful for
covering edge cases in the data pipeline that are hard to trigger via the router.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.schemas import MazmoRsvpEntry, MazmoUserEntry
from app.services.sync import GuestSyncer
from tests.conftest import make_guest, make_meetup, make_rsvp

# ── _build_guests ─────────────────────────────────────────────────────────────


def test_build_guests_skips_rsvps_with_missing_user_detail(session: Session):
    """
    Verify that RSVPs without matching user details are silently skipped.

    WHY: Mazmo might return RSVPs where the user detail fetch failed.
    We skip them rather than crashing the entire sync.
    """
    meetup = make_meetup(session)
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    now = datetime.now(UTC)
    rsvps = {
        MazmoUserId(111): MazmoRsvpEntry(userId=111, joinedAt=now),
        MazmoUserId(222): MazmoRsvpEntry(userId=222, joinedAt=now),
    }
    user_details = {
        MazmoUserId(111): MazmoUserEntry(username="alice", displayname="Alice"),
        # 222 is missing - simulates a failed user detail fetch
    }

    guests = syncer._build_guests(rsvps, user_details)

    assert len(guests) == 1
    assert guests[0].username == "alice"


def test_build_guests_returns_empty_when_all_user_details_missing(session: Session):
    """
    Verify that an empty list is returned when all user details are missing.
    """
    meetup = make_meetup(session)
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    rsvps = {MazmoUserId(111): MazmoRsvpEntry(userId=111, joinedAt=datetime.now(UTC))}
    user_details: dict[MazmoUserId, MazmoUserEntry] = {}

    guests = syncer._build_guests(rsvps, user_details)

    assert guests == []


# ── _build_rsvps ──────────────────────────────────────────────────────────────


def test_build_rsvps_skips_entries_without_user_details(session: Session):
    """
    Verify that _build_rsvps skips RSVPs with no user details (same logic as _build_guests).
    """
    meetup = make_meetup(session)
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    now = datetime.now(UTC)
    rsvps = {
        MazmoUserId(111): MazmoRsvpEntry(userId=111, joinedAt=now),
        MazmoUserId(222): MazmoRsvpEntry(userId=222, joinedAt=now),
    }
    user_details = {MazmoUserId(111): MazmoUserEntry(username="alice", displayname="Alice")}

    rsvp_list = syncer._build_rsvps(rsvps, user_details)

    assert len(rsvp_list) == 1
    assert rsvp_list[0].guest_id == 111


# ── _update_cancelled_rsvps ───────────────────────────────────────────────────


def test_update_cancelled_rsvps_marks_missing_guests_as_cancelled(session: Session):
    """
    Verify that guests who are no longer in Mazmo's list get marked as cancelled.

    WHY: Users can cancel their RSVP on Mazmo. When we sync, guests not in
    the current list should be marked cancelled so staff knows they might
    not show up.
    """
    meetup = make_meetup(session)
    alice = make_guest(session, mazmo_user_id=501, username="alice_cancel")
    bob = make_guest(session, mazmo_user_id=502, username="bob_cancel")
    alice_rsvp = make_rsvp(session, meetup=meetup, guest=alice)
    bob_rsvp = make_rsvp(session, meetup=meetup, guest=bob)

    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    # Only alice is in the current Mazmo RSVP list - bob cancelled
    syncer._update_cancelled_rsvps({MazmoUserId(501)})

    session.refresh(alice_rsvp)
    session.refresh(bob_rsvp)

    assert alice_rsvp.cancelled_rsvp is False
    assert bob_rsvp.cancelled_rsvp is True


def test_update_cancelled_rsvps_reinstates_returning_guests(session: Session):
    """
    Verify that previously cancelled RSVPs are reinstated when guests re-RSVP.

    WHY: A guest might cancel and then re-RSVP. We should flip them back
    to active so they appear in the guest list again.
    """
    meetup = make_meetup(session)
    alice = make_guest(session, mazmo_user_id=503, username="alice_return")
    rsvp = make_rsvp(session, meetup=meetup, guest=alice)
    rsvp.cancelled_rsvp = True
    session.add(rsvp)
    session.flush()

    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    # Alice is back in the current RSVP list
    syncer._update_cancelled_rsvps({MazmoUserId(503)})

    session.refresh(rsvp)
    assert rsvp.cancelled_rsvp is False


def test_update_cancelled_rsvps_only_affects_current_meetup(session: Session):
    """
    Verify that cancellations are scoped to the current meetup.

    WHY: A guest cancelling from one meetup must not affect their RSVP
    status at other meetups.
    """
    meetup_a = make_meetup(
        session, name="Meetup A", mazmo_meetup_url="https://mazmo.net/test/meetup-a-601"
    )
    meetup_b = make_meetup(
        session, name="Meetup B", mazmo_meetup_url="https://mazmo.net/test/meetup-b-601"
    )
    alice = make_guest(session, mazmo_user_id=601, username="alice_scope")
    rsvp_a = make_rsvp(session, meetup=meetup_a, guest=alice)
    rsvp_b = make_rsvp(session, meetup=meetup_b, guest=alice)

    settings = get_settings()
    # Run cancellation logic for meetup_a with empty current list
    syncer = GuestSyncer(session, settings, meetup_a)
    syncer._update_cancelled_rsvps(set())

    session.refresh(rsvp_a)
    session.refresh(rsvp_b)

    assert rsvp_a.cancelled_rsvp is True  # cancelled in meetup_a
    assert rsvp_b.cancelled_rsvp is False  # unaffected in meetup_b


# ── sync with partial user details ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_returns_skipped_count_when_all_user_details_missing(session: Session):
    """
    Verify that sync reports skipped count when user details can't be fetched.

    WHY: If the users endpoint fails for all IDs, we should return a sensible
    response indicating how many RSVPs were skipped, not crash.
    """
    from unittest.mock import AsyncMock, patch

    meetup = make_meetup(session, mazmo_meetup_url="https://mazmo.net/test/partial-sync")
    settings = get_settings()

    now = datetime.now(UTC)
    with patch("app.services.sync.MazmoClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.fetch_rsvps.return_value = {
            MazmoUserId(701): MazmoRsvpEntry(userId=701, joinedAt=now),
            MazmoUserId(702): MazmoRsvpEntry(userId=702, joinedAt=now),
        }
        mock_instance.fetch_users.return_value = {}  # No user details at all
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        MockClient.return_value = mock_instance

        syncer = GuestSyncer(session, settings, meetup)
        result = await syncer.sync()

    assert result.inserted == 0
    assert result.skipped == 2
