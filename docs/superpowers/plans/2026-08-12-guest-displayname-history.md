# Guest Displayname History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Mazmo sync reflect displayname changes (it currently never does, per `Guest`'s own docstring), and add a full audit history of every displayname value a guest has ever had, sourced from sync, manual edits, Mazmo linking, and guest creation.

**Architecture:** A new `guest_displayname_history` table records one row per displayname value (a full timeline, not before/after pairs). Four write paths append to it: the sync's rewritten atomic upsert, `PATCH /guests/{id}`, `PATCH /guests/{id}/link-mazmo`, and the two guest-creation endpoints (`POST /guests/mazmo`, `POST /guests/manual`). A companion `EventLog(GUEST_DISPLAYNAME_CHANGED)` is written only when an *existing* guest's value actually changes - never for a brand-new guest's first value, to avoid flooding the org timeline with noise on every sync. A new read-only endpoint exposes the full, unpaginated history per guest.

**Tech Stack:** FastAPI, SQLModel (Pydantic v2 + SQLAlchemy 2.0), PostgreSQL (`INSERT ... ON CONFLICT ... WHERE ... RETURNING`), Alembic, pytest (real Postgres test DB, no mocked sessions).

## Global Constraints

- ASCII-only in all code, comments, and docstrings you write - no em-dashes, no Unicode arrows, no smart quotes. Use `-` and `->`. (CLAUDE.md rule 9.)
- Never `DELETE` real data as part of this feature; this plan introduces no deletes.
- `EventLog` write + the corresponding domain-state write (or `GuestDisplaynameHistory` write) happen in the **same commit**, always (CLAUDE.md "Audit trail" rule).
- Services (`app/services/`) never import `fastapi` or raise `HTTPException`. This plan's only service-layer change (`app/services/sync.py`) must keep that invariant.
- `basedpyright` must pass in strict mode; use `# type: ignore[specific-code]` only where the existing code already does (e.g. raw `pg_insert` statements passed to `session.exec`).
- Run `uv run ruff format` and `uv run ruff check` before each commit.
- All new tests use the real Postgres test database via the `session`/`client` fixtures in `tests/conftest.py`. Never mock the DB.

---

## Design decisions this plan makes (spec gaps resolved)

The approved spec at `docs/superpowers/specs/2026-08-12-guest-displayname-history-design.md` leaves two things underspecified. Both are resolved here, deliberately, so no task below contains an unresolved ambiguity:

1. **Sync's `EventLog.reason` text cannot include the "from X" old value.** The spec's SQL example for the atomic upsert only returns `id, displayname` (plus the `xmax = 0` trick). `RETURNING` in an `INSERT ... ON CONFLICT DO UPDATE` reflects the row's state *after* the update, so the pre-update value is gone by the time `RETURNING` evaluates - there is no way to recover it in the same single atomic statement without a much more complex CTE-based query. Since atomicity (avoiding the two-step read-then-write race) is the entire point of this rewrite, this plan keeps the single-statement `pg_insert(...).on_conflict_do_update(...)` and uses a reason string that only states the new value: `f"Displayname changed to '{new}' via Mazmo sync"`. This is different from the `f"Displayname changed from '{old}' to '{new}'"` format used by `PATCH /guests/{id}` and `PATCH /guests/{id}/link-mazmo` (both of which already have the guest loaded before mutating it, so `old` is trivially available there). This was validated against the real test database before writing this plan (see Task 3).

2. **`POST /guests/mazmo` and `POST /guests/manual` also write an initial `GuestDisplaynameHistory` row**, even though the spec's prose only calls out "3 write points" (sync, `PATCH /guests/{id}`, `PATCH /guests/{id}/link-mazmo`). This is required by the spec's own test `test_get_displayname_history_returns_single_row_for_guest_with_no_changes_since_creation`, which explicitly expects a freshly created, never-edited guest to have exactly 1 history row (not an empty list) - and a guest created via these two endpoints has no other write path that would ever create that row. The source is `GuestDisplaynameSource.MANUAL_EDIT` (a staff member actively set the value at creation time; `SYNC` is reserved for the background Mazmo sync process specifically). No `EventLog(GUEST_DISPLAYNAME_CHANGED)` is written for these - `EventLog(GUEST_CREATED)` already covers creation, and a first value is not a "change," consistent with the rule applied everywhere else in this plan.

---

## Task 1: Data model - enum, table, EventType value

