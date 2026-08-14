"""
Tests for guest displayname history: the GuestDisplaynameHistory table,
GUEST_DISPLAYNAME_CHANGED events, and GET /guests/{guest_id}/displayname-history.

Sync-specific tests live in tests/test_sync.py (integration, via
TestClient) and tests/test_sync_service.py (unit, direct calls to
GuestSyncer._upsert_guests) instead, matching where the rest of the
sync test suite already lives.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    Meetup,
)
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


# -- PATCH /guests/{id}/link-mazmo ------------------------------------------------


def test_link_mazmo_with_different_displayname_creates_history_row_source_mazmo_link(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify linking to Mazmo with a different displayname writes a MAZMO_LINK history row."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")
    expected_displayname = mock_mazmo_for_guests.fetch_user_by_username.return_value.displayname

    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.MAZMO_LINK
    assert history[0].displayname == expected_displayname


def test_link_mazmo_with_same_displayname_creates_no_history_row(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify linking to Mazmo with a matching displayname writes no history row."""
    mock_mazmo_for_guests.fetch_user_by_username.return_value = SimpleNamespace(
        mazmo_user_id=39119, username="cindydark", displayname="Matching Name"
    )
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Matching Name")

    resp = client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []


def test_link_mazmo_creates_both_eventlog_entries(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify linking with a name change writes both GUEST_MAZMO_LINKED and GUEST_DISPLAYNAME_CHANGED, same commit."""
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Nombre Manual")

    client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    linked = session.exec(
        select(EventLog).where(EventLog.guest_id == guest.id).where(EventLog.event_type == EventType.GUEST_MAZMO_LINKED)
    ).one()
    changed = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert linked.timestamp == changed.timestamp


def test_link_mazmo_with_unchanged_displayname_creates_only_mazmo_linked_eventlog(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify linking without a name change writes only GUEST_MAZMO_LINKED, not GUEST_DISPLAYNAME_CHANGED."""
    mock_mazmo_for_guests.fetch_user_by_username.return_value = SimpleNamespace(
        mazmo_user_id=39119, username="cindydark", displayname="Matching Name"
    )
    guest = make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Matching Name")

    client.patch(f"/guests/{guest.id}/link-mazmo", json={"username": "cindydark"}, headers=staff_headers)

    linked = session.exec(
        select(EventLog).where(EventLog.guest_id == guest.id).where(EventLog.event_type == EventType.GUEST_MAZMO_LINKED)
    ).all()
    changed = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(linked) == 1
    assert changed == []


# -- Guest creation writes an initial history row ---------------------------------


def test_create_guest_by_mazmo_creates_initial_history_row(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """Verify POST /guests/mazmo writes an initial GuestDisplaynameHistory row."""
    resp = client.post("/guests/mazmo", json={"username": "cindydark"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_201_CREATED
    guest_id = uuid.UUID(resp.json()["id"])

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest_id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.MAZMO_LINK
    assert history[0].actor_id is not None


def test_create_manual_guest_creates_initial_history_row(client: TestClient, staff_headers: dict, session: Session):
    """Verify POST /guests/manual writes an initial GuestDisplaynameHistory row."""
    resp = client.post("/guests/manual", json={"displayname": "Recien Llegado"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_201_CREATED
    guest_id = uuid.UUID(resp.json()["id"])

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest_id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.MANUAL_EDIT
    assert history[0].displayname == "Recien Llegado"


def test_create_manual_guest_creates_no_displayname_changed_eventlog(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify creating a guest never writes a GUEST_DISPLAYNAME_CHANGED event - only GUEST_CREATED."""
    resp = client.post("/guests/manual", json={"displayname": "Recien Llegado"}, headers=staff_headers)
    guest_id = uuid.UUID(resp.json()["id"])

    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest_id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert events == []


# -- GET /guests/{guest_id}/displayname-history -----------------------------------


def test_get_displayname_history_returns_200_ordered_by_recorded_at_desc(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify the history list is ordered newest-first."""
    guest = make_guest(session, mazmo_user_id=10, mazmo_handle="ordered", displayname="First")
    session.add(GuestDisplaynameHistory(guest_id=guest.id, displayname="First", source=GuestDisplaynameSource.SYNC))
    session.flush()
    client.patch(f"/guests/{guest.id}", json={"displayname": "Second"}, headers=staff_headers)
    client.patch(f"/guests/{guest.id}", json={"displayname": "Third"}, headers=staff_headers)

    resp = client.get(f"/guests/{guest.id}/displayname-history", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    names = [row["displayname"] for row in resp.json()["history"]]
    assert names == ["Third", "Second", "First"]


def test_get_displayname_history_returns_401_without_auth(client: TestClient, session: Session):
    """Verify the endpoint requires authentication."""
    guest = make_guest(session, mazmo_user_id=11, mazmo_handle="noauth")
    resp = client.get(f"/guests/{guest.id}/displayname-history")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_displayname_history_includes_source_and_actor(
    client: TestClient, staff_headers: dict, session: Session, staff_user
):
    """Verify each entry includes source and actor (present for MANUAL_EDIT, null for SYNC)."""
    guest = make_guest(session, mazmo_user_id=12, mazmo_handle="withactor", displayname="Before")
    session.add(GuestDisplaynameHistory(guest_id=guest.id, displayname="Before", source=GuestDisplaynameSource.SYNC))
    session.flush()
    client.patch(f"/guests/{guest.id}", json={"displayname": "After"}, headers=staff_headers)

    resp = client.get(f"/guests/{guest.id}/displayname-history", headers=staff_headers)
    rows = resp.json()["history"]

    assert rows[0]["source"] == "MANUAL_EDIT"
    assert rows[0]["actor"]["username"] == staff_user.username
    assert rows[1]["source"] == "SYNC"
    assert rows[1]["actor"] is None


def test_get_displayname_history_returns_404_for_nonexistent_guest(client: TestClient, staff_headers: dict):
    """Verify a nonexistent guest id returns 404."""
    resp = client.get(
        "/guests/00000000-0000-0000-0000-000000000000/displayname-history",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_get_displayname_history_returns_single_row_for_guest_with_no_changes_since_creation(
    client: TestClient, staff_headers: dict
):
    """
    Verify a freshly-created guest with no subsequent edits has exactly
    one history row, not an empty list.

    WHY: POST /guests/manual writes an initial MANUAL_EDIT row at
    creation time (see the guest-creation task) - the response must
    reflect that starting value, never an empty history.
    """
    create_resp = client.post("/guests/manual", json={"displayname": "Solo Creation"}, headers=staff_headers)
    guest_id = create_resp.json()["id"]

    resp = client.get(f"/guests/{guest_id}/displayname-history", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 1
    assert data["history"][0]["displayname"] == "Solo Creation"
    assert data["history"][0]["source"] == "MANUAL_EDIT"


def test_get_displayname_history_accessible_by_any_approved_staff(
    client: TestClient, staff_headers: dict, session: Session
):
    """Verify the endpoint does not require org membership - it is a global guest endpoint."""
    guest = make_guest(session, mazmo_user_id=13, mazmo_handle="global", displayname="Global Guest")
    session.add(
        GuestDisplaynameHistory(guest_id=guest.id, displayname="Global Guest", source=GuestDisplaynameSource.SYNC)
    )
    session.flush()

    resp = client.get(f"/guests/{guest.id}/displayname-history", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK


# -- End-to-end multi-endpoint scenarios -------------------------------------------


def test_displayname_change_history_end_to_end(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Full flow: sync creates a guest, an admin edits it manually, sync
    runs again with a different Mazmo name - verify the 3-entry history
    and that only the 2 real changes appear as GUEST_DISPLAYNAME_CHANGED
    events (not the initial sync-created row).
    """
    # 1. Sync creates guest 111 with displayname "Juan"
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(username="alice", displayname="Juan"),
        222: SimpleNamespace(username="bob", displayname="Bob"),
    }
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    assert guest.displayname == "Juan"

    # 2. Admin edits manually to "Juan Perez"
    edit_resp = client.patch(f"/guests/{guest.id}", json={"displayname": "Juan Perez"}, headers=admin_headers)
    assert edit_resp.status_code == status.HTTP_200_OK

    # 3. Sync again, Mazmo now reports "Juan P."
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(username="alice", displayname="Juan P."),
        222: SimpleNamespace(username="bob", displayname="Bob"),
    }
    resync_resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resync_resp.status_code == status.HTTP_200_OK

    # 4. Verify history: 3 entries, newest first, correct sources
    history_resp = client.get(f"/guests/{guest.id}/displayname-history", headers=admin_headers)
    rows = history_resp.json()["history"]
    assert [r["displayname"] for r in rows] == ["Juan P.", "Juan Perez", "Juan"]
    assert [r["source"] for r in rows] == ["SYNC", "MANUAL_EDIT", "SYNC"]
    assert rows[0]["actor"] is None
    assert rows[1]["actor"] is not None
    assert rows[2]["actor"] is None

    # 5. Verify exactly 2 GUEST_DISPLAYNAME_CHANGED events (not 3 - the
    #    initial sync-created value is not a "change")
    changed_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(changed_events) == 2


def test_link_mazmo_with_name_change_end_to_end(
    client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests
):
    """
    Full flow: guest created manually as "Ana", then linked to a Mazmo
    profile with displayname "Ana Garcia" - verify 2 history rows and
    both GUEST_MAZMO_LINKED + GUEST_DISPLAYNAME_CHANGED in the same commit.
    """
    mock_mazmo_for_guests.fetch_user_by_username.return_value = SimpleNamespace(
        mazmo_user_id=50001, username="ana_garcia", displayname="Ana Garcia"
    )

    create_resp = client.post("/guests/manual", json={"displayname": "Ana"}, headers=staff_headers)
    guest_id = uuid.UUID(create_resp.json()["id"])

    link_resp = client.patch(f"/guests/{guest_id}/link-mazmo", json={"username": "ana_garcia"}, headers=staff_headers)
    assert link_resp.status_code == status.HTTP_200_OK

    history = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest_id)
        .order_by(GuestDisplaynameHistory.recorded_at)
    ).all()
    assert [h.source for h in history] == [GuestDisplaynameSource.MANUAL_EDIT, GuestDisplaynameSource.MAZMO_LINK]
    assert [h.displayname for h in history] == ["Ana", "Ana Garcia"]

    linked_event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest_id).where(EventLog.event_type == EventType.GUEST_MAZMO_LINKED)
    ).one()
    changed_event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest_id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert linked_event.timestamp == changed_event.timestamp


def test_backfilled_guest_then_manual_edit_end_to_end(client: TestClient, staff_headers: dict, session: Session):
    """
    Simulates a guest that existed before this migration (its only
    history row is BACKFILL, no EventLog), then receives a manual edit -
    verify [BACKFILL, MANUAL_EDIT] order and exactly 1
    GUEST_DISPLAYNAME_CHANGED event (the manual one, none for BACKFILL).
    """
    guest = make_guest(session, mazmo_user_id=15, mazmo_handle="preexisting", displayname="Pre-Existing Name")
    session.add(
        GuestDisplaynameHistory(
            guest_id=guest.id,
            displayname="Pre-Existing Name",
            source=GuestDisplaynameSource.BACKFILL,
            actor_id=None,
        )
    )
    session.flush()

    resp = client.patch(f"/guests/{guest.id}", json={"displayname": "Updated Name"}, headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK

    history = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest.id)
        .order_by(GuestDisplaynameHistory.recorded_at)
    ).all()
    assert [h.source for h in history] == [GuestDisplaynameSource.BACKFILL, GuestDisplaynameSource.MANUAL_EDIT]

    changed_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(changed_events) == 1
