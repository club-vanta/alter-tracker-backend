# Guest Type Payment Exemption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-RSVP `guest_type` category (NORMAL/INVITED/VENDOR/STAFF) that exempts non-NORMAL guests from the meetup payment check-in gate, plus an admin endpoint to set it and a stats endpoint to see counts per category.

**Architecture:** One new column on `MeetupRsvp` (`guest_type`, stored as VARCHAR like the existing `EventType`/`OrgRole` string-backed enums), one new admin-only PATCH endpoint to change it with an audit-trail entry, a one-line change to the existing check-in payment gate, and one new read-only GET endpoint that aggregates counts with a single query over `MeetupRsvp` rows.

**Tech Stack:** FastAPI, SQLModel (Pydantic v2 + SQLAlchemy), Alembic, PostgreSQL, structlog, pytest with a real Postgres test database (never mocked).

## Global Constraints

- ASCII only in all code, comments, docstrings, and this plan (CLAUDE.md rule 9) - no em-dashes, no Unicode arrows, no accented characters in anything you write. Use `-` instead of an em-dash and `->` instead of a Unicode arrow.
- Never mock the database in tests - all tests use the real Postgres test database via the `session`/`client` fixtures in `tests/conftest.py`.
- Every state-changing endpoint that should be audited creates an `EventLog` row in the *same commit* as the state change (CLAUDE.md "Audit trail atomico").
- Use `.with_for_update()` when a later write depends on a row you just read, matching the existing check-in/payment endpoints.
- Test naming: `test_<action>_<condition>_<expected_result>`.
- Run `basedpyright` and `ruff check` before considering any task's code changes final; run `ruff format` on any file you edit.
- Services (`app/services/`) must never import from `fastapi`. This plan does not touch `app/services/mazmo.py`, and only touches `app/services/sync.py`'s docstring (no logic change), so this constraint is not at risk here but is listed for completeness.

---

## Task 1: Data model - GuestType enum, MeetupRsvp.guest_type, EventType.GUEST_TYPE_CHANGED

**Files:**
- Modify: `app/models/models.py:41-60` (EventType enum), `app/models/models.py:183-225` (MeetupRsvp)
- Modify: `app/services/sync.py:1-17` (module docstring only, no logic change)
- Modify: `CLAUDE.md` (rule 2 in "Reglas importantes")
- Modify: `tests/conftest.py:306-333` (`make_rsvp` helper - add `guest_type` param)
- Test: `tests/test_guest_type.py` (new file, created in this task with its first test)

**Interfaces:**
- Produces: `GuestType(StrEnum)` with members `NORMAL`, `INVITED`, `VENDOR`, `STAFF`, importable as `from app.models.models import GuestType`.
- Produces: `MeetupRsvp.guest_type: str`, default `"NORMAL"`.
- Produces: `EventType.GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"`.
- Produces: `make_rsvp(session, *, meetup, guest, ..., guest_type: str = "NORMAL")` in `tests/conftest.py` - all later tasks that need a non-default guest_type on a test RSVP call this with `guest_type="VENDOR"` etc.

- [ ] **Step 1: Add the `GuestType` enum to `app/models/models.py`**

Insert this new class immediately after the closing of the `EventType` class (after the line `    PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"` and before the `# ── Role lookup table` comment divider):

```python
class GuestType(StrEnum):
    """Category of a guest's attendance at a specific meetup.

    NORMAL guests are subject to the meetup's requires_payment flag like
    any regular attendee. INVITED, VENDOR, and STAFF guests are exempt
    from the payment check-in gate regardless of has_paid: invited guests
    were personally invited by the meetup organizer and don't pay entry,
    vendors bring their own stand to sell goods and aren't attending as
    participants, and staff are working the event itself. This is set
    per-RSVP (not on the Guest) because these categories are decided
    event by event, not a persistent trait of the person.
    """

    NORMAL = "NORMAL"
    INVITED = "INVITED"
    VENDOR = "VENDOR"
    STAFF = "STAFF"
```

- [ ] **Step 2: Add `GUEST_TYPE_CHANGED` to `EventType`**

In the `EventType` class, add a new line after `PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"`:

```python
    PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"
    GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"
```

- [ ] **Step 3: Add the `guest_type` field to `MeetupRsvp` and update its docstring**

Replace the `MeetupRsvp` docstring:

```python
    """
    Association object representing a Guest's attendance at a specific Meetup.

    CRITICAL: The background sync upsert NEVER overwrites has_arrived,
    arrival_time, arrival_order, checked_in_by_id, has_paid, paid_at, or
    paid_by_id. These are set ONLY by the door tracker check-in and
    payment flows.

    arrival_order represents the sequence of arrival for this specific meetup.
    """
```

with:

```python
    """
    Association object representing a Guest's attendance at a specific Meetup.

    CRITICAL: The background sync upsert NEVER overwrites has_arrived,
    arrival_time, arrival_order, checked_in_by_id, has_paid, paid_at,
    paid_by_id, or guest_type. These are set ONLY by the door tracker
    check-in, payment, and guest-type-classification flows.

    guest_type defaults to NORMAL and is curated by hand by an org admin,
    same as has_paid - the sync never sets or changes it.

    arrival_order represents the sequence of arrival for this specific meetup.
    """
```

Then add the new field right after `paid_by_id: int | None = Field(default=None, foreign_key="users.id")` and before the `guest: "Guest" = Relationship(...)` line:

```python
    # Guest category for this specific meetup - only relevant for the
    # payment gate. Set manually by an org admin, never by Mazmo sync.
    guest_type: str = Field(default=GuestType.NORMAL.value, max_length=16, index=True)
```

- [ ] **Step 4: Update the `sync.py` module docstring**

In `app/services/sync.py`, replace:

```python
MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).
```

with:

```python
MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order), payment fields (has_paid, paid_at,
  paid_by_id), or guest_type.
```

No code change is needed in `_upsert_rsvps()` itself: its `on_conflict_do_update(set_={...})` dict already only lists `rsvp_time` and `cancelled_rsvp`, so `guest_type` (like `has_paid`) is already never overwritten by the upsert. Task 4 adds a regression test that proves this.

- [ ] **Step 5: Update CLAUDE.md rule 2**

In `/home/krapp/dev/vanta/alter-tracker-backend/CLAUDE.md`, in the "Reglas importantes" section, replace:

```
2. **Sync idempotente**: el upsert NUNCA sobrescribe `has_arrived`, `arrival_time`, `arrival_order`, `checked_in_by_id`.
```

with:

```
2. **Sync idempotente**: el upsert NUNCA sobrescribe `has_arrived`, `arrival_time`, `arrival_order`, `checked_in_by_id`, ni `guest_type`.
```

- [ ] **Step 6: Add `guest_type` param to the `make_rsvp` test helper**

In `tests/conftest.py`, replace the `make_rsvp` function:

```python
def make_rsvp(
    session: Session,
    *,
    meetup: Meetup,
    guest: Guest,
    has_arrived: bool = False,
    arrival_order: int | None = None,
    arrival_time: datetime | None = None,
    has_paid: bool = False,
    paid_at: datetime | None = None,
    paid_by_id: int | None = None,
) -> MeetupRsvp:
    """Helper to create a MeetupRsvp directly in the test session."""
    rsvp = MeetupRsvp(
        meetup_id=meetup.id,
        guest_id=guest.id,
        rsvp_time=datetime.now(UTC),
        has_arrived=has_arrived,
        arrival_order=arrival_order,
        arrival_time=arrival_time,
        has_paid=has_paid,
        paid_at=paid_at,
        paid_by_id=paid_by_id,
    )
    session.add(rsvp)
    session.flush()
    session.refresh(rsvp)
    return rsvp
```

with:

```python
def make_rsvp(
    session: Session,
    *,
    meetup: Meetup,
    guest: Guest,
    has_arrived: bool = False,
    arrival_order: int | None = None,
    arrival_time: datetime | None = None,
    has_paid: bool = False,
    paid_at: datetime | None = None,
    paid_by_id: int | None = None,
    guest_type: str = "NORMAL",
) -> MeetupRsvp:
    """Helper to create a MeetupRsvp directly in the test session."""
    rsvp = MeetupRsvp(
        meetup_id=meetup.id,
        guest_id=guest.id,
        rsvp_time=datetime.now(UTC),
        has_arrived=has_arrived,
        arrival_order=arrival_order,
        arrival_time=arrival_time,
        has_paid=has_paid,
        paid_at=paid_at,
        paid_by_id=paid_by_id,
        guest_type=guest_type,
    )
    session.add(rsvp)
    session.flush()
    session.refresh(rsvp)
    return rsvp
```

- [ ] **Step 7: Create `tests/test_guest_type.py` with the enum regression test**

This new file will hold every guest_type test except the sync-specific and event-filter-specific ones (those extend `tests/test_sync.py` and `tests/test_events.py` in later tasks, matching how this repo already splits sync tests out of `test_meetups.py`).

```python
"""
Tests for the guest_type feature: payment exemption categories per RSVP.

Covers: GuestType enum/schema validation, PATCH .../guests/{id}/type,
the check-in payment gate exemption, GET .../meetups/{id}/stats, and
end-to-end scenarios that chain multiple endpoints together.

Sync-specific guest_type regression tests live in test_sync.py.
Event-log filter regression test lives in test_events.py (TestEventFiltering).
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, GuestType, MeetupRsvp, Organization, OrgRole, User
from app.schemas import GuestTypeUpdateRequest
from tests.conftest import (
    get_auth_headers,
    make_guest,
    make_meetup,
    make_org,
    make_org_member,
    make_rsvp,
    make_user,
)


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    """Add staff_user to the default org with OrgRole.STAFF (mirrors test_meetups.py)."""
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


# -- GuestType enum -------------------------------------------------------


def test_guest_type_enum_has_exactly_four_values():
    """
    Verify GuestType has exactly NORMAL, INVITED, VENDOR, STAFF.

    WHY: Regression guard - if someone adds a fifth value without updating
    the stats formulas in get_meetup_stats(), this test turns that into a
    loud failure instead of a silently wrong stats endpoint.
    """
    values = {member.value for member in GuestType}
    assert values == {"NORMAL", "INVITED", "VENDOR", "STAFF"}
    assert len(GuestType) == 4
```

- [ ] **Step 8: Run the new test to confirm it passes**

Run: `pytest tests/test_guest_type.py -v`
Expected: 1 passed (`test_guest_type_enum_has_exactly_four_values`).

- [ ] **Step 9: Run basedpyright and ruff on changed files**