**Files:**
- Modify: `app/models/models.py:41-60` (EventType enum), `app/models/models.py:228-263` (Guest class), `app/models/models.py` after line 323 (new section after `OrganizationBan`)
- Modify: `app/schemas/events.py:25-37` (EventTypeFilter, post Plan 1's `GUEST_TYPE_CHANGED` addition)
- Test: `tests/test_guest_displayname_history.py` (new file)

**Interfaces:**
- Produces: `GuestDisplaynameSource` (StrEnum: `SYNC`, `MANUAL_EDIT`, `MAZMO_LINK`, `BACKFILL`), `GuestDisplaynameHistory` (table, fields `id: int | None`, `guest_id: uuid.UUID`, `displayname: str`, `source: str`, `actor_id: int | None`, `recorded_at: datetime`), `EventType.GUEST_DISPLAYNAME_CHANGED`, `EventTypeFilter.GUEST_DISPLAYNAME_CHANGED`, `Guest.displayname_history` relationship.
- Consumes: nothing new (pure additions to existing files).

- [ ] **Step 1: Add `GUEST_DISPLAYNAME_CHANGED` to `EventType`**

In `app/models/models.py`, in the `EventType` class (currently lines 41-59), change:

```python
    GUEST_MAZMO_UNLINKED = "GUEST_MAZMO_UNLINKED"
    PAYMENT_RECORDED = "PAYMENT_RECORDED"
```

to:

```python
    GUEST_MAZMO_UNLINKED = "GUEST_MAZMO_UNLINKED"
    GUEST_DISPLAYNAME_CHANGED = "GUEST_DISPLAYNAME_CHANGED"
    PAYMENT_RECORDED = "PAYMENT_RECORDED"
```

- [ ] **Step 2: Add the `GuestDisplaynameSource` enum**

Immediately after the `EventType` class closes (after the line `PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"` and before the `# ── Role lookup table ──` comment), insert:

```python
class GuestDisplaynameSource(StrEnum):
    """
    Origin of a recorded guest displayname value.

    SYNC: the Mazmo sync detected a different displayname than what we
    had stored, or inserted a brand new guest with this as its first
    value. MANUAL_EDIT: a staff/admin actively set it - either by
    editing an existing guest via PATCH /guests/{id}, or by registering
    a new one via POST /guests/mazmo or POST /guests/manual (there is no
    better-fitting source for "a human set this value directly").
    MAZMO_LINK: the value was set/overwritten by linking a Mazmo profile
    to a guest via PATCH /guests/{id}/link-mazmo. BACKFILL: historical
    entry created by the migration that introduced this table, for
    guests that already existed at that point; recorded_at is the
    migration's run time, not the guest's real creation time, because
    Guest has no created_at field to recover it from.
    """

    SYNC = "SYNC"
    MANUAL_EDIT = "MANUAL_EDIT"
    MAZMO_LINK = "MAZMO_LINK"
    BACKFILL = "BACKFILL"
```

- [ ] **Step 3: Add the `GuestDisplaynameHistory` table and `Guest.displayname_history` relationship**

In `app/models/models.py`, find the `Guest` class (currently lines 228-263). In its body, change:

```python
    org_bans: list["OrganizationBan"] = Relationship(back_populates="guest")
```

to:

```python
    org_bans: list["OrganizationBan"] = Relationship(back_populates="guest")
    displayname_history: list["GuestDisplaynameHistory"] = Relationship(back_populates="guest")
```

Then, after the `OrganizationBan` class closes (after its `banned_by: Optional["User"] = Relationship()` line, and before the `# ── Event Log ──` comment block), insert a new section:

```python
# ── Guest Displayname History ────────────────────────────────────────────────


class GuestDisplaynameHistory(SQLModel, table=True):
    """
    Full timeline of every displayname value a guest has had.

    One row per value (including the first one, at creation), not
    before/after pairs - reconstructing "changed from X to Y" means
    looking at the previous row by recorded_at. source is stored as str
    (not the GuestDisplaynameSource StrEnum directly), same convention
    as EventType/role/guest_type elsewhere in this codebase: these
    domain enums are not mapped to a native Postgres ENUM type.

    actor_id is NULL for SYNC and BACKFILL rows (no human triggered
    them), and set for MANUAL_EDIT/MAZMO_LINK rows.

    Closest shape precedent in this codebase: OrganizationBan (int PK,
    guest_id FK, an actor FK, a timestamp) - same structure, unrelated
    to EventLog.
    """

    __tablename__ = "guest_displayname_history"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    guest_id: uuid.UUID = Field(foreign_key="guests.id", index=True)
    displayname: str
    source: str = Field(max_length=16)
    actor_id: int | None = Field(default=None, foreign_key="users.id")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    guest: Guest = Relationship(back_populates="displayname_history")
    actor: Optional["User"] = Relationship()
```

- [ ] **Step 4: Add `GUEST_DISPLAYNAME_CHANGED` to `EventTypeFilter`**

This plan runs after `docs/superpowers/plans/2026-08-12-guest-type-payment-exemption.md`, whose Task 3 Step 3 already added `GUEST_TYPE_CHANGED` to this same class. In `app/schemas/events.py`, in the `EventTypeFilter` class, change:

```python
class EventTypeFilter(StrEnum):
    """
    Event types that can be filtered in queries.

    Maps directly to EventType in models but defined separately for
    schema validation to avoid circular imports.
    """

    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"
    BAN = "BAN"
    UNBAN = "UNBAN"
    GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"
```

to:

```python
class EventTypeFilter(StrEnum):
    """
    Event types that can be filtered in queries.

    Maps directly to EventType in models but defined separately for
    schema validation to avoid circular imports.
    """

    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"
    BAN = "BAN"
    UNBAN = "UNBAN"
    GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"
    GUEST_DISPLAYNAME_CHANGED = "GUEST_DISPLAYNAME_CHANGED"
```

Note: `EventTypeFilter` is exported from `app/schemas/__init__.py` but not actually wired into any router's validation logic today (`app/routers/events.py`'s `_parse_event_types` validates against `EventType` directly, not `EventTypeFilter`) - this addition keeps it in sync with the new `EventType` value, matching the existing (already incomplete, pre-existing, out of scope to fix here) convention.

- [ ] **Step 5: Create the test file with Task 1's tests**

Create `tests/test_guest_displayname_history.py`:

```python
"""
Tests for guest displayname history: the GuestDisplaynameHistory table,
GUEST_DISPLAYNAME_CHANGED events, and GET /guests/{guest_id}/displayname-history.

Sync-specific tests live in tests/test_sync.py (integration, via
TestClient) and tests/test_sync_service.py (unit, direct calls to
GuestSyncer._upsert_guests) instead, matching where the rest of the
sync test suite already lives.
"""

from sqlmodel import Session, select

from app.models.models import EventLog, EventType, GuestDisplaynameSource
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
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Run the full test suite to confirm nothing broke**

Run: `uv run pytest tests/ -v`
Expected: all previously-passing tests still PASS (the new table doesn't affect existing behavior; `SQLModel.metadata.create_all()` in `setup_test_database` picks up the new table automatically since it iterates all registered `SQLModel` subclasses).

- [ ] **Step 8: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 9: Commit**

```bash
git add app/models/models.py app/schemas/events.py tests/test_guest_displayname_history.py
git commit -m "feat: add GuestDisplaynameHistory table, GuestDisplaynameSource enum, GUEST_DISPLAYNAME_CHANGED event type"
```

---

## Task 2: Alembic migration - create table, indexes, backfill

**Files:**
- Create: `alembic/versions/0018_guest_displayname_history.py`
- Test: `tests/test_guest_displayname_history.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks (the migration is self-contained SQL DDL/DML; the test in this task exercises the same SQL directly against the already-existing test schema from Task 1).
- Produces: the `guest_displayname_history` table in any *real* database this migration is applied to (dev/staging/prod). The test database used by `pytest` does NOT get its schema from this migration - see the test's docstring below for why.

- [ ] **Step 1: Create the migration file**

Create `alembic/versions/0018_guest_displayname_history.py`:

```python
"""guest displayname history table

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-12

Adds guest_displayname_history: a full timeline of every displayname
value a guest has had (one row per value, not old/new pairs). Backfills
one BACKFILL row per existing guest using their current displayname,
since Guest has no created_at field to recover a real creation time from.

Follows the 0012_organization_bans.py pattern (create_table with
sa.ForeignKeyConstraint) plus a separate op.create_index for the
composite index, same pattern as the composite index in 0016.

Chained after 0017_guest_type_payment_exemption.py (the guest_type
migration from the prerequisite plan), not directly after 0016.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0018"
down_revision: str = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_displayname_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guest_id", UUID(as_uuid=True), nullable=False),
        sa.Column("displayname", sa.String(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_guest_displayname_history_guest_id", "guest_displayname_history", ["guest_id"])
    op.create_index(
        "ix_guest_displayname_history_guest_recorded",
        "guest_displayname_history",
        ["guest_id", "recorded_at"],
    )

    # Backfill: one BACKFILL row per existing guest, using their current
    # displayname. Unbatched - consistent with how 0016 runs its
    # UPDATE ... FROM statements without chunking at this data volume.
    op.execute("""
        INSERT INTO guest_displayname_history (guest_id, displayname, source, actor_id, recorded_at)
        SELECT id, displayname, 'BACKFILL', NULL, now()
        FROM guests
    """)


def downgrade() -> None:
    op.drop_index("ix_guest_displayname_history_guest_recorded", table_name="guest_displayname_history")
    op.drop_index("ix_guest_displayname_history_guest_id", table_name="guest_displayname_history")
    op.drop_table("guest_displayname_history")
```

