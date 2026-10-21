"""Unit tests for the GuestSyncer service internals.

These test the sync logic directly without going through HTTP - useful for
covering edge cases in the data pipeline that are hard to trigger via the router.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.models.models import EventLog, EventType, Guest, GuestDisplaynameHistory, GuestDisplaynameSource
from app.schemas import MazmoRsvpEntry, MazmoUserEntry
from app.services.sync import GuestSyncer
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp

# ── _build_guests ─────────────────────────────────────────────────────────────


def test_build_guests_skips_rsvps_with_missing_user_detail(session: Session):
    """
    Verify that RSVPs without matching user details are silently skipped.

    WHY: Mazmo might return RSVPs where the user detail fetch failed.
    We skip them rather than crashing the entire sync.
    """
    org = make_org(session)
    meetup = make_meetup(session, org=org)
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
    assert guests[0].mazmo_handle == "alice"


def test_build_guests_returns_empty_when_all_user_details_missing(session: Session):
    """
    Verify that an empty list is returned when all user details are missing.
    """
    org = make_org(session, name="Org 2", slug="org-2")
    meetup = make_meetup(session, org=org)
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
    org = make_org(session, name="Org 3", slug="org-3")
    meetup = make_meetup(session, org=org)
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")

    now = datetime.now(UTC)
    rsvps = {
        MazmoUserId(111): MazmoRsvpEntry(userId=111, joinedAt=now),
        MazmoUserId(222): MazmoRsvpEntry(userId=222, joinedAt=now),
    }
    user_details = {MazmoUserId(111): MazmoUserEntry(username="alice", displayname="Alice")}
    guest_id_map = {MazmoUserId(111): alice.id}

    rsvp_list = syncer._build_rsvps(rsvps, user_details, guest_id_map)

    assert len(rsvp_list) == 1
    assert rsvp_list[0].guest_id == alice.id


# ── _update_cancelled_rsvps ───────────────────────────────────────────────────


def test_update_cancelled_rsvps_marks_missing_guests_as_cancelled(session: Session):
    """
    Verify that guests who are no longer in Mazmo's list get marked as cancelled.

    WHY: Users can cancel their RSVP on Mazmo. When we sync, guests not in
    the current list should be marked cancelled so staff knows they might
    not show up.
    """
    org = make_org(session, name="Org 4", slug="org-4")
    meetup = make_meetup(session, org=org)
    alice = make_guest(session, mazmo_user_id=501, mazmo_handle="alice_cancel")
    bob = make_guest(session, mazmo_user_id=502, mazmo_handle="bob_cancel")
    alice_rsvp = make_rsvp(session, meetup=meetup, guest=alice)
    bob_rsvp = make_rsvp(session, meetup=meetup, guest=bob)

    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    # Only alice is in the current Mazmo RSVP list - bob cancelled
    syncer._update_cancelled_rsvps({MazmoUserId(501): alice.id})

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
    org = make_org(session, name="Org 5", slug="org-5")
    meetup = make_meetup(session, org=org)
    alice = make_guest(session, mazmo_user_id=503, mazmo_handle="alice_return")
    rsvp = make_rsvp(session, meetup=meetup, guest=alice)
    rsvp.cancelled_rsvp = True
    session.add(rsvp)
    session.flush()

    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    # Alice is back in the current RSVP list
    syncer._update_cancelled_rsvps({MazmoUserId(503): alice.id})

    session.refresh(rsvp)
    assert rsvp.cancelled_rsvp is False


def test_update_cancelled_rsvps_only_affects_current_meetup(session: Session):
    """
    Verify that cancellations are scoped to the current meetup.

    WHY: A guest cancelling from one meetup must not affect their RSVP
    status at other meetups.
    """
    org = make_org(session, name="Org 6", slug="org-6")
    meetup_a = make_meetup(session, org=org, name="Meetup A", mazmo_meetup_url="https://mazmo.net/test/meetup-a-601")
    meetup_b = make_meetup(session, org=org, name="Meetup B", mazmo_meetup_url="https://mazmo.net/test/meetup-b-601")
    alice = make_guest(session, mazmo_user_id=601, mazmo_handle="alice_scope")
    rsvp_a = make_rsvp(session, meetup=meetup_a, guest=alice)
    rsvp_b = make_rsvp(session, meetup=meetup_b, guest=alice)

    settings = get_settings()
    # Run cancellation logic for meetup_a with empty current list
    syncer = GuestSyncer(session, settings, meetup_a)
    syncer._update_cancelled_rsvps({})

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

    org = make_org(session, name="Org 7", slug="org-7")
    meetup = make_meetup(session, org=org, mazmo_meetup_url="https://mazmo.net/test/partial-sync")
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


# ── _upsert_guests (atomic upsert + history/event writes) ───────────────────


def test_new_guest_via_sync_creates_initial_history_row(session: Session):
    """
    Verify that a brand-new guest inserted via _upsert_guests gets one
    GuestDisplaynameHistory row with source=SYNC.

    WHY: The sync's atomic upsert must record the guest's starting
    displayname in the history table, same as any later change would be.
    """
    org = make_org(session, name="Org 8", slug="org-8")
    meetup = make_meetup(session, org=org)
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    syncer._upsert_guests([Guest(mazmo_user_id=MazmoUserId(801), mazmo_handle="newguest", displayname="New Guest")])

    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 801)).one()
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.SYNC
    assert history[0].displayname == "New Guest"
    assert history[0].actor_id is None


def test_sync_never_downgrades_manual_or_link_history(session: Session):
    """
    Verify sync always reflects Mazmo's value, even over a more recent
    manual edit.

    WHY: Sync has no awareness of GuestDisplaynameHistory rows created
    by PATCH /guests/{id} or link-mazmo. If Mazmo reports a different
    name than what we have, sync writes a new SYNC row regardless of who
    or what set the current value - it does not try to "protect" a
    manual edit. Placed here (service-level) rather than via HTTP so the
    pre-existing MANUAL_EDIT row and the exact upsert call can both be
    controlled precisely.
    """
    org = make_org(session, name="Org 10", slug="org-10")
    meetup = make_meetup(session, org=org)
    guest = make_guest(session, mazmo_user_id=902, mazmo_handle="edited", displayname="Manually Set Name")
    session.add(
        GuestDisplaynameHistory(
            guest_id=guest.id,
            displayname="Manually Set Name",
            source=GuestDisplaynameSource.MANUAL_EDIT,
            actor_id=None,
        )
    )
    session.flush()

    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)
    syncer._upsert_guests(
        [Guest(mazmo_user_id=MazmoUserId(902), mazmo_handle="edited", displayname="Mazmo Reported Name")]
    )

    session.refresh(guest)
    assert guest.displayname == "Mazmo Reported Name"

    history = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest.id)
        .order_by(GuestDisplaynameHistory.recorded_at)
    ).all()
    assert len(history) == 2
    assert history[0].source == GuestDisplaynameSource.MANUAL_EDIT
    assert history[1].source == GuestDisplaynameSource.SYNC
    assert history[1].displayname == "Mazmo Reported Name"


def test_sync_concurrent_upserts_do_not_create_duplicate_history_rows(session: Session):
    """
    Verify that two upserts proposing the same new displayname for the
    same guest only produce one history row, not two.

    WHY: Guest is a global (not per-org) table, so two syncs of
    different meetups that share a guest could run close together. True
    concurrent requests cannot be reproduced with a single test
    session/transaction (same limitation already noted for check-in
    concurrency in tests/test_meetups.py - see the comment block above
    test_checkin_second_attempt_after_first_succeeds_returns_409 there),
    so this verifies the observable guarantee instead: the atomic
    "WHERE displayname IS DISTINCT FROM" upsert means a second upsert
    that finds the row already matching produces no RETURNING row, and
    therefore no duplicate history/event write.
    """
    org = make_org(session, name="Org 9", slug="org-9")
    meetup = make_meetup(session, org=org)
    guest = make_guest(session, mazmo_user_id=901, mazmo_handle="racer", displayname="Old Name")
    settings = get_settings()
    syncer = GuestSyncer(session, settings, meetup)

    syncer._upsert_guests([Guest(mazmo_user_id=MazmoUserId(901), mazmo_handle="racer", displayname="New Name")])
    syncer._upsert_guests([Guest(mazmo_user_id=MazmoUserId(901), mazmo_handle="racer", displayname="New Name")])

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].displayname == "New Name"

    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(events) == 1