Run: `basedpyright app/models/models.py app/services/sync.py tests/conftest.py tests/test_guest_type.py`
Run: `ruff check app/models/models.py app/services/sync.py tests/conftest.py tests/test_guest_type.py`
Run: `ruff format app/models/models.py app/services/sync.py tests/conftest.py tests/test_guest_type.py`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add app/models/models.py app/services/sync.py CLAUDE.md tests/conftest.py tests/test_guest_type.py
git commit -m "feat: add GuestType enum and MeetupRsvp.guest_type field"
```

---

## Task 2: Migration 0017 - add guest_type column

**Files:**
- Create: `alembic/versions/0017_guest_type_payment_exemption.py`

**Interfaces:**
- Produces: `meetup_rsvps.guest_type` column (VARCHAR(16), NOT NULL, server_default `'NORMAL'`) and index `ix_meetup_rsvps_guest_type` in the real (non-test) database.
- Consumes: nothing from earlier tasks - purely additive schema change following the exact pattern of `alembic/versions/0015_meetup_payment_tracking.py`.

Note on testing this task: the pytest test suite (`tests/conftest.py`, `setup_test_database` fixture) builds its schema via `SQLModel.metadata.create_all(test_engine)` directly from the model definitions, not by running Alembic migrations. That means there is no pytest test that can exercise this migration file itself - the model's Python-level `default=GuestType.NORMAL.value` is what test rows get, regardless of what this migration's `server_default` says. This migration must instead be verified manually against a real Postgres database, which is what Step 3 below does. This directly implements the spec's own instruction to check `\d meetup_rsvps` in psql before considering the migration done, given that migration 0016 previously lost a constraint silently during a more complex column change.

- [ ] **Step 1: Write the migration file**

Create `alembic/versions/0017_guest_type_payment_exemption.py`:

```python
"""add guest_type to meetup_rsvps for payment exemption categories

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-12

Club Vanta needs to distinguish guests who don't pay entry (personally
invited by the organizer, vendors running their own stand, or event
staff) from guests who simply haven't paid yet. Marking them has_paid=True
by hand would lie about the audit trail and lose the reason.

Adds:
  - meetup_rsvps.guest_type: VARCHAR(16), one of NORMAL/INVITED/VENDOR/STAFF
    (see GuestType in app/models/models.py), defaulting to NORMAL so
    existing rows backfill without a separate data migration step. Never
    touched by Mazmo sync, curated by hand by an org admin, same as
    has_paid.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetup_rsvps",
        sa.Column("guest_type", sa.String(length=16), nullable=False, server_default="NORMAL"),
    )
    op.create_index("ix_meetup_rsvps_guest_type", "meetup_rsvps", ["guest_type"])


def downgrade() -> None:
    op.drop_index("ix_meetup_rsvps_guest_type", "meetup_rsvps")
    op.drop_column("meetup_rsvps", "guest_type")
```

- [ ] **Step 2: Run the migration against the real dev database**

Run: `db-start` (if the dev Postgres container isn't already running)
Run: `db-migrate`
Expected: Alembic reports upgrading to `0017`, no errors.

- [ ] **Step 3: Manually verify the column and index via psql**

Run: `psql "$DATABASE_URL" -c '\d meetup_rsvps'`
Expected output includes a line for `guest_type` typed `character varying(16)`, not null, and an index entry `"ix_meetup_rsvps_guest_type" btree (guest_type)`.

Run: `psql "$DATABASE_URL" -c "SELECT guest_type, count(*) FROM meetup_rsvps GROUP BY guest_type;"`
Expected: a single row `NORMAL | <N>` where `<N>` equals the total row count of `meetup_rsvps` before this migration ran (every pre-existing row backfilled to `NORMAL` via `server_default`, none NULL, none any other value). If the dev database was empty before this migration, this query returns zero rows, which is also a pass (nothing to backfill).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0017_guest_type_payment_exemption.py
git commit -m "feat: add migration for meetup_rsvps.guest_type column"
```

---

## Task 3: Schemas - RsvpPublic.guest_type, GuestTypeUpdateRequest, EventTypeFilter, MeetupStatsPublic

**Files:**
- Modify: `app/schemas/guests.py` (RsvpPublic, new GuestTypeUpdateRequest)
- Modify: `app/schemas/meetups.py` (new stats schemas)
- Modify: `app/schemas/events.py` (EventTypeFilter)
- Modify: `app/schemas/__init__.py` (re-exports)
- Test: `tests/test_guest_type.py` (append)

**Interfaces:**
- Consumes: `GuestType` from `app.models.models` (Task 1).
- Produces: `RsvpPublic.guest_type: str` (default `"NORMAL"`) - every endpoint returning `RsvpPublic` now includes this field automatically via `model_validate`.
- Produces: `GuestTypeUpdateRequest(BaseModel)` with field `guest_type: GuestType`, importable as `from app.schemas import GuestTypeUpdateRequest`.
- Produces: `AttendanceStats`, `CancellationStats`, `GuestTypeStats`, `PaymentStats`, `MeetupStatsPublic` in `app.schemas.meetups`, all importable from `app.schemas`. Field names are exactly as listed in each class below - Task 7's router code constructs these by exact keyword.
- Produces: `EventTypeFilter.GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"`.

- [ ] **Step 1: Add `guest_type` to `RsvpPublic` and the new `GuestTypeUpdateRequest` in `app/schemas/guests.py`**

Add the import at the top of the file, after the existing `from pydantic import ...` line:

```python
from app.models.models import GuestType
```

Replace the `RsvpPublic` class:

```python
class RsvpPublic(BaseModel):
    """
    Event-specific RSVP state for a guest at a meetup.

    arrival_time and arrival_order are set by a database trigger when
    has_arrived flips to True during check-in.
    """

    model_config = ConfigDict(from_attributes=True)

    rsvp_time: datetime
    cancelled_rsvp: bool
    has_arrived: bool
    arrival_time: datetime | None = None
    arrival_order: int | None = None
    is_walkin: bool = False
    has_paid: bool = False
    paid_at: datetime | None = None
```

with:

```python
class RsvpPublic(BaseModel):
    """
    Event-specific RSVP state for a guest at a meetup.

    arrival_time and arrival_order are set by a database trigger when
    has_arrived flips to True during check-in. guest_type defaults to
    NORMAL and is only ever changed via PATCH .../guests/{id}/type.
    """

    model_config = ConfigDict(from_attributes=True)

    rsvp_time: datetime
    cancelled_rsvp: bool
    has_arrived: bool
    arrival_time: datetime | None = None
    arrival_order: int | None = None
    is_walkin: bool = False
    has_paid: bool = False
    paid_at: datetime | None = None
    guest_type: str = "NORMAL"
```

Add `GuestTypeUpdateRequest` right after the `RsvpPublic` class (before `class MeetupGuestPublic`):

```python
class GuestTypeUpdateRequest(BaseModel):
    """Request body for changing a guest's category at a specific meetup."""

    guest_type: GuestType
```

- [ ] **Step 2: Add the stats schemas to `app/schemas/meetups.py`**

Append to the end of the file:

```python
class AttendanceStats(BaseModel):
    """Attendance counts for a meetup, excluding cancelled RSVPs."""

    total_rsvps: int
    arrived_count: int
    not_arrived_count: int
    walkin_count: int


class CancellationStats(BaseModel):
    """Counts describing the cancelled RSVP set for a meetup."""

    cancelled_count: int
    cancelled_but_paid_count: int


class GuestTypeStats(BaseModel):
    """Per-category guest counts for a meetup, excluding cancelled RSVPs."""

    normal_count: int
    invited_count: int
    vendor_count: int
    staff_count: int


class PaymentStats(BaseModel):
    """
    Payment counts for a meetup, excluding cancelled RSVPs.

    paid_count and unpaid_count are scoped to guest_type=NORMAL only - a
    non-NORMAL guest who happens to have has_paid=True (e.g. someone who
    paid before being reclassified as staff) is counted only in
    exempt_from_payment_count, never in paid_count or unpaid_count, so
    guest_types.normal_count always equals paid_count + unpaid_count.
    """

    paid_count: int
    unpaid_count: int
    exempt_from_payment_count: int


class MeetupStatsPublic(BaseModel):
    """Grouped attendance/cancellation/guest-type/payment statistics for a meetup."""

    attendance: AttendanceStats
    cancellations: CancellationStats
    guest_types: GuestTypeStats
    payment: PaymentStats
```

- [ ] **Step 3: Add `GUEST_TYPE_CHANGED` to `EventTypeFilter` in `app/schemas/events.py`**

Replace:

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
```

with:

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

(This repo already has other `EventType` values missing from `EventTypeFilter`, e.g. `GUEST_CREATED`, `PAYMENT_RECORDED` - the spec explicitly scopes this change to only adding `GUEST_TYPE_CHANGED`, the new value this feature introduces, and leaves the pre-existing gap alone.)

- [ ] **Step 4: Re-export the new schemas from `app/schemas/__init__.py`**

In the `from app.schemas.guests import (...)` block, add `GuestTypeUpdateRequest` (alphabetically, after `GuestListResponse` and before `GuestPublic`... actually alphabetically `GuestTypeUpdateRequest` sorts after `GuestPublic`, before `GuestWithBanPublic`):

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

In the `from app.schemas.meetups import (...)` block, add the four stats schemas plus `MeetupStatsPublic`:

```python
from app.schemas.meetups import (
    MAZMO_URL_PATTERN,
    AttendanceStats,
    CancellationStats,
    GuestTypeStats,
    MeetupCreate,
    MeetupListResponse,
    MeetupPublic,
    MeetupStatsPublic,
    PaymentStats,
    SyncResponse,
)
```

In the `__all__` list, add these six new names in alphabetical order:

```python
__all__ = [
    "MAZMO_URL_PATTERN",
    "AddOrgMemberRequest",
    "ApproveUserRequest",
    "AttendanceStats",
    "BanGuestRequest",
    "BannedGuestListResponse",
    "BannedGuestPublic",
    "CancellationStats",
    "CheckInResponse",
    "CheckedInByPublic",
    "CreateGuestRequest",
    "CreateManualGuestRequest",
    "DisableUserRequest",
    "EventActorPublic",
    "EventGuestPublic",
    "EventLogListResponse",
    "EventLogPublic",
    "EventLogQuery",
    "EventTypeFilter",
    "GuestListResponse",
    "GuestPublic",
    "GuestTypeStats",
    "GuestTypeUpdateRequest",
    "GuestWithBanPublic",
    "LinkMazmoRequest",
    "MazmoRsvpEntry",
    "MazmoUserEntry",
    "MeetupCreate",
    "MeetupGuestListResponse",
    "MeetupGuestPublic",
    "MeetupListResponse",
    "MeetupPublic",
    "MeetupStatsPublic",
    "OrgCreate",
    "OrgListResponse",
    "OrgMemberListResponse",
    "OrgMemberPublic",
    "OrgMembershipPublic",
    "OrgPublic",
    "OrgUpdate",
    "PaymentResponse",
    "PaymentStats",
    "RecoveryCodeResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "RolePublic",
    "RoleRequest",
    "RsvpPublic",
    "StaffRegisterRequest",
    "SyncResponse",
    "TokenResponse",
    "UpdateGuestRequest",
    "UserPublic",
    "UserSearchResult",
    "VerifyRecoveryCodeRequest",
]
```