- [ ] **Step 2: Apply the migration to the dev database and verify the round trip**

Run: `uv run alembic upgrade head`
Expected: succeeds, no errors. `guest_displayname_history` now exists in the dev DB (`alter_event_tracker`), with one `BACKFILL` row per pre-existing guest.

Run: `uv run alembic downgrade -1`
Expected: succeeds, drops the table cleanly.

Run: `uv run alembic upgrade head` again
Expected: succeeds again (idempotent round trip proves both directions work).

- [ ] **Step 3: Add the backfill-logic test**

First, update the imports at the top of `tests/test_guest_displayname_history.py`. Add `from sqlalchemy import text` (alongside the existing `from sqlmodel import Session, select` line), and add `GuestDisplaynameHistory` to the `app.models.models` import, which should now read:

```python
from app.models.models import EventLog, EventType, GuestDisplaynameHistory, GuestDisplaynameSource
```

Then append to `tests/test_guest_displayname_history.py`:

```python
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

    session.exec(
        text("""
            INSERT INTO guest_displayname_history (guest_id, displayname, source, actor_id, recorded_at)
            SELECT id, displayname, 'BACKFILL', NULL, now()
            FROM guests
            WHERE id IN (:a, :b)
        """).bindparams(a=guest_a.id, b=guest_b.id)
    )
    session.flush()

    for guest in (guest_a, guest_b):
        rows = session.exec(
            select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].source == GuestDisplaynameSource.BACKFILL
        assert rows[0].displayname == guest.displayname
        assert rows[0].actor_id is None
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_guest_displayname_history.py::test_backfill_migration_creates_one_row_per_existing_guest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0018_guest_displayname_history.py tests/test_guest_displayname_history.py
git commit -m "feat: add guest_displayname_history migration with backfill"
```

---

## Task 3: Rewrite the sync's atomic guest upsert

**This is the highest-risk task in this plan.** The atomic `ON CONFLICT ... WHERE ... RETURNING` design below was validated twice before writing this plan, against the real repo (both changes were then reverted - nothing was left applied):

1. **Runtime behavior**, directly against the real test database (`alter_event_tracker_test`): a two-row batch (one guest with a changed displayname, one brand-new guest) correctly returned `was_insert=False` for the existing guest (with its original `id` preserved) and `was_insert=True` for the new one; re-running the identical statement a second time returned zero rows (proving the no-op case matches the previous `DO NOTHING` behavior).
2. **Static types**, by temporarily applying this task's exact model and sync.py changes and running `uv run basedpyright` in strict mode. The first attempt (referencing `Guest.displayname`/`Guest.id` directly in the `where=`/`.returning()` clauses) failed with `reportAttributeAccessIssue`/`reportArgumentType` errors - SQLModel's class-level field annotations describe the Pydantic instance type (`str`, `uuid.UUID`) for basedpyright, not the SQLAlchemy `Column`, so `Guest.displayname.is_distinct_from(...)` type-checks as calling `.is_distinct_from()` on a `str`. The fix (referencing `Guest.__table__.c.displayname`/`Guest.__table__.c.id` instead, shown below) was confirmed to produce zero basedpyright errors, and was re-verified against the real test database to still produce byte-identical runtime results to the first (working but untyped) version.

**IMPORTANT - read before touching this file:** `_fetch_guest_id_map()` (current lines 133-146) is the helper that resolves `mazmo_user_id -> internal guest.id` for **every guest in the sync batch**, not just the ones that changed. It runs via a plain `SELECT ... WHERE mazmo_user_id IN (...)` **after** `_upsert_guests()`, and it is used by `_build_rsvps()` to build every `MeetupRsvp` in the batch. This task does **not** modify `_fetch_guest_id_map()` at all - the rewritten `_upsert_guests()` only returns rows for guests that were inserted or changed (via `RETURNING`), which is NOT sufficient to resolve every guest's id (unchanged guests never appear in `RETURNING`). `_fetch_guest_id_map()` must keep running exactly as it does today, immediately after `_upsert_guests()`, to cover the full batch. **A future plan (Mazmo profile fields) that needs guest-id resolution for a full sync batch should keep depending on `_fetch_guest_id_map()` - it is unchanged and still the correct helper for that.**

**Files:**
- Modify: `app/services/sync.py:176-191` (`_upsert_guests`), `app/services/sync.py:265-267` (`_count_guests`, removed)
- Test: `tests/test_sync_service.py` (append), `tests/test_sync.py` (append)

**Interfaces:**
- Consumes: `GuestDisplaynameHistory`, `GuestDisplaynameSource`, `EventType.GUEST_DISPLAYNAME_CHANGED` (Task 1).
- Produces: `_upsert_guests(self, guests: list[Guest]) -> None` (signature changes from `-> int` to `-> None`; the previous return value was never consumed by `sync()` or any test - verified by repo-wide grep before writing this plan). `_fetch_guest_id_map()` is unchanged (see note above).

- [ ] **Step 1: Update the module docstring and imports in `app/services/sync.py`**

Change the top-of-file docstring (currently lines 1-17) from:

```python
"""
Sync service - orchestrates the full guest list refresh for a specific meetup.

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
---------------
Guest table:
  INSERT ... ON CONFLICT (mazmo_user_id) DO NOTHING - identity data is immutable.

MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).
"""

import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, func, select

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.models.models import Guest, Meetup, MeetupRsvp
from app.schemas import MazmoRsvpEntry, MazmoUserEntry, SyncResponse
from app.services.mazmo import MazmoClient
```

to:

```python
"""
Sync service - orchestrates the full guest list refresh for a specific meetup.

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
---------------
Guest table:
  INSERT ... ON CONFLICT (mazmo_user_id) DO UPDATE SET displayname = ...
  WHERE guests.displayname IS DISTINCT FROM EXCLUDED.displayname
  RETURNING id, displayname, (xmax = 0) AS was_insert - atomic against
  concurrent syncs of different meetups sharing the same guest. Only
  displayname is ever updated; mazmo_handle and other identity fields
  never change via sync. Every row that appears in RETURNING (new
  insert or real displayname change) gets a GuestDisplaynameHistory row.
  Rows where was_insert is False (an existing guest's value changed)
  also get an EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None) - a
  brand new guest's first value is not a "change" worth an audit event.

MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, func, select

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    Meetup,
    MeetupRsvp,
)
from app.schemas import MazmoRsvpEntry, MazmoUserEntry, SyncResponse
from app.services.mazmo import MazmoClient
```

- [ ] **Step 2: Replace `_upsert_guests()` and remove `_count_guests()`**

Replace the current `_upsert_guests` method (lines 176-191):

