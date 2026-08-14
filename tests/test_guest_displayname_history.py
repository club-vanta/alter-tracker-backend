"""
Tests for guest displayname history: the GuestDisplaynameHistory table,
GUEST_DISPLAYNAME_CHANGED events, and GET /guests/{guest_id}/displayname-history.

Sync-specific tests live in tests/test_sync.py (integration, via
TestClient) and tests/test_sync_service.py (unit, direct calls to
GuestSyncer._upsert_guests) instead, matching where the rest of the
sync test suite already lives.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, GuestDisplaynameHistory, GuestDisplaynameSource
from app.schemas import EventTypeFilter
from tests.conftest import make_guest

# ── GuestDisplaynameSource enum ────────────────────────────────────────────────


def test_guest_displayname_source_has_exactly_four_values():
    """
    Regression guard for the GuestDisplaynameSource enum.

    WHY: If a 5th value is ever added, every place that documents or
    validates "the 4 sources" needs a deliberate update, not silent drift.
    """
    assert {member.value for member in GuestDisplaynameSource} == {
        "SYNC",
        "MANUAL_EDIT",
        "MAZMO_LINK",
        "BACKFILL",
    }


# ── Event type filter ────────────────────────────────────────────────────────


def test_event_log_filters_by_guest_displayname_changed(session: Session):
    """
    Verify EventTypeFilter accepts GUEST_DISPLAYNAME_CHANGED and that
    filtering EventLog by this event_type returns only matching rows.

    WHY: GUEST_DISPLAYNAME_CHANGED events have org_id=None (same as
    GUEST_CREATED/GUEST_MAZMO_LINKED/GUEST_MAZMO_UNLINKED), so they are
    never returned by any of the org-scoped GET /organizations/{org_id}
    /events/... endpoints - same pre-existing limitation as those other
    global guest event types (app/routers/events.py always filters
    EventLog.org_id == org_id, and NULL never matches). This is verified
    at the DB layer directly, matching how existing tests check
    GUEST_MAZMO_LINKED/GUEST_CREATED (e.g.
    test_link_mazmo_writes_guest_mazmo_linked_event in test_guests.py),
    not via an HTTP events endpoint.
    """
    assert EventTypeFilter("GUEST_DISPLAYNAME_CHANGED") == EventTypeFilter.GUEST_DISPLAYNAME_CHANGED

    guest = make_guest(session, mazmo_user_id=14, mazmo_handle="filtertest")
    session.add(EventLog(event_type=EventType.GUEST_DISPLAYNAME_CHANGED, guest_id=guest.id, org_id=None))
    session.add(EventLog(event_type=EventType.CHECK_IN, guest_id=guest.id, org_id=None))
    session.flush()

    matching = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED.value)
    ).all()
    assert len(matching) == 1
    assert matching[0].event_type == EventType.GUEST_DISPLAYNAME_CHANGED.value


# ── Backfill migration SQL ───────────────────────────────────────────────────


def test_backfill_migration_creates_one_row_per_existing_guest(session: Session):
    """
    Verify the migration's backfill INSERT creates exactly one BACKFILL
    row per pre-existing guest, using their current displayname.

    WHY: This runs the exact SQL from alembic/versions/0018_guest_
    displayname_history.py's upgrade() directly against the test
    session, rather than through Alembic - the test database schema is
    built via SQLModel.metadata.create_all() (see setup_test_database in
    tests/conftest.py), not by running migrations, so there is no
    "pre-migration" state to actually migrate in this test suite. This
    mirrors how the set_arrival_order trigger is tested: its SQL is
    recreated directly in setup_test_database instead of running the
    real migration that originally introduced it.

    The WHERE id IN (...) restriction below (absent from the real
    migration, which has no WHERE clause and applies to every guest) is
    added only so this test's assertions are scoped to the guests it
    itself created, in case other guests exist in this transaction.
    """
    guest_a = make_guest(session, mazmo_user_id=701, mazmo_handle="backfill_a", displayname="Backfill A")
    guest_b = make_guest(session, mazmo_user_id=702, mazmo_handle="backfill_b", displayname="Backfill B")

    backfill_stmt = text("""
        INSERT INTO guest_displayname_history (guest_id, displayname, source, actor_id, recorded_at)
        SELECT id, displayname, 'BACKFILL', NULL, now()
        FROM guests
        WHERE id IN (:a, :b)
    """).bindparams(a=guest_a.id, b=guest_b.id)
    session.exec(backfill_stmt)  # type: ignore[arg-type]
    session.flush()

    for guest in (guest_a, guest_b):
        rows = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
        assert len(rows) == 1
        assert rows[0].source == GuestDisplaynameSource.BACKFILL
        assert rows[0].displayname == guest.displayname
        assert rows[0].actor_id is None


# -- PATCH /guests/{id} ----------------------------------------------------------


def test_update_guest_displayname_creates_history_row_source_manual_edit(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify PATCH /guests/{id} with a changed displayname writes a MANUAL_EDIT history row."""
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="typo_name", displayname="Typo Nmae")

    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "Typo Name"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.MANUAL_EDIT
    assert history[0].displayname == "Typo Name"


def test_update_guest_displayname_creates_eventlog_entry(client: TestClient, staff_headers: dict, session: Session):
    """Verify PATCH /guests/{id} with a changed displayname writes a GUEST_DISPLAYNAME_CHANGED event."""
    guest = make_guest(session, mazmo_user_id=2, mazmo_handle="another", displayname="Old Name")

    client.patch(f"/guests/{guest.id}", json={"displayname": "New Name"}, headers=staff_headers)

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert event.org_id is None
    assert event.reason == "Displayname changed from 'Old Name' to 'New Name'"


def test_update_guest_without_displayname_field_creates_no_history_row(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify omitting displayname from the request body writes no history row."""
    guest = make_guest(session, mazmo_user_id=3, mazmo_handle="untouched", displayname="Kept Name")

    resp = client.patch(f"/guests/{guest.id}", json={"instagram_username": "new.handle"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []


def test_update_guest_displayname_to_same_value_creates_no_history_row(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify "changing" a displayname to its current value writes no history row."""
    guest = make_guest(session, mazmo_user_id=4, mazmo_handle="samey", displayname="Same Name")

    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "Same Name"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []


def test_update_guest_displayname_actor_id_matches_requesting_staff(
    client: TestClient, staff_headers: dict, session: Session, staff_user
):
    """Verify the history row's actor_id is the staff member who made the request."""
    guest = make_guest(session, mazmo_user_id=5, mazmo_handle="attributed", displayname="Before")

    client.patch(f"/guests/{guest.id}", json={"displayname": "After"}, headers=staff_headers)

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).one()
    assert history.actor_id == staff_user.id