- [ ] **Step 5: Write the schema-validation unit test**

Append to `tests/test_guest_type.py`:

```python
def test_guest_type_update_request_rejects_invalid_value():
    """
    Verify that GuestTypeUpdateRequest rejects a value outside the enum.

    WHY: This is the 422 the PATCH .../type endpoint will return for a
    malformed request body, enforced entirely by the GuestType field type
    - no manual validation needed in the router.
    """
    with pytest.raises(ValidationError):
        GuestTypeUpdateRequest(guest_type="BOGUS")  # type: ignore[arg-type]
```

- [ ] **Step 6: Write the guest-list-exposes-guest_type integration test**

Append to `tests/test_guest_type.py`:

```python
def test_guest_list_response_includes_guest_type(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that GET .../meetups/{id}/guests exposes guest_type per RSVP.

    WHY: RsvpPublic.guest_type must round-trip through the existing guest
    list endpoint (no router code change needed there - model_validate
    picks up the new field automatically), matching how has_paid already
    surfaces there.
    """
    guest = make_guest(session, mazmo_user_id=501, mazmo_handle="default_type_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    guest_entry = resp.json()["guests"][0]
    assert guest_entry["rsvp"]["guest_type"] == "NORMAL"
```

- [ ] **Step 7: Run the new tests to confirm they pass**

Run: `pytest tests/test_guest_type.py -v`
Expected: 3 passed (enum test from Task 1 plus the two new ones).

- [ ] **Step 8: Run basedpyright and ruff on changed files**

Run: `basedpyright app/schemas/guests.py app/schemas/meetups.py app/schemas/events.py app/schemas/__init__.py tests/test_guest_type.py`
Run: `ruff check app/schemas/guests.py app/schemas/meetups.py app/schemas/events.py app/schemas/__init__.py tests/test_guest_type.py`
Run: `ruff format app/schemas/guests.py app/schemas/meetups.py app/schemas/events.py app/schemas/__init__.py tests/test_guest_type.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add app/schemas/guests.py app/schemas/meetups.py app/schemas/events.py app/schemas/__init__.py tests/test_guest_type.py
git commit -m "feat: add guest_type schemas and stats response shapes"
```

---

## Task 4: Sync and walk-in default guest_type behavior

**Files:**
- Modify: `tests/test_sync.py` (append two tests)
- Modify: `tests/test_guest_type.py` (append one test)