```python
    def _upsert_guests(self, guests: list[Guest]) -> int:
        """
        Insert new guests via Postgres ON CONFLICT DO NOTHING.
        Returns the number of rows actually inserted.
        """
        if not guests:
            return 0

        rows = [g.model_dump(exclude={"rsvps", "meetups"}) for g in guests]
        count_before = self._count_guests()

        stmt = pg_insert(Guest).values(rows).on_conflict_do_nothing(index_elements=["mazmo_user_id"])
        self._session.exec(stmt)  # type: ignore[arg-type]
        self._session.commit()

        return self._count_guests() - count_before
```

with:

```python
    def _upsert_guests(self, guests: list[Guest]) -> None:
        """
        Upsert guests via Postgres ON CONFLICT DO UPDATE, atomic against
        concurrent syncs of different meetups sharing the same guest
        (Guest is a global, not per-org, table).

        Only displayname is ever updated on conflict - mazmo_handle and
        other identity fields are immutable via sync. Guests whose
        displayname is unchanged are excluded by the WHERE clause and
        never appear in RETURNING, so they get no history/event row -
        same observable behavior as the previous ON CONFLICT DO NOTHING
        for that case.

        Every row that DOES appear in RETURNING gets a
        GuestDisplaynameHistory row with source=SYNC. Only rows where
        was_insert is False (the guest already existed) additionally
        get an EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None) - a
        brand new guest's first displayname is not a "change".
        """
        if not guests:
            return

        rows = [g.model_dump(exclude={"rsvps", "meetups"}) for g in guests]

        # Referencing the underlying sa.Table (Guest.__table__) instead of
        # Guest.displayname/Guest.id directly: SQLModel's class-level field
        # annotations describe the Pydantic instance type (str, uuid.UUID)
        # for basedpyright's benefit, not the SQLAlchemy Column - using them
        # directly in a Core where=/returning() clause type-checks as "str
        # has no attribute is_distinct_from". Guest.__table__ itself has no
        # static stub either, hence the one type: ignore below - this was
        # verified to produce zero basedpyright errors and identical runtime
        # behavior to referencing Guest.displayname/Guest.id directly.
        table = Guest.__table__  # type: ignore[attr-defined]
        stmt = pg_insert(Guest).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["mazmo_user_id"],
            set_={"displayname": stmt.excluded.displayname},
            where=table.c.displayname.is_distinct_from(stmt.excluded.displayname),
        ).returning(
            table.c.id,
            table.c.displayname,
            literal_column("(xmax = 0)").label("was_insert"),
        )
        changed_rows = self._session.exec(stmt).all()  # type: ignore[arg-type]

        for guest_id, displayname, was_insert in changed_rows:
            now = datetime.now(UTC)
            self._session.add(
                GuestDisplaynameHistory(
                    guest_id=guest_id,
                    displayname=displayname,
                    source=GuestDisplaynameSource.SYNC,
                    actor_id=None,
                    recorded_at=now,
                )
            )
            if not was_insert:
                self._session.add(
                    EventLog(
                        event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                        org_id=None,
                        actor_id=None,
                        guest_id=guest_id,
                        timestamp=now,
                        reason=f"Displayname changed to '{displayname}' via Mazmo sync",
                    )
                )

        self._session.commit()
```

Then delete the now-unused `_count_guests` method entirely (currently lines 265-267):

```python
    def _count_guests(self) -> int:
        """Returns the current total number of guests in the database."""
        return self._session.exec(select(func.count()).select_from(Guest)).one()
```

Note: `func` remains used elsewhere in the file (by `_count_rsvps`), so keep the `from sqlmodel import Session, func, select` import line as-is.

- [ ] **Step 3: Run the existing sync test suite to confirm nothing broke**

Run: `uv run pytest tests/test_sync.py tests/test_sync_service.py -v`
Expected: all existing tests still PASS (the rewrite preserves the "unchanged guest = no-op" and "new guest = inserted" behaviors those tests rely on).

- [ ] **Step 4: Add the unit-level tests to `tests/test_sync_service.py`**

`tests/test_sync_service.py` currently has no `app.models.models` import at all, and imports only `Session` (not `select`) from `sqlmodel`. Change:

```python
from sqlmodel import Session
```

to:

```python
from sqlmodel import Session, select
```

and add a new import line (e.g. right after the existing `from app.domain_types import MazmoUserId` line):

```python
from app.models.models import EventLog, EventType, Guest, GuestDisplaynameHistory, GuestDisplaynameSource
```

`make_guest` is already imported via the existing `from tests.conftest import make_guest, make_meetup, make_org, make_rsvp` line - no change needed there.

Then append these tests to the file:

```python
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
```

- [ ] **Step 5: Run the new unit tests**

Run: `uv run pytest tests/test_sync_service.py -v`
Expected: all PASS, including the 3 new tests.

- [ ] **Step 6: Add the integration tests to `tests/test_sync.py`**

At the top of `tests/test_sync.py`, change the models import from:

```python
from app.models.models import Guest, Meetup, MeetupRsvp, Organization, OrgRole
```

to:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    Meetup,
    MeetupRsvp,
    Organization,
    OrgRole,
)
```

Then append these 6 tests (they rely on `mock_mazmo`'s fixed `FAKE_RSVPS`/`FAKE_USERS` data from `tests/conftest.py`: user 111 is "alice"/"Alice", user 222 is "bob"/"Bob"):

```python
# -- Displayname history via sync -----------------------------------------------