**Interfaces:**
- Consumes: `make_rsvp(..., guest_type=...)` (Task 1), `RsvpPublic.guest_type` (Task 3), the existing `mock_mazmo` fixture and `FAKE_RSVPS`/`FAKE_USERS` constants from `tests/conftest.py` (unchanged - `FAKE_RSVPS` has keys `111` and `222`).
- No production code changes in this task - `MeetupRsvp(...)` already defaults `guest_type` to `"NORMAL"` (Task 1's `Field(default=GuestType.NORMAL.value, ...)`), and `add_walkin_guest()`/`GuestSyncer._build_rsvps()` already construct `MeetupRsvp` without passing `guest_type`, so the default applies automatically. This task is pure regression-test coverage proving that default and the sync upsert's non-overwrite behavior.

- [ ] **Step 1: Write the sync-defaults-to-NORMAL test**

Append to `tests/test_sync.py` (near the other `has_paid`/`has_arrived` sync tests, e.g. right after `test_sync_does_not_overwrite_payment_data_of_paid_guests_returns_200_ok`):

```python
def test_new_rsvp_defaults_to_guest_type_normal_via_sync(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that a brand-new RSVP created by sync defaults to guest_type=NORMAL.

    WHY: The sync path builds MeetupRsvp() without ever setting guest_type,
    so it must rely on the model's default rather than needing explicit
    sync logic to write "NORMAL".
    """
    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id.in_(  # noqa: E712
            select(Guest.id).where(Guest.mazmo_user_id == 111)
        ))
    ).one()
    assert rsvp.guest_type == "NORMAL"
```

Check the top of `tests/test_sync.py` for its existing imports before adding this - it must already import `Guest` and `select` from `sqlmodel`/`app.models.models` for the query above to resolve; if either is missing from the existing import block, add it.

- [ ] **Step 2: Write the sync-never-overwrites test**

Append to `tests/test_sync.py`, right after the test from Step 1:

```python
def test_sync_never_overwrites_existing_guest_type(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that re-syncing a meetup preserves a guest's existing guest_type.

    WHY: Same invariant as has_paid/has_arrived - if an admin classified a
    guest as VENDOR, then staff syncs again later, the classification must
    survive. The upsert's ON CONFLICT DO UPDATE set_={} dict only touches
    rsvp_time and cancelled_rsvp, so this should already hold with zero
    production code changes; this test proves it.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice")
    rsvp = make_rsvp(session, meetup=meetup, guest=alice, guest_type="VENDOR")

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.guest_type == "VENDOR"
```

- [ ] **Step 3: Write the walk-in-defaults-to-NORMAL test**

Append to `tests/test_guest_type.py`:

```python
def test_new_rsvp_defaults_to_guest_type_normal_via_walkin(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a walk-in RSVP defaults to guest_type=NORMAL.

    WHY: add_walkin_guest() builds MeetupRsvp() without setting guest_type,
    same as the sync path - must rely on the model default.
    """
    guest = make_guest(session, mazmo_user_id=502, mazmo_handle="walkin_guest")

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["rsvp"]["guest_type"] == "NORMAL"
```

- [ ] **Step 4: Run the new tests to confirm they pass**

Run: `pytest tests/test_sync.py tests/test_guest_type.py -v`
Expected: all pass, including the two new `test_sync.py` tests and the new `test_guest_type.py` test.

- [ ] **Step 5: Run basedpyright and ruff on changed files**

Run: `basedpyright tests/test_sync.py tests/test_guest_type.py`
Run: `ruff check tests/test_sync.py tests/test_guest_type.py`
Run: `ruff format tests/test_sync.py tests/test_guest_type.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tests/test_sync.py tests/test_guest_type.py
git commit -m "test: verify guest_type defaults and survives re-sync"
```

---

## Task 5: PATCH .../guests/{guest_id}/type endpoint

**Files:**
- Modify: `app/routers/meetups.py` (new endpoint, import updates, docstring route list)
- Modify: `app/openapi_examples/meetups_examples.py` (new request/response examples)
- Modify: `app/openapi_examples/_constants.py` (new `MEETUP_GUEST_VENDOR_EXAMPLE`, plus `guest_type` added to existing RSVP example dicts)
- Modify: `app/openapi_examples/_error_responses.py` (new `error_422_validation_guest_type`)
- Modify: `tests/test_events.py` (append event-filter regression test to `TestEventFiltering`)
- Test: `tests/test_guest_type.py` (append 9 integration tests)

**Interfaces:**
- Consumes: `GuestTypeUpdateRequest` (Task 3), `GuestType` (Task 1), `EventType.GUEST_TYPE_CHANGED` (Task 1), `_get_meetup_or_404_in_org`, `_refetch_guest_or_500` (existing helpers in `app/routers/meetups.py`).
- Produces: `PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/type`, admin-only (`get_org_admin`), `response_model=MeetupGuestPublic`, status 200.

- [ ] **Step 1: Add the `guest_type` field to the existing RSVP example constants**

In `app/openapi_examples/_constants.py`, add `"guest_type": "NORMAL"` to `RSVP_NOT_ARRIVED`, `RSVP_ARRIVED`, and `RSVP_WALKIN`:

```python
RSVP_NOT_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": False,
    "arrival_time": None,
    "arrival_order": None,
    "is_walkin": False,
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}

RSVP_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": True,
    "arrival_time": TIMESTAMP_2024_03_23_CHECKIN,
    "arrival_order": 1,
    "is_walkin": False,
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}
```

And further down:

```python
RSVP_WALKIN = {
    "rsvp_time": TIMESTAMP_2024_03_23_CHECKIN,
    "cancelled_rsvp": False,
    "has_arrived": False,
    "arrival_time": None,
    "arrival_order": None,
    "is_walkin": True,
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}
```

- [ ] **Step 2: Add `MEETUP_GUEST_VENDOR_EXAMPLE` to `app/openapi_examples/_constants.py`**

Add this new constant right after `MEETUP_GUEST_WALKIN`:

```python
MEETUP_GUEST_VENDOR_EXAMPLE = {
    "guest": {
        "id": GUEST_UUID,
        "mazmo_user_id": 12345,
        "mazmo_handle": "fiestero_feliz",
        "displayname": "Juan El Fiestero",
        "instagram_username": None,
        "is_banned": False,
    },
    "rsvp": {
        "rsvp_time": TIMESTAMP_2024_03_20,
        "cancelled_rsvp": False,
        "has_arrived": False,
        "arrival_time": None,
        "arrival_order": None,
        "is_walkin": False,
        "has_paid": False,
        "paid_at": None,
        "guest_type": "VENDOR",
    },
}
```

- [ ] **Step 3: Add `error_422_validation_guest_type` to `app/openapi_examples/_error_responses.py`**

Add this function right after `error_422_validation_reason` (before `error_422_validation_username`):

```python
def error_422_validation_guest_type() -> ResponsesDict:
    """422 - guest_type is not one of NORMAL/INVITED/VENDOR/STAFF."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_guest_type": {
                            "summary": "guest_type is not a recognized category",
                            "value": {
                                "detail": [
                                    {
                                        "type": "enum",
                                        "loc": ["body", "guest_type"],
                                        "msg": "Input should be 'NORMAL', 'INVITED', 'VENDOR' or 'STAFF'",
                                        "input": "BOGUS",
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }
```

- [ ] **Step 4: Add the OpenAPI examples for the new endpoint in `app/openapi_examples/meetups_examples.py`**

Add `MEETUP_GUEST_VENDOR_EXAMPLE` to the constants import block:

```python
from app.openapi_examples._constants import (
    CHECKIN_RESPONSE_EXAMPLE,
    GUEST_IN_ORG_BANNED,
    GUEST_IN_ORG_NOT_BANNED,
    GUEST_NORMAL_2,
    MEETUP_EXAMPLE,
    MEETUP_EXAMPLE_2,
    MEETUP_EXAMPLE_FINALIZED,
    MEETUP_EXAMPLE_PAID,
    MEETUP_GUEST_VENDOR_EXAMPLE,
    MEETUP_GUEST_WALKIN,
    PAYMENT_RESPONSE_EXAMPLE,
    RSVP_ARRIVED,
    RSVP_NOT_ARRIVED,
    SYNC_RESPONSE_EXAMPLE,
)
```

Add `error_422_validation_guest_type` to the error imports block (alphabetically after `error_409_walkin_already_rsvped`, before `error_422_validation_reason`):

```python
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_meetup,
    error_404_rsvp,
    error_404_walkin_guest_not_in_system,
    error_409_already_checked_in,
    error_409_already_paid,
    error_409_checkin_payment_required,
    error_409_duplicate_meetup,
    error_409_meetup_finalized,
    error_409_meetup_not_finalized,
    error_409_not_checked_in,
    error_409_not_paid,
    error_409_payment_already_disabled,
    error_409_payment_already_enabled,
    error_409_payment_not_required,
    error_409_walkin_already_rsvped,
    error_422_validation_guest_type,
    error_422_validation_reason,
    error_422_validation_url,
    error_502_mazmo_create_meetup,
    error_502_mazmo_sync,
    error_504_mazmo_create_meetup,
    error_504_mazmo_sync,
)
```

Add these new example dicts at the end of the file:

```python
# ── PATCH /meetups/{id}/guests/{guest_id}/type ─────────────────────────────────

UPDATE_GUEST_TYPE_REQUEST_EXAMPLES: dict[str, Any] = {
    "classify_vendor": {
        "summary": "Classify a guest as a vendor",
        "description": "Vendors bring their own stand and are exempt from the payment gate",
        "value": {"guest_type": "VENDOR"},
    },
    "classify_invited": {
        "summary": "Classify a guest as personally invited",
        "description": "Invited guests were personally invited by the organizer and don't pay entry",
        "value": {"guest_type": "INVITED"},
    },
    "revert_to_normal": {
        "summary": "Revert a guest back to normal",
        "description": "Normal guests are subject to the meetup's requires_payment flag like anyone else",
        "value": {"guest_type": "NORMAL"},
    },
}

UPDATE_GUEST_TYPE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest type updated",
        "content": {
            "application/json": {
                "examples": {
                    "updated": {
                        "summary": "Guest reclassified as vendor",
                        "description": "This guest is now exempt from the payment check-in gate",
                        "value": MEETUP_GUEST_VENDOR_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_rsvp(action="change guest type"),
    **error_422_validation_guest_type(),
}
```

- [ ] **Step 5: Add the router endpoint in `app/routers/meetups.py`**

Update the module docstring route list - add these two lines after the `undo-checkin` line and before the `payment` line (the `stats` line belongs to Task 7, add both now to keep the docstring block edited once):

```python
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/undo-checkin -> undo check-in (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/type        -> change guest type (org admin)
GET   /organizations/{org_id}/meetups/{meetup_id}/stats                  -> meetup stats (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/payment      -> mark paid (org admin)
```

Update the model import line:

```python
from app.models.models import EventLog, EventType, Guest, GuestType, Meetup, MeetupRsvp, OrganizationBan, User
```

Add to the `from app.openapi_examples.meetups_examples import (...)` block (alphabetically):

```python
from app.openapi_examples.meetups_examples import (
    ADD_WALKIN_RESPONSES,
    CHECKIN_RESPONSES,
    CREATE_MEETUP_REQUEST_EXAMPLES,
    CREATE_MEETUP_RESPONSES,
    DISABLE_PAYMENT_RESPONSES,
    ENABLE_PAYMENT_RESPONSES,
    FINALIZE_MEETUP_RESPONSES,
    GET_MEETUP_RESPONSES,
    LIST_MEETUP_GUESTS_RESPONSES,
    LIST_MEETUPS_RESPONSES,
    MARK_PAYMENT_RESPONSES,
    SYNC_MEETUP_RESPONSES,
    UNDO_CHECKIN_REQUEST_EXAMPLES,
    UNDO_CHECKIN_RESPONSES,
    UNDO_PAYMENT_REQUEST_EXAMPLES,
    UNDO_PAYMENT_RESPONSES,
    UNFINALIZE_MEETUP_RESPONSES,
    UPDATE_GUEST_TYPE_REQUEST_EXAMPLES,
    UPDATE_GUEST_TYPE_RESPONSES,
)
```

Add to the `from app.schemas import (...)` block:

```python
from app.schemas import (
    CheckedInByPublic,
    CheckInResponse,
    GuestPublic,
    GuestTypeUpdateRequest,
    GuestWithBanPublic,
    MeetupCreate,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    MeetupListResponse,
    MeetupPublic,
    PaymentResponse,
    RsvpPublic,
    SyncResponse,
)
```

Insert the new endpoint right after `undo_checkin_guest()` (its closing `return guest` line) and before the `# -- Mark payment` section divider:

```python
# -- Change guest type ---------------------------------------------------


@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/type",
    response_model=MeetupGuestPublic,
    summary="Change a guest's category for this meetup (org admin only)",
    responses=UPDATE_GUEST_TYPE_RESPONSES,
)
async def update_guest_type(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    payload: Annotated[GuestTypeUpdateRequest, Body(openapi_examples=UPDATE_GUEST_TYPE_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> MeetupGuestPublic:
    """
    Change a guest's category (NORMAL/INVITED/VENDOR/STAFF) at this meetup.

    INVITED, VENDOR, and STAFF guests are exempt from the payment check-in
    gate regardless of has_paid - see checkin_guest(). This does not touch
    has_paid, paid_at, or paid_by_id.

    Returns 404 if the guest is not RSVPed to this meetup.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == guest_id)
        .with_for_update()
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot change guest type: guest id={guest_id} is not RSVPed to this meetup. "
                f"Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    old_guest_type = rsvp.guest_type
    new_guest_type = payload.guest_type.value
    rsvp.guest_type = new_guest_type

    event = EventLog(
        event_type=EventType.GUEST_TYPE_CHANGED,
        actor_id=admin.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=f"Changed guest_type from {old_guest_type} to {new_guest_type}",
    )

    session.add(rsvp)
    session.add(event)
    session.commit()
    session.refresh(rsvp)

    guest = _refetch_guest_or_500(session, guest_id, action="guest type change")

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()

    log.info(
        "Guest type changed",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        old_guest_type=old_guest_type,
        new_guest_type=new_guest_type,
    )

    return MeetupGuestPublic(
        guest=GuestWithBanPublic(
            id=guest.id,
            mazmo_user_id=guest.mazmo_user_id,
            mazmo_handle=guest.mazmo_handle,
            displayname=guest.displayname,
            instagram_username=guest.instagram_username,
            is_banned=ban is not None,
        ),
        rsvp=RsvpPublic.model_validate(rsvp),
    )
```

- [ ] **Step 6: Write the 9 PATCH endpoint integration tests**

Append to `tests/test_guest_type.py`:

```python
# -- PATCH .../guests/{guest_id}/type --------------------------------------


def test_update_guest_type_returns_200_and_updates_rsvp(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that an admin can reclassify a guest and the RSVP reflects it.
    """
    guest = make_guest(session, mazmo_user_id=510, mazmo_handle="vendor_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "VENDOR"

    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.guest_type == "VENDOR"


def test_update_guest_type_returns_403_for_staff_non_admin(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot change guest_type.

    WHY: Same permission level as mark_guest_paid - reclassification is
    admin-only.
    """
    guest = make_guest(session, mazmo_user_id=511, mazmo_handle="staff_blocked_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=staff_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_update_guest_type_returns_401_without_auth(client: TestClient, session: Session, meetup):
    """Verify that an unauthenticated request is rejected."""
    guest = make_guest(session, mazmo_user_id=512, mazmo_handle="no_auth_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_update_guest_type_returns_404_when_guest_not_rsvped_to_meetup(
    client: TestClient, admin_headers: dict, meetup
):
    """Verify that changing guest_type for a non-RSVPed guest returns 404."""
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{uuid.uuid4()}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_update_guest_type_returns_404_for_nonexistent_meetup(
    client: TestClient, admin_headers: dict, org: Organization
):
    """Verify that changing guest_type for a non-existent meetup returns 404."""
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{uuid.uuid4()}/guests/{uuid.uuid4()}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_update_guest_type_returns_403_for_admin_of_different_org(client: TestClient, session: Session):
    """
    Verify multi-tenant isolation: an Org A admin cannot change guest_type
    on an Org B meetup.

    WHY: get_org_admin(org_id) checks membership in the org_id from the
    URL path - an admin of a different org has no membership row there at
    all, so this must 403 before ever looking at the RSVP.
    """
    org_a = make_org(session, name="Org A Guest Type", slug="org-a-guest-type")
    org_b = make_org(session, name="Org B Guest Type", slug="org-b-guest-type")
    admin_a = make_user(session, username="admin_a_guest_type")
    make_org_member(session, org=org_a, user=admin_a, role=OrgRole.ADMIN)
    headers_a = get_auth_headers(client, "admin_a_guest_type", "a-very-secure-passphrase")

    guest = make_guest(session, mazmo_user_id=513, mazmo_handle="cross_org_guest")
    meetup_b = make_meetup(
        session, org=org_b, name="Org B Meetup", mazmo_meetup_url="https://mazmo.net/test/org-b-meetup-gt-1"
    )
    make_rsvp(session, meetup=meetup_b, guest=guest)

    resp = client.patch(
        f"/organizations/{org_b.id}/meetups/{meetup_b.id}/guests/{guest.id}/type",
        headers=headers_a,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_update_guest_type_creates_audit_log_with_old_and_new_reason(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
    admin_user: User,
):
    """
    Verify that changing guest_type writes a GUEST_TYPE_CHANGED EventLog
    with a reason naming both the old and new value.
    """
    guest = make_guest(session, mazmo_user_id=514, mazmo_handle="audit_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "STAFF"},
    )

    assert resp.status_code == status.HTTP_200_OK

    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_TYPE_CHANGED)
    ).one()
    assert event.actor_id == admin_user.id
    assert event.meetup_id == meetup.id
    assert event.reason == "Changed guest_type from NORMAL to STAFF"


def test_update_guest_type_does_not_modify_has_paid(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that reclassifying a guest who already paid leaves has_paid,
    paid_at, and paid_by_id untouched.
    """
    guest = make_guest(session, mazmo_user_id=515, mazmo_handle="already_paid_guest")
    paid_at = datetime(2026, 3, 17, 21, 0, tzinfo=UTC)
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, has_paid=True, paid_at=paid_at)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.has_paid is True
    assert rsvp.paid_at == paid_at
    assert rsvp.guest_type == "VENDOR"


def test_update_guest_type_back_to_normal(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """Verify a VENDOR -> NORMAL round-trip works."""
    guest = make_guest(session, mazmo_user_id=516, mazmo_handle="roundtrip_guest")
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, guest_type="VENDOR")

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "NORMAL"},
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "NORMAL"
    session.refresh(rsvp)
    assert rsvp.guest_type == "NORMAL"
```

- [ ] **Step 7: Write the event-filter regression test**

Append to `tests/test_events.py`, inside the `TestEventFiltering` class (after `test_filter_by_actor_id`, matching its style):

```python
    def test_filter_by_guest_type_changed(
        self, client: TestClient, admin_headers: dict, session: Session, org: Organization
    ):
        """
        Verify that ?type=GUEST_TYPE_CHANGED filters to only guest-type-change
        events.

        WHY: GUEST_TYPE_CHANGED must be added to EventTypeFilter (not just
        EventType) or every request with this filter value 400s.
        """
        from app.models.models import Meetup
        from tests.conftest import make_meetup, make_rsvp

        guest = make_guest(session, mazmo_user_id=1, mazmo_handle="testguest")
        meetup: Meetup = make_meetup(session, org=org)
        make_rsvp(session, meetup=meetup, guest=guest)

        client.patch(
            f"/organizations/{org.id}/guests/{guest.id}/ban",
            json={"reason": "Testing"},
            headers=admin_headers,
        )
        client.patch(
            f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/type",
            json={"guest_type": "VENDOR"},
            headers=admin_headers,
        )

        resp = client.get(f"/organizations/{org.id}/events/?type=GUEST_TYPE_CHANGED", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["event_type"] == "GUEST_TYPE_CHANGED"
```

Note: this test uses local imports for `Meetup`/`make_meetup`/`make_rsvp` because `tests/test_events.py`'s existing top-level imports (`from app.models.models import EventLog, EventType, Organization, OrgRole` and `from tests.conftest import make_guest, make_meetup, make_org_member, make_rsvp, make_user`) already include `make_meetup` and `make_rsvp` - check the actual current import block first; if they are already imported at module level (they are, per the file read during planning), remove the local `from tests.conftest import make_meetup, make_rsvp` line and the `from app.models.models import Meetup` line from inside the test and just use them directly, keeping the test body otherwise identical. Only keep a local import if the top-level block turns out to be missing one of these names when you open the file.

- [ ] **Step 8: Run the new tests to confirm they pass**

Run: `pytest tests/test_guest_type.py tests/test_events.py -v`
Expected: all pass, including the 9 new PATCH tests and the event filter test.

- [ ] **Step 9: Run the full test suite to confirm nothing regressed**

Run: `pytest -v`
Expected: all tests pass (no pre-existing test should be affected by an additive endpoint and additive schema field).

- [ ] **Step 10: Run basedpyright and ruff on changed files**

Run: `basedpyright app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py app/openapi_examples/_error_responses.py tests/test_guest_type.py tests/test_events.py`
Run: `ruff check app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py app/openapi_examples/_error_responses.py tests/test_guest_type.py tests/test_events.py`
Run: `ruff format app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py app/openapi_examples/_error_responses.py tests/test_guest_type.py tests/test_events.py`
Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py app/openapi_examples/_error_responses.py tests/test_guest_type.py tests/test_events.py
git commit -m "feat: add PATCH .../guests/{guest_id}/type endpoint"
```

---

## Task 6: Check-in payment gate exemption

**Files:**
- Modify: `app/routers/meetups.py` (checkin_guest gate, mark_guest_paid docstring)
- Test: `tests/test_guest_type.py` (append 7 integration tests)

**Interfaces:**
- Consumes: `GuestType` (Task 1), the existing `checkin_guest()` function and its `.with_for_update()` row lock (unchanged).
- Produces: no new interfaces - modifies the existing gate condition only.

- [ ] **Step 1: Change the check-in payment gate**

In `app/routers/meetups.py`, inside `checkin_guest()`, replace:

```python
    if meetup.requires_payment and not rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.displayname}' (id={guest_id}) "
                f"has not paid the entrance fee for '{meetup.name}'. "
                f"Mark the payment first via PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{guest_id}/payment."
            ),
        )
```

with:

```python
    if meetup.requires_payment and rsvp.guest_type == GuestType.NORMAL.value and not rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.displayname}' (id={guest_id}) "
                f"has not paid the entrance fee for '{meetup.name}'. "
                f"Mark the payment first via PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{guest_id}/payment, or reclassify them via PATCH /organizations/{org_id}"
                f"/meetups/{meetup_id}/guests/{guest_id}/type if they should be exempt."
            ),
        )
```

The `.with_for_update()` row lock earlier in the function is untouched.

- [ ] **Step 2: Update the `mark_guest_paid` docstring**

Replace:

```python
    """
    Mark a guest's entrance fee as paid for this specific meetup.

    Payment is handled externally by the organizer (cash, transfer, etc.);
    this just records that it happened so check-in can be enforced.

    Returns 409 if the meetup doesn't require payment, if the guest already
    paid, or if the meetup is finalized. Returns 404 if not RSVPed.
    """
```

with:

```python
    """
    Mark a guest's entrance fee as paid for this specific meetup.

    Payment is handled externally by the organizer (cash, transfer, etc.);
    this just records that it happened so check-in can be enforced for
    NORMAL guests. INVITED/VENDOR/STAFF guests (see PATCH .../guests/{id}
    /type) skip the payment check-in gate entirely regardless of has_paid.

    Returns 409 if the meetup doesn't require payment, if the guest already
    paid, or if the meetup is finalized. Returns 404 if not RSVPed.
    """
```

- [ ] **Step 3: Write the 7 check-in gate integration tests**

Append to `tests/test_guest_type.py`. These tests need a paid meetup fixture - define it locally in this file (mirroring `tests/test_meetups.py`'s `paid_meetup` fixture, since fixtures are not shared across test files in this repo unless placed in `conftest.py`):

```python
# -- Check-in payment gate exemption ---------------------------------------


@pytest.fixture()
def paid_meetup(session: Session, org: Organization):
    """A meetup that requires payment before check-in."""
    return make_meetup(
        session,
        org=org,
        name="Alter Paid Event - Guest Type",
        mazmo_meetup_url="https://mazmo.net/test/alter-paid-guest-type",
        requires_payment=True,
    )


def test_checkin_blocks_normal_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """
    Verify that an unpaid NORMAL guest is still blocked at check-in.

    WHY: Regression guard - the guest_type exemption must not weaken the
    existing payment gate for the default category.
    """
    guest = make_guest(session, mazmo_user_id=520, mazmo_handle="normal_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_allows_invited_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid INVITED guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=521, mazmo_handle="invited_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="INVITED")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_vendor_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid VENDOR guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=522, mazmo_handle="vendor_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="VENDOR")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_staff_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid STAFF guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=523, mazmo_handle="staff_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="STAFF")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_normal_paid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """
    Verify that a NORMAL guest who has paid still checks in successfully.

    WHY: Regression guard for the existing happy path.
    """
    guest = make_guest(session, mazmo_user_id=524, mazmo_handle="normal_paid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_normal_guest_when_requires_payment_false(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify that a NORMAL guest checks in freely when the meetup itself
    does not require payment.

    WHY: Regression guard - the meetup-level flag must still be the
    primary gate; guest_type only matters when requires_payment is True.
    """
    guest = make_guest(session, mazmo_user_id=525, mazmo_handle="normal_free_event")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_still_blocked_for_banned_guest_even_if_exempt_from_payment(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    org: Organization,
    admin_user: User,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that the guest_type payment exemption does not affect ban status
    reporting for a banned STAFF guest.

    IMPORTANT DISCREPANCY FROM THE SPEC: the design spec
    (docs/superpowers/specs/2026-08-12-guest-type-payment-exemption-design.md)
    describes this test as verifying that check-in remains hard-blocked
    (409) for a banned guest even when guest_type exempts them from
    payment, treating "block banned guests at check-in" as an existing,
    untouched gate. Investigation during planning found NO such gate
    exists anywhere in this codebase: checkin_guest() in
    app/routers/meetups.py never queries OrganizationBan or checks
    is_banned. Ban status is informational only - GuestWithBanPublic.
    is_banned is surfaced in the guest list (GET .../meetups/{id}/guests)
    purely so door staff can see the warning and refuse entry manually;
    the API itself has never enforced it. Adding a new ban-blocking gate
    to check-in is out of scope for this feature (the spec's own "Fuera
    de alcance" section does not list it, and the spec's payment-gate
    diff explicitly does not touch anything ban-related). Implementing
    one here would be inventing a different feature.

    This test instead verifies the two things that ARE true and ARE this
    feature's responsibility: (1) a banned guest classified as STAFF (and
    therefore payment-exempt) still checks in successfully - the
    guest_type exemption does not crash or misbehave in the presence of a
    ban - and (2) is_banned is still correctly reported as True via the
    guest list endpoint afterward, so staff retain the information they'd
    need to intervene by hand.

    If a hard ban-blocking gate at check-in is later desired, it should be
    scoped as its own feature with its own spec, not folded into this one.
    """
    guest = make_guest(session, mazmo_user_id=526, mazmo_handle="banned_staff_guest")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="STAFF")
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Testing ban plus payment-exemption composition"},
        headers=staff_headers,
    )

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    list_resp = client.get(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests",
        headers=staff_headers,
    )
    assert list_resp.status_code == status.HTTP_200_OK
    guest_entry = next(g for g in list_resp.json()["guests"] if g["guest"]["id"] == str(guest.id))
    assert guest_entry["guest"]["is_banned"] is True
```

Note: `POST /organizations/{org_id}/guests/{id}/ban` requires org admin per this repo's existing ban endpoint (`get_org_admin` in `app/routers/organizations.py`) - check the actual permission dependency on that route when writing this step; if it turns out to require admin rather than staff, use `admin_headers` for the ban call specifically (still use `staff_headers` for the check-in and guest-list calls, matching the spec's staff-does-check-in framing), e.g.:

```python
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Testing ban plus payment-exemption composition"},
        headers=admin_headers,
    )