def test_sync_updates_displayname_when_mazmo_reports_different_value(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that sync updates Guest.displayname when Mazmo reports a
    different value than what we have stored.

    WHY: Before this change, sync used ON CONFLICT DO NOTHING and never
    reflected a Mazmo displayname change after the guest's first sync -
    see the Guest model's own docstring, which called this out.
    """
    make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    assert guest.displayname == "Alice"


def test_sync_does_not_update_displayname_when_unchanged(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync creates no history row or event when the displayname is
    already up to date.

    WHY: Regression guard - this is the noisy case the atomic
    IS DISTINCT FROM WHERE clause exists to avoid.
    """
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Alice")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []
    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert events == []


def test_sync_creates_history_row_with_source_sync_on_change_to_existing_guest(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """Verify the GuestDisplaynameHistory row written for a changed existing guest."""
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.SYNC
    assert history[0].displayname == "Alice"
    assert history[0].actor_id is None


def test_sync_creates_eventlog_for_change_to_existing_guest_with_org_id_null(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """Verify the GUEST_DISPLAYNAME_CHANGED event for a changed existing guest has org_id=None."""
    guest = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert event.org_id is None


def test_sync_new_guest_creates_history_row_but_no_eventlog(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a brand-new guest created by sync gets a history row but no
    GUEST_DISPLAYNAME_CHANGED event.

    WHY: A first value is not a "change" - logging it as one would flood
    the org timeline with noise on every large sync.
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    guest = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert len(history) == 1
    assert history[0].source == GuestDisplaynameSource.SYNC

    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert events == []


def test_sync_batch_with_mixed_new_and_changed_guests(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a single sync with one changed existing guest and one brand
    new guest gives each the correct treatment.

    WHY: mock_mazmo's FAKE data always returns both user 111 (alice) and
    222 (bob) in one batch - pre-seeding only 111 makes 111 the "changed"
    case and 222 the "new" case within the same sync call, exercising
    both branches of the RETURNING loop in one statement.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Old Alice Name")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    bob = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()

    alice_history = session.exec(
        select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == alice.id)
    ).all()
    bob_history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == bob.id)).all()
    assert len(alice_history) == 1
    assert len(bob_history) == 1

    alice_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == alice.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    bob_events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == bob.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(alice_events) == 1
    assert bob_events == []
```

- [ ] **Step 7: Run the new integration tests**

Run: `uv run pytest tests/test_sync.py -v`
Expected: all PASS, including the 6 new tests.

- [ ] **Step 8: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors (the `# type: ignore[arg-type]` on `self._session.exec(stmt)` matches the existing convention already used elsewhere in this same file for raw `pg_insert` statements).

- [ ] **Step 9: Commit**

```bash
git add app/services/sync.py tests/test_sync.py tests/test_sync_service.py
git commit -m "feat: sync writes displayname history and updates changed displaynames atomically"
```

---

## Task 4: `PATCH /guests/{id}` writes history on real changes

**Files:**
- Modify: `app/routers/guests.py:16-52` (imports), `app/routers/guests.py:485-526` (`update_guest`)
- Test: `tests/test_guest_displayname_history.py` (append)

**Interfaces:**
- Consumes: `GuestDisplaynameHistory`, `GuestDisplaynameSource`, `EventType.GUEST_DISPLAYNAME_CHANGED` (Task 1).
- Produces: nothing new consumed by later tasks (this is a leaf change to one endpoint).

- [ ] **Step 1: Update imports in `app/routers/guests.py`**

Change:

```python
import uuid
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import EventLog, EventType, Guest, OrganizationBan, User
```

to:

```python
import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    OrganizationBan,
    User,
)
```

- [ ] **Step 2: Rewrite `update_guest`**

Replace the current `update_guest` function (lines 485-526):

```python
@router.patch(
    "/{guest_id}",
    response_model=GuestPublic,
    summary="Edit a guest's displayname and/or Instagram handle",
    responses=UPDATE_GUEST_RESPONSES,
)
async def update_guest(
    guest_id: uuid.UUID,
    payload: Annotated[UpdateGuestRequest, Body(openapi_examples=UPDATE_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Edit a guest's displayname and/or instagram_username.

    A key omitted from the request body is left untouched. A key sent
    explicitly as null clears it - but only for instagram_username.
    displayname is typed str | None on the request schema, so the schema
    itself accepts {"displayname": null} without a 422; it is this
    function's own guard (payload.displayname is not None) that silently
    ignores an explicit null for displayname instead of clearing it,
    because Guest.displayname is non-nullable and cannot actually be
    cleared. This distinction uses payload.model_fields_set, since
    payload.instagram_username is None in both the "omitted" and
    "explicitly cleared" cases.

    mazmo_user_id and mazmo_handle cannot be changed here - use
    link-mazmo/unlink-mazmo for that. This is a cosmetic edit, not an
    audited business event, so no event_log entry is written.
    """
    guest = _get_guest_or_404(session, guest_id)

    if "displayname" in payload.model_fields_set and payload.displayname is not None:
        guest.displayname = payload.displayname
    if "instagram_username" in payload.model_fields_set:
        guest.instagram_username = payload.instagram_username

    session.add(guest)
    session.commit()
    session.refresh(guest)

    return guest
```

with:

```python
@router.patch(
    "/{guest_id}",
    response_model=GuestPublic,
    summary="Edit a guest's displayname and/or Instagram handle",
    responses=UPDATE_GUEST_RESPONSES,
)
async def update_guest(
    guest_id: uuid.UUID,
    payload: Annotated[UpdateGuestRequest, Body(openapi_examples=UPDATE_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Edit a guest's displayname and/or instagram_username.

    A key omitted from the request body is left untouched. A key sent
    explicitly as null clears it - but only for instagram_username.
    displayname is typed str | None on the request schema, so the schema
    itself accepts {"displayname": null} without a 422; it is this
    function's own guard (payload.displayname is not None) that silently
    ignores an explicit null for displayname instead of clearing it,
    because Guest.displayname is non-nullable and cannot actually be
    cleared. This distinction uses payload.model_fields_set, since
    payload.instagram_username is None in both the "omitted" and
    "explicitly cleared" cases.

    mazmo_user_id and mazmo_handle cannot be changed here - use
    link-mazmo/unlink-mazmo for that.

    A real displayname change (new value differs from the current one)
    writes a GuestDisplaynameHistory row (source=MANUAL_EDIT) and an
    EventLog(GUEST_DISPLAYNAME_CHANGED) entry, in the same commit as the
    guest update. Omitting displayname, or "changing" it to its current
    value, writes neither - only a real change is audited.
    """
    guest = _get_guest_or_404(session, guest_id)

    if "displayname" in payload.model_fields_set and payload.displayname is not None:
        if payload.displayname != guest.displayname:
            old_displayname = guest.displayname
            guest.displayname = payload.displayname
            now = datetime.now(UTC)
            session.add(
                GuestDisplaynameHistory(
                    guest_id=guest.id,
                    displayname=guest.displayname,
                    source=GuestDisplaynameSource.MANUAL_EDIT,
                    actor_id=staff.id,
                    recorded_at=now,
                )
            )
            session.add(
                EventLog(
                    event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                    org_id=None,
                    actor_id=staff.id,
                    guest_id=guest.id,
                    timestamp=now,
                    reason=f"Displayname changed from '{old_displayname}' to '{guest.displayname}'",
                )
            )
    if "instagram_username" in payload.model_fields_set:
        guest.instagram_username = payload.instagram_username

    session.add(guest)
    session.commit()
    session.refresh(guest)

    return guest
```

- [ ] **Step 3: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py -v`
Expected: all existing tests still PASS, including the ones that already exercise `PATCH /guests/{id}` (they don't assert on `GuestDisplaynameHistory`/`EventLog`, so they are unaffected by the addition).

- [ ] **Step 4: Add the new tests**

First, add two new imports to the top of `tests/test_guest_displayname_history.py` (needed by every HTTP-level test from this task onward): `from fastapi import status` and `from fastapi.testclient import TestClient`, placed above the existing `from sqlalchemy import text` line.

Then append to `tests/test_guest_displayname_history.py`:

```python
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
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: all PASS, including the 5 new tests.

- [ ] **Step 6: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add app/routers/guests.py tests/test_guest_displayname_history.py
git commit -m "feat: PATCH /guests/{id} writes displayname history and event on real changes"
```

---

## Task 5: `PATCH /guests/{id}/link-mazmo` writes history on real changes

**Files:**
- Modify: `app/routers/guests.py:284-384` (`link_guest_to_mazmo`)
- Test: `tests/test_guest_displayname_history.py` (append)

**Interfaces:**
- Consumes: `GuestDisplaynameHistory`, `GuestDisplaynameSource`, `EventType.GUEST_DISPLAYNAME_CHANGED`, `datetime`/`UTC` imports (already added to `app/routers/guests.py` in Task 4 - no further import changes needed in this task).

- [ ] **Step 1: Rewrite `link_guest_to_mazmo`**

Replace the current function body from the `guest.mazmo_user_id = mazmo_user.mazmo_user_id` line through the `session.refresh(guest)` line (currently within lines 349-375):

```python
    guest.mazmo_user_id = mazmo_user.mazmo_user_id
    guest.mazmo_handle = mazmo_user.username
    guest.displayname = mazmo_user.displayname

    event = EventLog(
        event_type=EventType.GUEST_MAZMO_LINKED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    session.add(guest)
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        # Race: another request linked the same mazmo_user_id first,
        # between our pre-check SELECT above and this commit.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: mazmo_user_id={mazmo_user.mazmo_user_id} was linked to "
                f"another guest by a concurrent request. Look it up via "
                f"GET /guests/by-mazmo-handle/{mazmo_user.username} to see who has it now."
            ),
        ) from None
    session.refresh(guest)
```

with:

```python
    old_displayname = guest.displayname
    guest.mazmo_user_id = mazmo_user.mazmo_user_id
    guest.mazmo_handle = mazmo_user.username
    guest.displayname = mazmo_user.displayname

    now = datetime.now(UTC)
    event = EventLog(
        event_type=EventType.GUEST_MAZMO_LINKED,
        actor_id=staff.id,
        guest_id=guest.id,
        timestamp=now,
    )

    session.add(guest)
    session.add(event)

    if guest.displayname != old_displayname:
        session.add(
            GuestDisplaynameHistory(
                guest_id=guest.id,
                displayname=guest.displayname,
                source=GuestDisplaynameSource.MAZMO_LINK,
                actor_id=staff.id,
                recorded_at=now,
            )
        )
        session.add(
            EventLog(
                event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                org_id=None,
                actor_id=staff.id,
                guest_id=guest.id,
                timestamp=now,
                reason=f"Displayname changed from '{old_displayname}' to '{guest.displayname}'",
            )
        )

    try:
        session.commit()
    except IntegrityError:
        # Race: another request linked the same mazmo_user_id first,
        # between our pre-check SELECT above and this commit.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: mazmo_user_id={mazmo_user.mazmo_user_id} was linked to "
                f"another guest by a concurrent request. Look it up via "
                f"GET /guests/by-mazmo-handle/{mazmo_user.username} to see who has it now."
            ),
        ) from None
    session.refresh(guest)
```

Also update the function's docstring (directly above this block) to mention the new behavior - change:

```python
    """
    Attach a Mazmo account to a guest created without one.

    Overwrites mazmo_user_id, mazmo_handle, and displayname with the
    Mazmo profile data. instagram_username is left untouched.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already linked, or if the Mazmo account
    is already linked to a different guest (no automatic merge).
    """
```

to:

```python
    """
    Attach a Mazmo account to a guest created without one.

    Overwrites mazmo_user_id, mazmo_handle, and displayname with the
    Mazmo profile data. instagram_username is left untouched.

    If the incoming Mazmo displayname differs from the guest's previous
    value, writes a GuestDisplaynameHistory row (source=MAZMO_LINK) and
    an EventLog(GUEST_DISPLAYNAME_CHANGED) entry, alongside the existing
    EventLog(GUEST_MAZMO_LINKED) - same commit, same timestamp.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already linked, or if the Mazmo account
    is already linked to a different guest (no automatic merge).
    """
```

- [ ] **Step 2: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 3: Add the new tests**

First, add `from types import SimpleNamespace` to the imports at the top of `tests/test_guest_displayname_history.py` (needed by two of the tests below, to override `mock_mazmo_for_guests`'s default return value with an ASCII-only displayname).

Then append to `tests/test_guest_displayname_history.py`:

```python
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
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: all PASS, including the 4 new tests.

- [ ] **Step 5: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add app/routers/guests.py tests/test_guest_displayname_history.py
git commit -m "feat: PATCH /guests/{id}/link-mazmo writes displayname history and event on real changes"
```

---

## Task 6: Guest creation endpoints write an initial history row

Implements design decision #2 from the "Design decisions this plan makes" section above: `POST /guests/mazmo` and `POST /guests/manual` each write an initial `GuestDisplaynameHistory` row (source=`MANUAL_EDIT`) at creation time, with no `EventLog(GUEST_DISPLAYNAME_CHANGED)` (only the existing `EventLog(GUEST_CREATED)`).

**Files:**
- Modify: `app/routers/guests.py:75-153` (`create_guest_from_mazmo`), `app/routers/guests.py:159-201` (`create_manual_guest`)
- Test: `tests/test_guest_displayname_history.py` (append)

- [ ] **Step 1: Rewrite `create_guest_from_mazmo`**

Change:

```python
    guest = Guest(
        mazmo_user_id=mazmo_user.mazmo_user_id,
        mazmo_handle=mazmo_user.username,
        displayname=mazmo_user.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)
```

to:

```python
    guest = Guest(
        mazmo_user_id=mazmo_user.mazmo_user_id,
        mazmo_handle=mazmo_user.username,
        displayname=mazmo_user.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )
    history = GuestDisplaynameHistory(
        guest_id=guest.id,
        displayname=guest.displayname,
        source=GuestDisplaynameSource.MANUAL_EDIT,
        actor_id=staff.id,
    )

    session.add(guest)
    session.add(event)
    session.add(history)
    session.commit()
    session.refresh(guest)
```

- [ ] **Step 2: Rewrite `create_manual_guest`**

Change:

```python
    guest = Guest(
        displayname=request.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)
```

to:

```python
    guest = Guest(
        displayname=request.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )
    history = GuestDisplaynameHistory(
        guest_id=guest.id,
        displayname=guest.displayname,
        source=GuestDisplaynameSource.MANUAL_EDIT,
        actor_id=staff.id,
    )

    session.add(guest)
    session.add(event)
    session.add(history)
    session.commit()
    session.refresh(guest)
```

- [ ] **Step 3: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 4: Add the new tests**

First, add `import uuid` to the top of `tests/test_guest_displayname_history.py` (needed below to parse the `id` string from JSON responses back into a `uuid.UUID` for querying).

Then append to `tests/test_guest_displayname_history.py`:

```python
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
    assert history[0].source == GuestDisplaynameSource.MANUAL_EDIT
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
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: all PASS, including the 3 new tests.