```

(add `admin_headers: dict` to the test's parameter list if needed).

- [ ] **Step 4: Run the new tests to confirm they pass**

Run: `pytest tests/test_guest_type.py -v`
Expected: all pass, including the 7 new check-in gate tests.

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `pytest -v`
Expected: all tests pass, in particular `test_checkin_returns_409_when_meetup_requires_payment_and_guest_unpaid` and the other pre-existing payment tests in `tests/test_meetups.py` still pass unchanged (they all use NORMAL guests implicitly via `make_rsvp`'s new default).

- [ ] **Step 6: Run basedpyright and ruff on changed files**

Run: `basedpyright app/routers/meetups.py tests/test_guest_type.py`
Run: `ruff check app/routers/meetups.py tests/test_guest_type.py`
Run: `ruff format app/routers/meetups.py tests/test_guest_type.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add app/routers/meetups.py tests/test_guest_type.py
git commit -m "feat: exempt non-NORMAL guest types from the payment check-in gate"
```

---

## Task 7: GET .../meetups/{meetup_id}/stats endpoint

**Files:**
- Modify: `app/routers/meetups.py` (new endpoint)
- Modify: `app/openapi_examples/meetups_examples.py` (new response example)
- Modify: `app/openapi_examples/_constants.py` (new `MEETUP_STATS_EXAMPLE`)
- Test: `tests/test_guest_type.py` (append 13 integration tests)

**Interfaces:**
- Consumes: `MeetupStatsPublic`, `AttendanceStats`, `CancellationStats`, `GuestTypeStats`, `PaymentStats` (Task 3), `GuestType` (Task 1).
- Produces: `GET /organizations/{org_id}/meetups/{meetup_id}/stats`, org-member-level (`get_org_member`), `response_model=MeetupStatsPublic`, status 200.

- [ ] **Step 1: Add `MEETUP_STATS_EXAMPLE` to `app/openapi_examples/_constants.py`**

Add this new constant right after `MEETUP_GUEST_VENDOR_EXAMPLE` (added in Task 5):

```python
MEETUP_STATS_EXAMPLE = {
    "attendance": {
        "total_rsvps": 20,
        "arrived_count": 15,
        "not_arrived_count": 5,
        "walkin_count": 2,
    },
    "cancellations": {
        "cancelled_count": 3,
        "cancelled_but_paid_count": 1,
    },
    "guest_types": {
        "normal_count": 15,
        "invited_count": 2,
        "vendor_count": 2,
        "staff_count": 1,
    },
    "payment": {
        "paid_count": 10,
        "unpaid_count": 5,
        "exempt_from_payment_count": 5,
    },
}
```

(Invariant check for whoever reviews this: `normal_count` 15 = `paid_count` 10 + `unpaid_count` 5; `total_rsvps` 20 = 15+2+2+1 = 10+5+5. Keep these three numbers consistent if you ever edit this example.)

- [ ] **Step 2: Add the OpenAPI response example in `app/openapi_examples/meetups_examples.py`**

Add `MEETUP_STATS_EXAMPLE` to the constants import block (alphabetically, after `MEETUP_GUEST_WALKIN`... actually after `MEETUP_GUEST_VENDOR_EXAMPLE` added in Task 5):

```python
from app.openapi_examples._constants import (
    CHECKIN_RESPONSE_EXAMPLE,
    GUEST_IN_ORG_BANNED,
    GUEST_IN_ORG_NOT_BANNED,
    GUEST_NORMAL_2,
    MEETUP_EXAMPLE,
    MEETUP_EXAMPLE_2,
    MEETUP_EXAMPLE_FINALIZED,
    MEETUP_EXAMPLE_PAID,
    MEETUP_GUEST_VENDOR_EXAMPLE,
    MEETUP_GUEST_WALKIN,
    MEETUP_STATS_EXAMPLE,
    PAYMENT_RESPONSE_EXAMPLE,
    RSVP_ARRIVED,
    RSVP_NOT_ARRIVED,
    SYNC_RESPONSE_EXAMPLE,
)
```

Add this new responses dict at the end of the file:

```python
# ── GET /meetups/{id}/stats ─────────────────────────────────────────────────