- [ ] **Step 6: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add app/routers/guests.py tests/test_guest_displayname_history.py
git commit -m "feat: guest creation endpoints write an initial displayname history row"
```

---

## Task 7: Response schemas for the history read endpoint

**Files:**
- Modify: `app/schemas/guests.py` (append new schemas), `app/schemas/__init__.py` (re-export)

**Interfaces:**
- Produces: `GuestDisplaynameHistoryPublic` (fields: `displayname: str`, `source: str`, `recorded_at: datetime`, `actor: EventActorPublic | None`), `GuestDisplaynameHistoryListResponse` (fields: `total: int`, `history: list[GuestDisplaynameHistoryPublic]`).
- Consumes: `EventActorPublic` from `app.schemas.events` (already defined there - no circular import risk, since `app/schemas/events.py` only imports from `app.models.models`, `pydantic`, `uuid`, `datetime`, `enum`, never from `app.schemas.guests`).

- [ ] **Step 1: Add the new schemas to `app/schemas/guests.py`**

At the top of `app/schemas/guests.py`, add the import:

```python
from app.schemas.events import EventActorPublic
```

(Place it after the existing `from pydantic import ...` import line.)

At the end of `app/schemas/guests.py`, after the existing `BannedGuestListResponse` class, append:

```python
# -- Displayname history ---------------------------------------------------------


class GuestDisplaynameHistoryPublic(BaseModel):
    """
    One entry in a guest's displayname history.

    A full timeline (one row per value the displayname ever had,
    including the first), not before/after pairs. actor is None for
    SYNC and BACKFILL entries - no human triggered them.
    """

    model_config = ConfigDict(from_attributes=True)

    displayname: str
    source: str
    recorded_at: datetime
    actor: EventActorPublic | None = None


class GuestDisplaynameHistoryListResponse(BaseModel):
    """
    Full displayname history for a guest, newest first.

    Not paginated: a displayname changes rarely over a guest's lifetime,
    so the complete list is always returned.
    """

    total: int
    history: list[GuestDisplaynameHistoryPublic]
```

- [ ] **Step 2: Re-export from `app/schemas/__init__.py`**

This plan runs after `docs/superpowers/plans/2026-08-12-guest-type-payment-exemption.md`, whose Task 3 Step 4 already inserted `GuestTypeUpdateRequest` into this same import block and into `__all__`. Change the `from app.schemas.guests import (...)` block from:

```python
from app.schemas.guests import (
    BanGuestRequest,
    BannedGuestListResponse,
    BannedGuestPublic,
    CheckedInByPublic,
    CheckInResponse,
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestListResponse,
    GuestPublic,
    GuestTypeUpdateRequest,
    GuestWithBanPublic,
    LinkMazmoRequest,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    PaymentResponse,
    RsvpPublic,
    UpdateGuestRequest,
)
```

to:

```python
from app.schemas.guests import (
    BanGuestRequest,
    BannedGuestListResponse,
    BannedGuestPublic,
    CheckedInByPublic,
    CheckInResponse,
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestDisplaynameHistoryListResponse,
    GuestDisplaynameHistoryPublic,
    GuestListResponse,
    GuestPublic,
    GuestTypeUpdateRequest,
    GuestWithBanPublic,
    LinkMazmoRequest,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    PaymentResponse,
    RsvpPublic,
    UpdateGuestRequest,
)
```

Then add both new names to the `__all__` list, in alphabetical order alongside the existing guest-related entries (Plan 1 already inserted `GuestTypeStats` and `GuestTypeUpdateRequest` there, after `GuestPublic` and before `GuestWithBanPublic` - leave those untouched):

```python
    "GuestDisplaynameHistoryListResponse",
    "GuestDisplaynameHistoryPublic",
    "GuestListResponse",
```

(insert the first two lines immediately before the existing `"GuestListResponse",` line - its position in `__all__` is unchanged by Plan 1's edit).

- [ ] **Step 3: Verify the app still imports cleanly**

Run: `uv run python -c "import app.main"`
Expected: no errors (confirms no circular import was introduced).

- [ ] **Step 4: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/guests.py app/schemas/__init__.py
git commit -m "feat: add GuestDisplaynameHistoryPublic and GuestDisplaynameHistoryListResponse schemas"
```

---

## Task 8: `GET /guests/{guest_id}/displayname-history` endpoint

**Files:**
- Modify: `app/routers/guests.py` (imports, new endpoint at the end of the file)
- Modify: `app/openapi_examples/guests_examples.py` (imports, new responses dict)
- Test: `tests/test_guest_displayname_history.py` (append)

**Interfaces:**
- Consumes: `GuestDisplaynameHistory` model (Task 1), `GuestDisplaynameHistoryListResponse`/`GuestDisplaynameHistoryPublic` schemas (Task 7), `_get_guest_or_404` helper (already exists in `app/routers/guests.py`, unchanged).

- [ ] **Step 1: Update imports in `app/routers/guests.py`**

Change the `from sqlmodel import Session, select` line to also import `selectinload` from SQLAlchemy:

```python
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
```

(add the `from sqlalchemy.orm import selectinload` line right after the existing `from sqlalchemy.exc import IntegrityError` line).

Change the `from app.schemas import (...)` block from:

```python
from app.schemas import (
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestListResponse,
    GuestPublic,
    LinkMazmoRequest,
    UpdateGuestRequest,
)
```

to:

```python
from app.schemas import (
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestDisplaynameHistoryListResponse,
    GuestDisplaynameHistoryPublic,
    GuestListResponse,
    GuestPublic,
    LinkMazmoRequest,
    UpdateGuestRequest,
)
from app.schemas.events import EventActorPublic
```

Change the `from app.models.models import (...)` block (already multi-line after Task 4) to also import `GuestDisplaynameHistory` - it should now read:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    OrganizationBan,
    User,
)
```

Change the `from app.openapi_examples.guests_examples import (...)` block to add the new responses constant - it should now read:

```python
from app.openapi_examples.guests_examples import (
    CREATE_MANUAL_GUEST_REQUEST_EXAMPLES,
    CREATE_MANUAL_GUEST_RESPONSES,
    CREATE_MAZMO_GUEST_REQUEST_EXAMPLES,
    CREATE_MAZMO_GUEST_RESPONSES,
    GET_DISPLAYNAME_HISTORY_RESPONSES,
    GET_GUEST_BY_MAZMO_HANDLE_RESPONSES,
    GET_GUEST_RESPONSES,
    LINK_MAZMO_REQUEST_EXAMPLES,
    LINK_MAZMO_RESPONSES,
    LIST_GUESTS_RESPONSES,
    UNLINK_MAZMO_RESPONSES,
    UPDATE_GUEST_REQUEST_EXAMPLES,
    UPDATE_GUEST_RESPONSES,
)
```

- [ ] **Step 2: Add the OpenAPI responses constant**

In `app/openapi_examples/guests_examples.py`, at the end of the file (after the existing `UPDATE_GUEST_RESPONSES` block), append:

```python
# -- GET /guests/{guest_id}/displayname-history -----------------------------------

GET_DISPLAYNAME_HISTORY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest's full displayname history, newest first",
        "content": {
            "application/json": {
                "examples": {
                    "history": {
                        "summary": "A guest whose name changed twice",
                        "value": {
                            "total": 3,
                            "history": [
                                {
                                    "displayname": "Juan P.",
                                    "source": "SYNC",
                                    "recorded_at": TIMESTAMP_2024_03_23,
                                    "actor": None,
                                },
                                {
                                    "displayname": "Juan Perez",
                                    "source": "MANUAL_EDIT",
                                    "recorded_at": TIMESTAMP_2024_03_20,
                                    "actor": {"id": 2, "username": "carlos_staff"},
                                },
                                {
                                    "displayname": "Juan",
                                    "source": "SYNC",
                                    "recorded_at": TIMESTAMP_2024_03_15,
                                    "actor": None,
                                },
                            ],
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}
```

This uses `TIMESTAMP_2024_03_23`, `TIMESTAMP_2024_03_20`, and `TIMESTAMP_2024_03_15`, all of which already exist in `app/openapi_examples/_constants.py` - add them to the existing `from app.openapi_examples._constants import (...)` import block at the top of `app/openapi_examples/guests_examples.py`, which currently reads:

```python
from app.openapi_examples._constants import (
    GUEST_MANUAL,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
)
```

change it to:

```python
from app.openapi_examples._constants import (
    GUEST_MANUAL,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
    TIMESTAMP_2024_03_15,
    TIMESTAMP_2024_03_20,
    TIMESTAMP_2024_03_23,
)
```

- [ ] **Step 3: Add the endpoint**

At the end of `app/routers/guests.py` (after the `update_guest` function), append:

```python
# -- Get a guest's displayname history ---------------------------------------------


@router.get(
    "/{guest_id}/displayname-history",
    response_model=GuestDisplaynameHistoryListResponse,
    summary="Get a guest's full displayname history",
    responses=GET_DISPLAYNAME_HISTORY_RESPONSES,
)
async def get_guest_displayname_history(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> GuestDisplaynameHistoryListResponse:
    """
    Get the full displayname history for a guest, newest first.

    Not paginated: a displayname changes rarely over a guest's lifetime,
    so the complete list is always returned. A guest with no changes
    since creation still has exactly one row (its initial value) - never
    an empty list, since every guest creation path writes one.

    Global guest endpoint, same as GET /guests/{guest_id} - not scoped
    to an organization.
    """
    _get_guest_or_404(session, guest_id)

    rows = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest_id)
        .options(selectinload(GuestDisplaynameHistory.actor))  # type: ignore[arg-type]
        .order_by(GuestDisplaynameHistory.recorded_at.desc())  # type: ignore[union-attr]
    ).all()

    return GuestDisplaynameHistoryListResponse(
        total=len(rows),
        history=[
            GuestDisplaynameHistoryPublic(
                displayname=row.displayname,
                source=row.source,
                recorded_at=row.recorded_at,
                actor=EventActorPublic.model_validate(row.actor) if row.actor else None,
            )
            for row in rows
        ],
    )
```

- [ ] **Step 4: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 5: Add the new tests**

Append to `tests/test_guest_displayname_history.py`:

```python
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
```

- [ ] **Step 6: Run the new tests**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: all PASS, including the 6 new tests.

- [ ] **Step 7: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add app/routers/guests.py app/openapi_examples/guests_examples.py tests/test_guest_displayname_history.py
git commit -m "feat: add GET /guests/{guest_id}/displayname-history endpoint"
```

---

## Task 9: End-to-end multi-endpoint tests

**Files:**
- Test: `tests/test_guest_displayname_history.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-8 (this task only adds tests, no source changes).

- [ ] **Step 1: Add the E2E tests**

Append to `tests/test_guest_displayname_history.py`:

```python
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


def test_link_mazmo_with_name_change_end_to_end(client: TestClient, staff_headers: dict, session: Session, mock_mazmo_for_guests):
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
```

These tests need `TestClient` and `status` (already imported, from Task 4), `SimpleNamespace` (already imported, from Task 5), and `uuid` (already imported, from Task 6), plus `client`/`admin_headers`/`meetup`/`mock_mazmo` fixtures from `tests/conftest.py` (already available as fixtures - no import needed for those). Three names are still missing at this point, so add them now:

1. Add `from unittest.mock import AsyncMock` as a new top-level import line.
2. Add `Guest` and `Meetup` to the existing `from app.models.models import (...)` block, which should now read:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    Meetup,
)
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_guest_displayname_history.py -v`
Expected: all PASS, including the 3 new E2E tests.

- [ ] **Step 3: Commit**

```bash
git add tests/test_guest_displayname_history.py
git commit -m "test: add end-to-end scenarios for guest displayname history"
```

---

## Task 10: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: every test passes, including all new tests added across Tasks 1-9 and every pre-existing test in the repo.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check app/ tests/ alembic/`
Expected: no errors.

Run: `uv run ruff format --check app/ tests/ alembic/`
Expected: no files need reformatting. If any do, run `uv run ruff format app/ tests/ alembic/` and re-verify.

- [ ] **Step 3: Run basedpyright**

Run: `uv run basedpyright`
Expected: no errors.

- [ ] **Step 4: Re-verify the migration round trip against the dev database**

Run: `uv run alembic downgrade -1`
Run: `uv run alembic upgrade head`
Expected: both succeed cleanly (final confirmation that Task 2's migration is still consistent with the final state of the model after Tasks 3-9).

- [ ] **Step 5: Scan all new/modified files for non-ASCII characters**

Run: `grep -rnP '[^\x00-\x7F]' app/models/models.py app/schemas/events.py app/schemas/guests.py app/schemas/__init__.py app/services/sync.py app/routers/guests.py app/openapi_examples/guests_examples.py alembic/versions/0018_guest_displayname_history.py tests/test_guest_displayname_history.py tests/test_sync.py tests/test_sync_service.py`
Expected: no output (empty result = fully ASCII, per CLAUDE.md rule 9). If anything is found outside of test assertions that intentionally reference pre-existing fixture data (there should be none, per the design decisions in Task 5's tests, which deliberately avoid the existing Unicode fixture value), fix it.

- [ ] **Step 6: Manual smoke test (optional but recommended)**

Start the dev server (`dev-backend` devenv command, or `uv run fastapi dev app/main.py`), open `/docs`, and confirm:
- `GET /guests/{guest_id}/displayname-history` appears under the `guests` tag with the new example response.
- `PATCH /guests/{guest_id}` and `PATCH /guests/{guest_id}/link-mazmo` docs still render correctly.

No commit for this task (verification only, no file changes expected unless Step 2 found formatting issues).