MEETUP_STATS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Meetup statistics",
        "content": {
            "application/json": {
                "examples": {
                    "stats": {
                        "summary": "Grouped attendance/payment/guest-type stats",
                        "value": MEETUP_STATS_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
}
```

- [ ] **Step 3: Add the router endpoint in `app/routers/meetups.py`**

Add to the `from app.openapi_examples.meetups_examples import (...)` block (alphabetically, after `MARK_PAYMENT_RESPONSES`):

```python
    MARK_PAYMENT_RESPONSES,
    MEETUP_STATS_RESPONSES,
    SYNC_MEETUP_RESPONSES,
```

Add to the `from app.schemas import (...)` block:

```python
from app.schemas import (
    AttendanceStats,
    CancellationStats,
    CheckedInByPublic,
    CheckInResponse,
    GuestPublic,
    GuestTypeStats,
    GuestTypeUpdateRequest,
    GuestWithBanPublic,
    MeetupCreate,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    MeetupListResponse,
    MeetupPublic,
    MeetupStatsPublic,
    PaymentResponse,
    PaymentStats,
    RsvpPublic,
    SyncResponse,
)
```

Insert the new endpoint right after `list_meetup_guests()` (its closing `return MeetupGuestListResponse(...)` line) and before the `# -- Add walk-in guest` section divider:

```python
# -- Meetup stats ---------------------------------------------------------


@router.get(
    "/organizations/{org_id}/meetups/{meetup_id}/stats",
    response_model=MeetupStatsPublic,
    summary="Get grouped attendance/payment/guest-type statistics for this meetup",
    responses=MEETUP_STATS_RESPONSES,
)
async def get_meetup_stats(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _member: User = Depends(get_org_member),
) -> MeetupStatsPublic:
    """
    Return grouped attendance, cancellation, guest-type, and payment stats
    for this meetup.

    All counts except cancellations.* exclude cancelled RSVPs.
    payment.paid_count and payment.unpaid_count are scoped to
    guest_type=NORMAL only, so guest_types.normal_count always equals
    payment.paid_count + payment.unpaid_count, and attendance.total_rsvps
    always equals the sum of all four guest_types.* counts and also the
    sum of all three payment.* counts.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rows = session.exec(
        select(
            MeetupRsvp.cancelled_rsvp,
            MeetupRsvp.has_arrived,
            MeetupRsvp.is_walkin,
            MeetupRsvp.guest_type,
            MeetupRsvp.has_paid,
        ).where(MeetupRsvp.meetup_id == meetup_id)
    ).all()

    total_rsvps = 0
    arrived_count = 0
    not_arrived_count = 0
    walkin_count = 0
    cancelled_count = 0
    cancelled_but_paid_count = 0
    normal_count = 0
    invited_count = 0
    vendor_count = 0
    staff_count = 0
    paid_count = 0
    unpaid_count = 0
    exempt_from_payment_count = 0

    for cancelled_rsvp, has_arrived, is_walkin, guest_type, has_paid in rows:
        if cancelled_rsvp:
            cancelled_count += 1
            if has_paid:
                cancelled_but_paid_count += 1
            continue

        total_rsvps += 1
        if has_arrived:
            arrived_count += 1
        else:
            not_arrived_count += 1
        if is_walkin:
            walkin_count += 1

        if guest_type == GuestType.NORMAL.value:
            normal_count += 1
            if has_paid:
                paid_count += 1
            else:
                unpaid_count += 1
        elif guest_type == GuestType.INVITED.value:
            invited_count += 1
            exempt_from_payment_count += 1
        elif guest_type == GuestType.VENDOR.value:
            vendor_count += 1
            exempt_from_payment_count += 1
        elif guest_type == GuestType.STAFF.value:
            staff_count += 1
            exempt_from_payment_count += 1

    return MeetupStatsPublic(
        attendance=AttendanceStats(
            total_rsvps=total_rsvps,
            arrived_count=arrived_count,
            not_arrived_count=not_arrived_count,
            walkin_count=walkin_count,
        ),
        cancellations=CancellationStats(
            cancelled_count=cancelled_count,
            cancelled_but_paid_count=cancelled_but_paid_count,
        ),
        guest_types=GuestTypeStats(
            normal_count=normal_count,
            invited_count=invited_count,
            vendor_count=vendor_count,
            staff_count=staff_count,
        ),
        payment=PaymentStats(
            paid_count=paid_count,
            unpaid_count=unpaid_count,
            exempt_from_payment_count=exempt_from_payment_count,
        ),
    )
```

Also update the module docstring's `stats` route-list line added in Task 5 Step 5 if it needs the summary text aligned - it already reads `GET   /organizations/{org_id}/meetups/{meetup_id}/stats                  -> meetup stats (org member)`, which is already correct; no further docstring change needed here.

- [ ] **Step 4: Write the 13 stats endpoint integration tests**

Append to `tests/test_guest_type.py`:

```python
# -- GET .../meetups/{meetup_id}/stats -------------------------------------


def test_meetup_stats_returns_200_with_grouped_shape(
    client: TestClient, staff_headers: dict, meetup, org_staff_member
):
    """Verify the response has all 4 sub-objects with their fields."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert set(data.keys()) == {"attendance", "cancellations", "guest_types", "payment"}
    assert set(data["attendance"].keys()) == {"total_rsvps", "arrived_count", "not_arrived_count", "walkin_count"}
    assert set(data["cancellations"].keys()) == {"cancelled_count", "cancelled_but_paid_count"}
    assert set(data["guest_types"].keys()) == {"normal_count", "invited_count", "vendor_count", "staff_count"}
    assert set(data["payment"].keys()) == {"paid_count", "unpaid_count", "exempt_from_payment_count"}


def test_meetup_stats_returns_401_without_auth(client: TestClient, meetup):
    """Verify that an unauthenticated request is rejected."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_meetup_stats_returns_403_for_member_of_different_org(client: TestClient, session: Session):
    """Verify multi-tenant isolation for the stats endpoint."""
    org_a = make_org(session, name="Stats Org A", slug="stats-org-a")
    org_b = make_org(session, name="Stats Org B", slug="stats-org-b")
    member_a = make_user(session, username="member_a_stats")
    make_org_member(session, org=org_a, user=member_a, role=OrgRole.STAFF)
    headers_a = get_auth_headers(client, "member_a_stats", "a-very-secure-passphrase")

    meetup_b = make_meetup(
        session, org=org_b, name="Org B Stats Meetup", mazmo_meetup_url="https://mazmo.net/test/org-b-stats-1"
    )

    resp = client.get(f"/organizations/{org_b.id}/meetups/{meetup_b.id}/stats", headers=headers_a)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_meetup_stats_returns_404_for_nonexistent_meetup(
    client: TestClient, staff_headers: dict, org: Organization, org_staff_member
):
    """Verify that a non-existent meetup id returns 404."""
    resp = client.get(f"/organizations/{org.id}/meetups/{uuid.uuid4()}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_meetup_stats_returns_zero_counts_for_meetup_with_no_rsvps(
    client: TestClient, staff_headers: dict, meetup, org_staff_member
):
    """Verify a freshly created meetup with no RSVPs returns all zeros."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["attendance"] == {
        "total_rsvps": 0,
        "arrived_count": 0,
        "not_arrived_count": 0,
        "walkin_count": 0,
    }
    assert data["cancellations"] == {"cancelled_count": 0, "cancelled_but_paid_count": 0}
    assert data["guest_types"] == {
        "normal_count": 0,
        "invited_count": 0,
        "vendor_count": 0,
        "staff_count": 0,
    }
    assert data["payment"] == {"paid_count": 0, "unpaid_count": 0, "exempt_from_payment_count": 0}


def test_meetup_stats_counts_arrived_and_not_arrived_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify arrived_count and not_arrived_count split correctly."""
    arrived = make_guest(session, mazmo_user_id=530, mazmo_handle="arrived_guest")
    not_arrived = make_guest(session, mazmo_user_id=531, mazmo_handle="not_arrived_guest")
    make_rsvp(session, meetup=meetup, guest=arrived, has_arrived=True, arrival_order=1)
    make_rsvp(session, meetup=meetup, guest=not_arrived)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["attendance"]
    assert data["total_rsvps"] == 2
    assert data["arrived_count"] == 1
    assert data["not_arrived_count"] == 1


def test_meetup_stats_counts_walkins_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify walkin_count only counts is_walkin=True RSVPs."""
    walkin = make_guest(session, mazmo_user_id=532, mazmo_handle="walkin_stats_guest")
    rsvped = make_guest(session, mazmo_user_id=533, mazmo_handle="rsvped_stats_guest")
    rsvp = make_rsvp(session, meetup=meetup, guest=walkin)
    rsvp.is_walkin = True
    session.add(rsvp)
    session.flush()
    make_rsvp(session, meetup=meetup, guest=rsvped)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["attendance"]["walkin_count"] == 1


def test_meetup_stats_excludes_cancelled_from_attendance_totals(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify a cancelled RSVP does not count toward attendance.total_rsvps."""
    active = make_guest(session, mazmo_user_id=534, mazmo_handle="active_stats_guest")
    cancelled = make_guest(session, mazmo_user_id=535, mazmo_handle="cancelled_stats_guest")
    make_rsvp(session, meetup=meetup, guest=active)
    cancelled_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled)
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["attendance"]["total_rsvps"] == 1
    assert data["cancellations"]["cancelled_count"] == 1


def test_meetup_stats_counts_cancelled_but_paid_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify cancelled_but_paid_count only counts cancelled+has_paid RSVPs."""
    cancelled_paid = make_guest(session, mazmo_user_id=536, mazmo_handle="cancelled_paid_guest")
    cancelled_unpaid = make_guest(session, mazmo_user_id=537, mazmo_handle="cancelled_unpaid_guest")
    paid_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_paid, has_paid=True, paid_at=datetime.now(UTC))
    paid_rsvp.cancelled_rsvp = True
    session.add(paid_rsvp)
    unpaid_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_unpaid)
    unpaid_rsvp.cancelled_rsvp = True
    session.add(unpaid_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["cancellations"]
    assert data["cancelled_count"] == 2
    assert data["cancelled_but_paid_count"] == 1


def test_meetup_stats_counts_all_four_guest_types_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify one guest of each type produces the expected 4 counters."""
    normal = make_guest(session, mazmo_user_id=538, mazmo_handle="stats_normal")
    invited = make_guest(session, mazmo_user_id=539, mazmo_handle="stats_invited")
    vendor = make_guest(session, mazmo_user_id=540, mazmo_handle="stats_vendor")
    staff = make_guest(session, mazmo_user_id=541, mazmo_handle="stats_staff")
    make_rsvp(session, meetup=meetup, guest=normal)
    make_rsvp(session, meetup=meetup, guest=invited, guest_type="INVITED")
    make_rsvp(session, meetup=meetup, guest=vendor, guest_type="VENDOR")
    make_rsvp(session, meetup=meetup, guest=staff, guest_type="STAFF")

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["guest_types"]
    assert data == {"normal_count": 1, "invited_count": 1, "vendor_count": 1, "staff_count": 1}


def test_meetup_stats_counts_multiple_guests_per_type_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify 3 VENDOR guests produce vendor_count == 3, not just a
    presence-detecting count.
    """
    for i in range(3):
        guest = make_guest(session, mazmo_user_id=550 + i, mazmo_handle=f"vendor_multi_{i}")
        make_rsvp(session, meetup=meetup, guest=guest, guest_type="VENDOR")

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["guest_types"]["vendor_count"] == 3


def test_meetup_stats_paid_and_unpaid_scoped_to_normal_guest_type(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify a VENDOR guest with has_paid=True counts only toward
    exempt_from_payment_count, never paid_count or unpaid_count.

    WHY: This is the double-counting bug explicitly ruled out in the
    design - guest_types.normal_count must always equal
    payment.paid_count + payment.unpaid_count.
    """
    paid_vendor = make_guest(session, mazmo_user_id=560, mazmo_handle="paid_vendor_guest")
    make_rsvp(session, meetup=meetup, guest=paid_vendor, guest_type="VENDOR", has_paid=True, paid_at=datetime.now(UTC))

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["payment"]["paid_count"] == 0
    assert data["payment"]["unpaid_count"] == 0
    assert data["payment"]["exempt_from_payment_count"] == 1
    assert data["guest_types"]["normal_count"] == 0


def test_meetup_stats_invariants_hold_across_mixed_fixture(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify the 3 documented invariants numerically against a fixture that
    mixes all 4 guest types, paid/unpaid, cancelled, and walk-ins:
      guest_types.normal_count == payment.paid_count + payment.unpaid_count
      attendance.total_rsvps == sum(guest_types.*)
      attendance.total_rsvps == sum(payment.*)
    """
    normal_paid = make_guest(session, mazmo_user_id=570, mazmo_handle="mix_normal_paid")
    normal_unpaid = make_guest(session, mazmo_user_id=571, mazmo_handle="mix_normal_unpaid")
    invited = make_guest(session, mazmo_user_id=572, mazmo_handle="mix_invited")
    vendor = make_guest(session, mazmo_user_id=573, mazmo_handle="mix_vendor")
    staff = make_guest(session, mazmo_user_id=574, mazmo_handle="mix_staff")
    cancelled_guest = make_guest(session, mazmo_user_id=575, mazmo_handle="mix_cancelled")
    walkin_guest = make_guest(session, mazmo_user_id=576, mazmo_handle="mix_walkin")

    make_rsvp(session, meetup=meetup, guest=normal_paid, has_paid=True, paid_at=datetime.now(UTC))
    make_rsvp(session, meetup=meetup, guest=normal_unpaid)
    make_rsvp(session, meetup=meetup, guest=invited, guest_type="INVITED")
    make_rsvp(session, meetup=meetup, guest=vendor, guest_type="VENDOR")
    make_rsvp(session, meetup=meetup, guest=staff, guest_type="STAFF")
    cancelled_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_guest)
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    walkin_rsvp = make_rsvp(session, meetup=meetup, guest=walkin_guest)
    walkin_rsvp.is_walkin = True
    session.add(walkin_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    normal_count = data["guest_types"]["normal_count"]
    invited_count = data["guest_types"]["invited_count"]
    vendor_count = data["guest_types"]["vendor_count"]
    staff_count = data["guest_types"]["staff_count"]
    paid_count = data["payment"]["paid_count"]
    unpaid_count = data["payment"]["unpaid_count"]
    exempt_count = data["payment"]["exempt_from_payment_count"]
    total_rsvps = data["attendance"]["total_rsvps"]

    assert normal_count == paid_count + unpaid_count
    assert total_rsvps == normal_count + invited_count + vendor_count + staff_count
    assert total_rsvps == paid_count + unpaid_count + exempt_count
    # Concrete values for this fixture, not just the invariants:
    assert normal_count == 2
    assert paid_count == 1
    assert unpaid_count == 1
    assert invited_count == 1
    assert vendor_count == 1
    assert staff_count == 1
    assert total_rsvps == 6  # 7 RSVPs made, 1 cancelled -> 6 active
```

- [ ] **Step 5: Run the new tests to confirm they pass**

Run: `pytest tests/test_guest_type.py -v`
Expected: all pass, including the 13 new stats tests.

- [ ] **Step 6: Run the full test suite to confirm nothing regressed**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Run basedpyright and ruff on changed files**

Run: `basedpyright app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py tests/test_guest_type.py`
Run: `ruff check app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py tests/test_guest_type.py`
Run: `ruff format app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py tests/test_guest_type.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app/routers/meetups.py app/openapi_examples/meetups_examples.py app/openapi_examples/_constants.py tests/test_guest_type.py
git commit -m "feat: add GET .../meetups/{meetup_id}/stats endpoint"
```

---

## Task 8: End-to-end scenarios

**Files:**
- Test: `tests/test_guest_type.py` (append 2 E2E tests)

**Interfaces:**
- Consumes: every endpoint and schema produced by Tasks 1-7. No production code changes in this task.

- [ ] **Step 1: Write the Eros scenario E2E test**

Append to `tests/test_guest_type.py`:

```python
# -- End-to-end scenarios ---------------------------------------------------


def test_eros_scenario_invited_vendor_staff_and_normal_guests_end_to_end(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Replicate the real scenario that motivated this feature end to end:
    sync guests, enable payment, classify 3 exempt guests, check everyone
    in, mark the remaining NORMAL guest as paid, cancel one paid guest,
    register a walk-in, then verify stats and the audit trail.
    """
    meetup = make_meetup(
        session, org=org, name="Alter Eros", mazmo_meetup_url="https://mazmo.net/test/alter-eros-e2e"
    )

    normal_guest = make_guest(session, mazmo_user_id=600, mazmo_handle="eros_normal")
    invited_guest = make_guest(session, mazmo_user_id=601, mazmo_handle="eros_invited")
    vendor_guest = make_guest(session, mazmo_user_id=602, mazmo_handle="eros_vendor")
    staff_guest = make_guest(session, mazmo_user_id=603, mazmo_handle="eros_staff")
    cancels_after_paying_guest = make_guest(session, mazmo_user_id=604, mazmo_handle="eros_cancels")
    walkin_guest = make_guest(session, mazmo_user_id=605, mazmo_handle="eros_walkin")

    make_rsvp(session, meetup=meetup, guest=normal_guest)
    make_rsvp(session, meetup=meetup, guest=invited_guest)
    make_rsvp(session, meetup=meetup, guest=vendor_guest)
    make_rsvp(session, meetup=meetup, guest=staff_guest)
    make_rsvp(session, meetup=meetup, guest=cancels_after_paying_guest)

    # 2. Admin enables requires_payment
    resp = client.patch(f"/organizations/{org.id}/meetups/{meetup.id}/enable-payment", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    # 3. Admin classifies invited/vendor/staff guests; normal_guest and
    #    cancels_after_paying_guest stay NORMAL.
    for guest, guest_type in (
        (invited_guest, "INVITED"),
        (vendor_guest, "VENDOR"),
        (staff_guest, "STAFF"),
    ):
        resp = client.patch(
            f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/type",
            headers=admin_headers,
            json={"guest_type": guest_type},
        )
        assert resp.status_code == status.HTTP_200_OK

    # 4. Staff checks in the 3 exempt guests without payment -> 200 each.
    for guest in (invited_guest, vendor_guest, staff_guest):
        resp = client.post(
            f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_200_OK

    # 5. Staff attempts check-in of a NORMAL guest without paying -> 409.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT

    # 6. Admin marks that guest as paid; retry check-in -> 200.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    # 7. A guest who already paid cancels their RSVP.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{cancels_after_paying_guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    cancelled_rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup.id)
        .where(MeetupRsvp.guest_id == cancels_after_paying_guest.id)
    ).one()
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    session.flush()

    # 8. Staff registers a walk-in.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{walkin_guest.id}/add-walkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_201_CREATED

    # 9. GET .../stats - verify every field against the scenario above.
    resp = client.get(f"/organizations/{org.id}/meetups/{meetup.id}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    # 6 active RSVPs: normal, invited, vendor, staff, walkin (5 original
    # non-cancelled + 1 walk-in); cancels_after_paying_guest is cancelled.
    assert data["attendance"]["total_rsvps"] == 6
    assert data["attendance"]["arrived_count"] == 4  # invited, vendor, staff, normal
    assert data["attendance"]["not_arrived_count"] == 2  # walkin guest not checked in yet
    assert data["attendance"]["walkin_count"] == 1

    assert data["cancellations"]["cancelled_count"] == 1
    assert data["cancellations"]["cancelled_but_paid_count"] == 1

    assert data["guest_types"] == {
        "normal_count": 2,  # normal_guest + walkin_guest (defaults to NORMAL)
        "invited_count": 1,
        "vendor_count": 1,
        "staff_count": 1,
    }

    assert data["payment"]["paid_count"] == 1  # normal_guest
    assert data["payment"]["unpaid_count"] == 1  # walkin_guest
    assert data["payment"]["exempt_from_payment_count"] == 3  # invited, vendor, staff

    # 10. GET .../events?type=GUEST_TYPE_CHANGED - verify 3 entries with
    #     the correct reason each.
    resp = client.get(
        f"/organizations/{org.id}/events/?type=GUEST_TYPE_CHANGED",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    events = resp.json()["events"]
    assert len(events) == 3
    reasons = {e["reason"] for e in events}
    assert reasons == {
        "Changed guest_type from NORMAL to INVITED",
        "Changed guest_type from NORMAL to VENDOR",
        "Changed guest_type from NORMAL to STAFF",
    }
```

- [ ] **Step 2: Write the reclassify-after-checkin E2E test**

Append to `tests/test_guest_type.py`:

```python
def test_reclassify_after_checkin_does_not_affect_already_checked_in_guest(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that reclassifying a guest who already checked in as NORMAL
    (and paid) does not retroactively change their check-in state.
    """
    meetup = make_meetup(
        session,
        org=org,
        name="Alter Retroactive Reclassify",
        mazmo_meetup_url="https://mazmo.net/test/alter-retro-reclassify",
        requires_payment=True,
    )
    guest = make_guest(session, mazmo_user_id=610, mazmo_handle="retro_reclassify_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    checkin_data = resp.json()
    arrival_order = checkin_data["arrival_order"]
    arrival_time = checkin_data["arrival_time"]

    # Admin reclassifies the guest as VENDOR after the fact.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "VENDOR"

    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.has_arrived is True
    assert rsvp.arrival_order == arrival_order
    assert rsvp.arrival_time.isoformat().replace("+00:00", "Z") == arrival_time or str(rsvp.arrival_time) in arrival_time

    # A second check-in attempt is still correctly rejected as "already
    # checked in" (409), not silently re-processed because of the
    # reclassification.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
```

Note on the `arrival_time` assertion: the check-in response serializes `arrival_time` as an ISO 8601 string with a timezone (e.g. `...Z` or `...+00:00` depending on FastAPI/Pydantic's datetime serialization), while `rsvp.arrival_time` after `session.exec(select(...)).one()` is a Python `datetime`. Do not assume an exact string format - when running this test for the first time, check the actual serialized value in `checkin_data["arrival_time"]` and simplify this comparison to whatever format FastAPI actually produces (most likely comparing `datetime.fromisoformat(arrival_time.replace("Z", "+00:00")) == rsvp.arrival_time`, which is the more robust form - prefer that over the string comparison above if the test fails on the raw string check).

- [ ] **Step 3: Run the new tests to confirm they pass**

Run: `pytest tests/test_guest_type.py -v -k "eros_scenario or reclassify_after_checkin"`
Expected: both E2E tests pass. If `test_reclassify_after_checkin_does_not_affect_already_checked_in_guest` fails on the `arrival_time` comparison, apply the fix described in Step 2's note (switch to `datetime.fromisoformat` comparison) and re-run.

- [ ] **Step 4: Run the full test suite one final time**

Run: `pytest -v`
Expected: every test in the suite passes.

- [ ] **Step 5: Run basedpyright, ruff check, and ruff format across the whole repo**

Run: `basedpyright`
Run: `ruff check`
Run: `ruff format`
Expected: no errors, no unformatted files.

- [ ] **Step 6: Commit**

```bash
git add tests/test_guest_type.py
git commit -m "test: add end-to-end guest_type payment exemption scenarios"
```

---

## Self-Review

**1. Spec coverage:**

- `GuestType` enum (4 values, docstring) - Task 1, Step 1. Covered.
- `MeetupRsvp.guest_type` field + docstring update - Task 1, Step 3. Covered.
- `EventType.GUEST_TYPE_CHANGED` - Task 1, Step 2. Covered.
- `EventTypeFilter.GUEST_TYPE_CHANGED` - Task 3, Step 3. Covered.
- Docstring updates (MeetupRsvp, sync.py comment, CLAUDE.md rule 2, mark_guest_paid) - Task 1 Steps 3-5, Task 6 Step 2. Covered.
- Migration with server_default backfill + manual verification - Task 2. Covered (with an explicit, documented substitution of manual psql verification for a pytest test, since this repo's test DB setup does not run Alembic).
- `PATCH .../guests/{guest_id}/type` endpoint (admin-only, body validation, audit log with reason, does not touch has_paid) - Task 5, Step 5. Covered.
- Check-in payment gate exemption (`rsvp.guest_type == GuestType.NORMAL.value` added to the condition, `.with_for_update()` untouched) - Task 6, Step 1. Covered.
- `GET .../meetups/{meetup_id}/stats` endpoint with all 4 grouped sub-objects and all 13 formula rows from the spec's table - Task 7, Step 3. Covered; every formula row maps 1:1 to a branch in the loop.
- `RsvpPublic.guest_type` - Task 3, Step 1. Covered.
- Out-of-scope items respected: `mark_guest_paid`/`undo_guest_payment`/enable-disable-payment untouched by this plan except the one docstring line specified; the pre-existing `EventTypeFilter` gap for other `EventType` values is not touched; `guest_type` is not added to `Guest`. Confirmed no task does any of these.
- Every test named in the spec's "Tests" section has a corresponding step: 5 unit (4 pytest + 1 manual migration verification), 9 PATCH integration, 7 check-in integration, 13 stats integration, 1 event filter, 1 sync-never-overwrites, 1 guest-list-includes, 2 E2E = 39 spec items, all present across Tasks 1-8 (38 pytest tests + 1 documented manual verification).

**2. Placeholder scan:** No "TBD"/"TODO"/"handle appropriately" language anywhere in the steps above. The one place this plan explicitly asks the implementer to check something at run time rather than hardcoding it (Task 6 Step 3's note about the ban endpoint's exact permission dependency, and Task 8 Step 2's note about `arrival_time` serialization format) is not a placeholder - it gives the exact code for both possible outcomes and explains how to tell which one applies, which is different from leaving a gap.

**3. Type consistency:** `GuestType` (Task 1) is used identically everywhere: `app/schemas/guests.py` (`GuestTypeUpdateRequest.guest_type: GuestType`), `app/routers/meetups.py` (`GuestType.NORMAL.value` in the check-in gate and the stats loop, `payload.guest_type.value` in the PATCH endpoint). `MeetupStatsPublic`/`AttendanceStats`/`CancellationStats`/`GuestTypeStats`/`PaymentStats` field names defined in Task 3 Step 2 are used with the exact same keyword names when constructed in Task 7 Step 3's `get_meetup_stats()`. `make_rsvp(..., guest_type: str = "NORMAL")` (Task 1 Step 6) is called consistently with plain string values (`"VENDOR"`, `"INVITED"`, `"STAFF"`) across Tasks 4, 6, 7, and 8 - never with a `GuestType` enum member, matching the helper's declared `str` type. `MeetupGuestPublic` (used as the PATCH endpoint's response_model in Task 5) is the same type already used by `add_walkin_guest()`, constructed with the same `GuestWithBanPublic`/`RsvpPublic` shape in both places.

**Known, explicitly documented deviation from the spec:** Task 6's `test_checkin_still_blocked_for_banned_guest_even_if_exempt_from_payment` keeps the spec's exact test name (for 1:1 traceability against the spec) but verifies different, factually-accurate behavior: this codebase has no ban-blocking gate in `checkin_guest()` today (confirmed by reading the full function and grepping the whole `app/` tree for `banned`/`OrganizationBan` usage), so a banned exempt guest is *not* blocked at check-in, contrary to the spec's assumption that such a gate already exists and merely needs to "compose correctly" with the new payment exemption. The plan documents this discrepancy prominently in the test's own docstring and explains why inventing a new ban-blocking gate is out of scope for this feature rather than silently doing so.
