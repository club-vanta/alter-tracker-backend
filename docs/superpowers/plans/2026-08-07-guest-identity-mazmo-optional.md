# Guest Identity Decoupled from Mazmo - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let guests exist in the system without a Mazmo account (manual creation, later linkable to Mazmo), and add an optional Instagram handle to every guest.

**Architecture:** `guests.id` (UUID) becomes the primary key instead of `guests.mazmo_user_id`. `mazmo_user_id` and `mazmo_handle` (renamed from `username`) become nullable. Every foreign key that pointed at `guests.mazmo_user_id` (`meetup_rsvps.guest_id`, `organization_bans.guest_id`, `event_log.guest_id`) is retargeted to `guests.id` (UUID). Two new creation endpoints (`POST /guests/mazmo`, `POST /guests/manual`) replace the old `POST /guests/`; `link-mazmo` / `unlink-mazmo` let staff attach or detach a Mazmo account after the fact without ever changing a guest's internal `id`.

**Tech Stack:** FastAPI, SQLModel (Pydantic v2 + SQLAlchemy), Alembic, PostgreSQL 18, pytest, structlog.

**Spec:** `docs/superpowers/specs/2026-08-07-guest-identity-mazmo-optional-design.md`

## Global Constraints

- Solo ASCII en codigo, comentarios y docs (CLAUDE.md regla 9) - no tildes, no em-dashes, no flechas Unicode. Usar `-` y `->`.
- Todos los `EventLog` se escriben en el mismo commit que el cambio de estado que registran.
- El sync nunca toca `has_arrived`, `arrival_time`, `arrival_order`, `checked_in_by_id`, `has_paid`, `paid_at`, `paid_by_id`.
- Nunca `DELETE` fisico de guests ni de ningun otro registro de identidad.
- Servicios (`app/services/`) no importan nada de `fastapi`.
- Tests corren contra Postgres real (fixture `session` en `tests/conftest.py`), nunca mockeada. El test suite construye el schema desde `SQLModel.metadata` (`create_all`), **no** corre Alembic - por eso la migracion (Tarea 1) se verifica aparte, contra la DB de dev via `alembic upgrade head`.
- Mensajes de error HTTP siguen el patron `"Cannot X: <contexto>. <que hacer>."` ya usado en el resto de los routers.

---

## File Structure

| File | Responsibility |
|------|-----------------|
| `alembic/versions/0016_guest_identity_mazmo_optional.py` | Schema migration: new `guests.id` PK, nullable `mazmo_user_id`/`mazmo_handle`, `instagram_username`, FK retargeting on 3 tables |
| `app/models/models.py` | `Guest`, `MeetupRsvp`, `OrganizationBan`, `EventLog`, `EventType` updated to match new schema |
| `tests/conftest.py` | `make_guest`/`make_rsvp`/`make_ban` helpers updated for the new fields |
| `app/schemas/guests.py` | `GuestPublic`, `GuestWithBanPublic`, `BannedGuestPublic`, new `CreateManualGuestRequest`/`LinkMazmoRequest`/`UpdateGuestRequest` |
| `app/schemas/events.py` | `EventGuestPublic`, `EventLogQuery.guest_id` typed as UUID |
| `app/services/sync.py` | Resolves `mazmo_user_id -> guest.id` before building `MeetupRsvp` rows |
| `app/routers/guests.py` | Full rewrite: `/guests/mazmo`, `/guests/manual`, `/guests/{guest_id}`, `/guests/by-mazmo-handle/{mazmo_handle}`, `link-mazmo`, `unlink-mazmo`, edit, search |
| `app/routers/meetups.py` | `{mazmo_user_id}` path params -> `{guest_id}` (UUID) on 5 endpoints |
| `app/routers/organizations.py` | `{mazmo_user_id}` path params -> `{guest_id}` (UUID) on ban/unban |
| `app/routers/events.py` | `guest_id` filter typed as UUID |
| `app/openapi_examples/_constants.py`, `_error_responses.py`, `guests_examples.py`, `meetups_examples.py`, `organizations_examples.py` | Example payloads updated to the new `GuestPublic` shape |
| `tests/test_guests.py`, `tests/test_meetups.py`, `tests/test_organizations.py`, `tests/test_events.py`, `tests/test_sync_service.py` | Updated/new tests |
| `/home/krapp/dev/vanta/docs/docs/business-logic/guests.md`, `/home/krapp/dev/vanta/docs/docs/technical/database-schema.md` | External docs repo, updated in the final task |

---

## Task 1: Migration 0016 (schema)

**Files:**
- Create: `alembic/versions/0016_guest_identity_mazmo_optional.py`

**Interfaces:**
- Produces: final DB schema that Task 2's `models.py` must match exactly (column names, nullability, types).

Verified constraint/index names (from migrations 0001, 0002, 0005, 0012, 0015 - all use Postgres' default auto-generated names since no explicit `name=` was passed):
- `guests_pkey` (PK on `mazmo_user_id`), `ix_guests_username`
- `meetup_rsvps_pkey` (composite PK), `meetup_rsvps_guest_id_fkey`
- `organization_bans_guest_id_fkey`, `ix_organization_bans_guest_id`
- `event_log_guest_id_fkey`, `ix_event_log_guest_id`, `ix_event_log_guest_meetup`

- [ ] **Step 1: Write the migration file**

```python
"""guest identity decoupled from mazmo + instagram_username

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-07

Guests could not exist without a Mazmo account: guests.mazmo_user_id was
the primary key, and every table that references a guest (meetup_rsvps,
organization_bans, event_log) had its guest_id FK pointing at it.

This migration:
  - Adds guests.id (UUID) as the new primary key.
  - Makes guests.mazmo_user_id nullable (still UNIQUE + indexed).
  - Renames guests.username to guests.mazmo_handle, makes it nullable.
  - Adds guests.instagram_username (nullable, free text).
  - Retargets meetup_rsvps.guest_id, organization_bans.guest_id, and
    event_log.guest_id from guests.mazmo_user_id to guests.id (UUID).

Existing rows keep their identity: guests.id is backfilled with a fresh
UUID per row, and every FK column is backfilled by joining the old
mazmo_user_id value against the new id before the old column is dropped.

See downgrade() for why this migration cannot be reverted once a guest
with mazmo_user_id IS NULL exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- 1. guests: add UUID surrogate PK --------------------------------------
    op.add_column("guests", sa.Column("id", sa.UUID(), nullable=True))
    op.execute("UPDATE guests SET id = gen_random_uuid()")
    op.alter_column("guests", "id", nullable=False)

    op.drop_constraint("guests_pkey", "guests", type_="primary")
    op.create_primary_key("guests_pkey", "guests", ["id"])

    # mazmo_user_id is no longer the PK: nullable, but still unique.
    op.alter_column("guests", "mazmo_user_id", nullable=True)
    op.create_index("ix_guests_mazmo_user_id", "guests", ["mazmo_user_id"], unique=True)

    # username -> mazmo_handle, nullable (a manual guest has no handle).
    op.alter_column("guests", "username", new_column_name="mazmo_handle", nullable=True)
    op.drop_index("ix_guests_username", table_name="guests")
    op.create_index("ix_guests_mazmo_handle", "guests", ["mazmo_handle"])

    op.add_column("guests", sa.Column("instagram_username", sa.String(length=64), nullable=True))

    # -- 2. meetup_rsvps: guest_id is part of the composite PK -----------------
    op.add_column("meetup_rsvps", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE meetup_rsvps mr
        SET guest_id_new = g.id
        FROM guests g
        WHERE mr.guest_id = g.mazmo_user_id
    """)
    op.alter_column("meetup_rsvps", "guest_id_new", nullable=False)
    op.drop_constraint("meetup_rsvps_pkey", "meetup_rsvps", type_="primary")
    op.drop_constraint("meetup_rsvps_guest_id_fkey", "meetup_rsvps", type_="foreignkey")
    op.drop_column("meetup_rsvps", "guest_id")
    op.alter_column("meetup_rsvps", "guest_id_new", new_column_name="guest_id")
    op.create_primary_key("meetup_rsvps_pkey", "meetup_rsvps", ["meetup_id", "guest_id"])
    op.create_foreign_key(
        "meetup_rsvps_guest_id_fkey", "meetup_rsvps", "guests", ["guest_id"], ["id"], ondelete="CASCADE"
    )

    # -- 3. organization_bans: guest_id is a plain indexed FK column -----------
    op.add_column("organization_bans", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE organization_bans ob
        SET guest_id_new = g.id
        FROM guests g
        WHERE ob.guest_id = g.mazmo_user_id
    """)
    op.alter_column("organization_bans", "guest_id_new", nullable=False)
    op.drop_constraint("organization_bans_guest_id_fkey", "organization_bans", type_="foreignkey")
    op.drop_index("ix_organization_bans_guest_id", table_name="organization_bans")
    op.drop_column("organization_bans", "guest_id")
    op.alter_column("organization_bans", "guest_id_new", new_column_name="guest_id")
    op.create_index("ix_organization_bans_guest_id", "organization_bans", ["guest_id"])
    op.create_foreign_key(
        "organization_bans_guest_id_fkey", "organization_bans", "guests", ["guest_id"], ["id"], ondelete="CASCADE"
    )

    # -- 4. event_log: guest_id is nullable, plain indexed FK column -----------
    op.add_column("event_log", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE event_log el
        SET guest_id_new = g.id
        FROM guests g
        WHERE el.guest_id = g.mazmo_user_id
    """)
    # No NOT NULL here: event_log.guest_id stays nullable (non-guest events).
    op.drop_constraint("event_log_guest_id_fkey", "event_log", type_="foreignkey")
    op.drop_index("ix_event_log_guest_meetup", table_name="event_log")
    op.drop_index("ix_event_log_guest_id", table_name="event_log")
    op.drop_column("event_log", "guest_id")
    op.alter_column("event_log", "guest_id_new", new_column_name="guest_id")
    op.create_index("ix_event_log_guest_id", "event_log", ["guest_id"])
    op.create_index("ix_event_log_guest_meetup", "event_log", ["guest_id", "meetup_id"])
    op.create_foreign_key(
        "event_log_guest_id_fkey", "event_log", "guests", ["guest_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    """
    Reverts guests.id back to mazmo_user_id as the PK.

    This is only possible if every guest still has a mazmo_user_id. A
    guest created via POST /guests/manual and never linked has
    mazmo_user_id IS NULL, which cannot become a PK value - reverting
    would either violate NOT NULL or silently drop that guest. Neither
    is acceptable, so this downgrade refuses to run rather than doing
    either silently.
    """
    manual_guests = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM guests WHERE mazmo_user_id IS NULL")
    ).scalar()
    if manual_guests:
        raise RuntimeError(
            f"Cannot downgrade migration 0016: {manual_guests} guest(s) have no "
            f"mazmo_user_id (created via POST /guests/manual and never linked via "
            f"PATCH /guests/{{id}}/link-mazmo). Reverting mazmo_user_id to a "
            f"NOT-NULL primary key would break or drop them. Link or remove those "
            f"guests before downgrading."
        )

    # -- event_log ---------------------------------------------------------
    op.drop_constraint("event_log_guest_id_fkey", "event_log", type_="foreignkey")
    op.drop_index("ix_event_log_guest_meetup", table_name="event_log")
    op.drop_index("ix_event_log_guest_id", table_name="event_log")
    op.add_column("event_log", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE event_log el
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE el.guest_id = g.id
    """)
    op.drop_column("event_log", "guest_id")
    op.alter_column("event_log", "guest_id_old", new_column_name="guest_id")
    op.create_index("ix_event_log_guest_id", "event_log", ["guest_id"])
    op.create_index("ix_event_log_guest_meetup", "event_log", ["guest_id", "meetup_id"])
    op.create_foreign_key(
        "event_log_guest_id_fkey", "event_log", "guests", ["guest_id"], ["mazmo_user_id"], ondelete="SET NULL"
    )

    # -- organization_bans ---------------------------------------------------
    op.drop_constraint("organization_bans_guest_id_fkey", "organization_bans", type_="foreignkey")
    op.drop_index("ix_organization_bans_guest_id", table_name="organization_bans")
    op.add_column("organization_bans", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE organization_bans ob
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE ob.guest_id = g.id
    """)
    op.alter_column("organization_bans", "guest_id_old", nullable=False)
    op.drop_column("organization_bans", "guest_id")
    op.alter_column("organization_bans", "guest_id_old", new_column_name="guest_id")
    op.create_index("ix_organization_bans_guest_id", "organization_bans", ["guest_id"])
    op.create_foreign_key(
        "organization_bans_guest_id_fkey",
        "organization_bans",
        "guests",
        ["guest_id"],
        ["mazmo_user_id"],
        ondelete="CASCADE",
    )

    # -- meetup_rsvps ---------------------------------------------------
    op.drop_constraint("meetup_rsvps_guest_id_fkey", "meetup_rsvps", type_="foreignkey")
    op.drop_constraint("meetup_rsvps_pkey", "meetup_rsvps", type_="primary")
    op.add_column("meetup_rsvps", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE meetup_rsvps mr
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE mr.guest_id = g.id
    """)
    op.alter_column("meetup_rsvps", "guest_id_old", nullable=False)
    op.drop_column("meetup_rsvps", "guest_id")
    op.alter_column("meetup_rsvps", "guest_id_old", new_column_name="guest_id")
    op.create_primary_key("meetup_rsvps_pkey", "meetup_rsvps", ["meetup_id", "guest_id"])
    op.create_foreign_key(
        "meetup_rsvps_guest_id_fkey", "meetup_rsvps", "guests", ["guest_id"], ["mazmo_user_id"], ondelete="CASCADE"
    )

    # -- guests ---------------------------------------------------
    op.drop_column("guests", "instagram_username")
    op.drop_index("ix_guests_mazmo_handle", table_name="guests")
    op.alter_column("guests", "mazmo_handle", new_column_name="username", nullable=False)
    op.create_index("ix_guests_username", "guests", ["username"])
    op.drop_index("ix_guests_mazmo_user_id", table_name="guests")
    op.alter_column("guests", "mazmo_user_id", nullable=False)
    op.drop_constraint("guests_pkey", "guests", type_="primary")
    op.create_primary_key("guests_pkey", "guests", ["mazmo_user_id"])
    op.drop_column("guests", "id")
```

- [ ] **Step 2: Apply against the dev database and inspect the schema**

```bash
db-start
db-migrate
```

Then, via `psql $DATABASE_URL` (or `devenv shell -- psql "$DATABASE_URL"`):

```sql
\d guests
\d meetup_rsvps
\d organization_bans
\d event_log
```

Expected: `guests.id` is the PK (uuid), `mazmo_user_id` and `mazmo_handle` are nullable, `instagram_username` exists. `meetup_rsvps.guest_id`, `organization_bans.guest_id`, `event_log.guest_id` are all `uuid` and FK to `guests(id)`.

- [ ] **Step 3: Verify downgrade works when no manual guests exist, then re-upgrade**

```bash
devenv shell -- alembic downgrade -1
devenv shell -- alembic upgrade head
```

Expected: both succeed without error (no manual guests exist yet in a fresh dev DB).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0016_guest_identity_mazmo_optional.py
git commit -m "feat: decouple guest identity from Mazmo (schema migration)"
```

---

## Task 2: Models (`app/models/models.py`)

**Files:**
- Modify: `app/models/models.py:41-57` (EventType), `:181-256` (MeetupRsvp, Guest)

**Interfaces:**
- Consumes: schema from Task 1.
- Produces: `Guest.id: uuid.UUID`, `Guest.mazmo_user_id: MazmoUserId | None`, `Guest.mazmo_handle: str | None`, `Guest.instagram_username: str | None`, `MeetupRsvp.guest_id: uuid.UUID`, `OrganizationBan.guest_id: uuid.UUID`, `EventLog.guest_id: uuid.UUID | None`, `EventType.GUEST_MAZMO_LINKED`, `EventType.GUEST_MAZMO_UNLINKED`. All later tasks import these.

- [ ] **Step 1: Add the two new EventType values**

In `app/models/models.py`, edit the `EventType` enum:

```python
class EventType(StrEnum):
    """
    Types of auditable events tracked in the event_log table.
    """

    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"
    BAN = "BAN"
    UNBAN = "UNBAN"
    MEETUP_FINALIZED = "MEETUP_FINALIZED"
    MEETUP_UNFINALIZED = "MEETUP_UNFINALIZED"
    WALKIN = "WALKIN"
    GUEST_CREATED = "GUEST_CREATED"
    GUEST_MAZMO_LINKED = "GUEST_MAZMO_LINKED"
    GUEST_MAZMO_UNLINKED = "GUEST_MAZMO_UNLINKED"
    PAYMENT_RECORDED = "PAYMENT_RECORDED"
    PAYMENT_REVOKED = "PAYMENT_REVOKED"
    PAYMENT_REQUIREMENT_ENABLED = "PAYMENT_REQUIREMENT_ENABLED"
    PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"
```

- [ ] **Step 2: Update `MeetupRsvp.guest_id`**

Replace:
```python
    guest_id: MazmoUserId = Field(foreign_key="guests.mazmo_user_id", primary_key=True, sa_type=Integer)
```
with:
```python
    guest_id: uuid.UUID = Field(foreign_key="guests.id", primary_key=True)
```

- [ ] **Step 3: Rewrite the `Guest` model**

Replace the entire `Guest` class:

```python
class Guest(SQLModel, table=True):
    """
    Core identity of a guest, optionally linked to a Mazmo account.

    PK is an internal UUID, independent of Mazmo - it never changes even
    if a guest is linked, unlinked, or re-linked to a different Mazmo
    account over time. mazmo_user_id/mazmo_handle are nullable: a guest
    created via POST /guests/manual has neither until someone links one
    via PATCH /guests/{id}/link-mazmo.

    displayname is highly mutable; mazmo_handle is effectively constant
    while a guest is linked.
    """

    __tablename__ = "guests"  # type: ignore[assignment]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    mazmo_user_id: MazmoUserId | None = Field(default=None, unique=True, index=True, sa_type=Integer)
    mazmo_handle: str | None = Field(default=None, index=True)
    displayname: str
    instagram_username: str | None = Field(default=None, max_length=64)

    # See Guest/Meetup comment in models - both rsvps and meetups relationships
    # are intentional (different use cases, same underlying data).
    rsvps: list["MeetupRsvp"] = Relationship(
        back_populates="guest",
        sa_relationship_kwargs={"overlaps": "meetups,guests"},
    )

    meetups: list["Meetup"] = Relationship(
        back_populates="guests",
        link_model=MeetupRsvp,
        sa_relationship_kwargs={"overlaps": "guest,rsvps,meetup"},
    )

    org_bans: list["OrganizationBan"] = Relationship(back_populates="guest")
```

- [ ] **Step 4: Update `OrganizationBan.guest_id`**

Replace:
```python
    guest_id: MazmoUserId = Field(foreign_key="guests.mazmo_user_id", index=True, sa_type=Integer)
```
with:
```python
    guest_id: uuid.UUID = Field(foreign_key="guests.id", index=True)
```

- [ ] **Step 5: Update `EventLog.guest_id`**

Replace:
```python
    guest_id: int | None = Field(default=None, foreign_key="guests.mazmo_user_id", index=True)
```
with:
```python
    guest_id: uuid.UUID | None = Field(default=None, foreign_key="guests.id", index=True)
```

- [ ] **Step 6: Check that `Integer` import is still used**

`Integer` (from `sqlalchemy`) is still needed for `Guest.mazmo_user_id`'s `sa_type=Integer`. Leave the import as-is.

- [ ] **Step 7: Verify with basedpyright**

```bash
basedpyright app/models/models.py
```

Expected: no new errors (existing unrelated errors, if any, are out of scope).

- [ ] **Step 8: Commit**

```bash
git add app/models/models.py
git commit -m "feat: update Guest/MeetupRsvp/OrganizationBan/EventLog models for optional Mazmo linking"
```

---

## Task 3: Test helpers (`tests/conftest.py`)

**Files:**
- Modify: `tests/conftest.py` (`make_guest`, `make_rsvp`, `make_ban`)

**Interfaces:**
- Consumes: `Guest`, `MeetupRsvp`, `OrganizationBan` from Task 2.
- Produces: `make_guest(session, *, mazmo_user_id=1, mazmo_handle="guestuser", displayname="Guest User", instagram_username=None) -> Guest`. `make_rsvp`/`make_ban` now key off `guest.id` instead of `guest.mazmo_user_id`.

- [ ] **Step 1: Update `make_guest`**

Replace:
```python
def make_guest(
    session: Session,
    *,
    mazmo_user_id: int = 1,
    username: str = "guestuser",
    displayname: str = "Guest User",
) -> Guest:
    """Helper to create a Guest (identity only) directly in the test session."""
    guest = Guest(
        mazmo_user_id=MazmoUserId(mazmo_user_id),
        username=username,
        displayname=displayname,
    )
    session.add(guest)
    session.flush()
    session.refresh(guest)
    return guest
```
with:
```python
def make_guest(
    session: Session,
    *,
    mazmo_user_id: int | None = 1,
    mazmo_handle: str | None = "guestuser",
    displayname: str = "Guest User",
    instagram_username: str | None = None,
) -> Guest:
    """
    Helper to create a Guest (identity only) directly in the test session.

    Defaults to a Mazmo-linked guest (matches most existing tests). Pass
    mazmo_user_id=None, mazmo_handle=None for a manual (no-Mazmo) guest.
    """
    guest = Guest(
        mazmo_user_id=MazmoUserId(mazmo_user_id) if mazmo_user_id is not None else None,
        mazmo_handle=mazmo_handle,
        displayname=displayname,
        instagram_username=instagram_username,
    )
    session.add(guest)
    session.flush()
    session.refresh(guest)
    return guest
```

- [ ] **Step 2: Update `make_rsvp`**

Replace the single line:
```python
        guest_id=guest.mazmo_user_id,
```
with:
```python
        guest_id=guest.id,
```
(inside `make_rsvp`'s `MeetupRsvp(...)` construction).

- [ ] **Step 3: Update `make_ban`**

Replace:
```python
    ban = OrganizationBan(
        org_id=org.id,
        guest_id=MazmoUserId(guest.mazmo_user_id),
        banned_by_id=banned_by.id,
        banned_at=datetime.now(UTC),
        reason=reason,
    )
```
with:
```python
    ban = OrganizationBan(
        org_id=org.id,
        guest_id=guest.id,
        banned_by_id=banned_by.id,
        banned_at=datetime.now(UTC),
        reason=reason,
    )
```

- [ ] **Step 4: Run the full test suite to confirm the expected failure surface**

```bash
run-tests
```

Expected: many failures across `test_guests.py`, `test_meetups.py`, `test_organizations.py`, `test_events.py`, `test_sync.py`, `test_sync_service.py` - these are expected until Tasks 4-9 land, since routers/schemas/sync.py still reference the old field names and old `{mazmo_user_id}` paths. `test_health.py`, `test_auth.py`, `test_staff.py`, `test_recovery.py`, `test_security.py`, `test_multi_tenant.py` (anything not touching guests) should still pass. Note the passing baseline here; it should only grow from this point on.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test: update guest/rsvp/ban fixtures for the new Guest schema"
```

---

## Task 4: Schemas (`app/schemas/guests.py`)

**Files:**
- Modify: `app/schemas/guests.py` (full rewrite of the affected classes)
- Modify: `app/schemas/__init__.py` (export new schemas)

**Interfaces:**
- Consumes: nothing (Pydantic-only, no model imports).
- Produces: `GuestPublic`, `GuestWithBanPublic`, `BannedGuestPublic` (all with `id: uuid.UUID`, `mazmo_user_id: int | None`, `mazmo_handle: str | None`, `instagram_username: str | None`); `CreateGuestRequest` (unchanged shape + `instagram_username`); `CreateManualGuestRequest`; `LinkMazmoRequest`; `UpdateGuestRequest`.

- [ ] **Step 1: Rewrite `app/schemas/guests.py`**

```python
"""
Guest and RSVP-related schemas.

A guest may or may not have a Mazmo account: mazmo_user_id and
mazmo_handle are both nullable. instagram_username is optional
regardless of Mazmo linkage. Guest identity is separate from ban status
- bans are per-org. GuestPublic contains identity only (no is_banned).
GuestWithBanPublic adds org-scoped ban status for endpoints that need it.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_leading_at(value: str | None) -> str | None:
    """Normalizes an Instagram handle so '@user' and 'user' store the same way."""
    if value is None:
        return None
    return value.removeprefix("@")


class GuestPublic(BaseModel):
    """
    A guest's identity (cached locally, may or may not have a Mazmo account).

    Identity-only - no RSVP or ban state. Bans are per-org and are
    included only in org-scoped endpoints via GuestWithBanPublic.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
    instagram_username: str | None


class GuestWithBanPublic(GuestPublic):
    """
    Guest identity with org-scoped ban status.

    Used in meetup guest lists and org-specific guest endpoints where
    the frontend needs to know if this guest is banned in the current org.
    """

    is_banned: bool = False


class GuestListResponse(BaseModel):
    """
    Response for listing all known guests (global, no org context).

    Returns identity-only data. For RSVP state at a specific meetup,
    use the /organizations/{org_id}/meetups/{id}/guests endpoint instead.
    """

    total: int
    guests: list[GuestPublic]


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


class MeetupGuestPublic(BaseModel):
    """
    Combined view of a guest at a specific meetup.

    guest includes ban status for this org (is_banned) so the frontend
    can render warnings at the door.
    """

    guest: GuestWithBanPublic
    rsvp: RsvpPublic


class MeetupGuestListResponse(BaseModel):
    """Response for listing guests at a specific meetup."""

    total: int
    guests: list[MeetupGuestPublic]


class CheckedInByPublic(BaseModel):
    """Minimal staff representation for check-in attribution."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class CheckInResponse(BaseModel):
    """Response after successfully checking in a guest."""

    guest: GuestPublic
    arrival_order: int
    arrival_time: datetime
    checked_in_by: CheckedInByPublic


class PaymentResponse(BaseModel):
    """Response after successfully marking a guest's entrance as paid."""

    guest: GuestPublic
    paid_at: datetime
    paid_by: CheckedInByPublic


# -- Guest creation and editing -------------------------------------------------


class CreateGuestRequest(BaseModel):
    """Request body for creating a guest by Mazmo username (POST /guests/mazmo)."""

    username: str = Field(
        min_length=1,
        max_length=255,
        description="Mazmo username to look up (e.g. 'cindydark')",
    )
    instagram_username: str | None = Field(default=None, max_length=64)

    _normalize_instagram = field_validator("instagram_username")(_strip_leading_at)


class CreateManualGuestRequest(BaseModel):
    """Request body for creating a guest without a Mazmo account (POST /guests/manual)."""

    displayname: str = Field(min_length=1, max_length=255)
    instagram_username: str | None = Field(default=None, max_length=64)

    _normalize_instagram = field_validator("instagram_username")(_strip_leading_at)


class LinkMazmoRequest(BaseModel):
    """Request body for linking an existing guest to a Mazmo account."""

    username: str = Field(min_length=1, max_length=255, description="Mazmo username to link")


class UpdateGuestRequest(BaseModel):
    """
    Request body for editing a guest's displayname and/or Instagram handle.

    Both fields are optional (partial update). Whether an omitted key
    means "don't touch" vs. an explicit null meaning "clear it" is
    resolved by the router via payload.model_fields_set, not by this
    schema - see update_guest() in app/routers/guests.py.
    """

    displayname: str | None = Field(default=None, min_length=1, max_length=255)
    instagram_username: str | None = Field(default=None, max_length=64)

    _normalize_instagram = field_validator("instagram_username")(_strip_leading_at)


# -- Ban-related schemas ---------------------------------------------------------


class BanGuestRequest(BaseModel):
    """Request body for banning a guest within an organization."""

    reason: str = Field(min_length=5, max_length=500)


class BannedGuestPublic(BaseModel):
    """
    Guest info with ban details, sourced from organization_bans.

    Used in the org-scoped banned guests list endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
    instagram_username: str | None
    banned_at: datetime
    banned_reason: str
    banned_by_id: int | None


class BannedGuestListResponse(BaseModel):
    """Response for listing all banned guests in an organization."""

    total: int
    guests: list[BannedGuestPublic]
```

- [ ] **Step 2: Export the new schemas from `app/schemas/__init__.py`**

Add `CreateManualGuestRequest`, `LinkMazmoRequest`, `UpdateGuestRequest` to both the `from app.schemas.guests import (...)` block and the `__all__` list, alongside the existing `CreateGuestRequest`.

- [ ] **Step 3: Verify with basedpyright**

```bash
basedpyright app/schemas/guests.py app/schemas/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add app/schemas/guests.py app/schemas/__init__.py
git commit -m "feat: add manual/link/unlink/edit guest schemas, rename username to mazmo_handle"
```

---

## Task 5: `app/services/sync.py`

**Files:**
- Modify: `app/services/sync.py`

**Interfaces:**
- Consumes: `Guest.id`, `Guest.mazmo_handle` from Task 2.
- Produces: `GuestSyncer.sync()` unchanged public signature; behavior unchanged (idempotent, never touches check-in/payment fields).

- [ ] **Step 1: Update `_build_guests` to use `mazmo_handle`**

Replace:
```python
            guests.append(
                Guest(
                    mazmo_user_id=user_id,
                    username=user.username,
                    displayname=user.displayname,
                )
            )
```
with:
```python
            guests.append(
                Guest(
                    mazmo_user_id=user_id,
                    mazmo_handle=user.username,
                    displayname=user.displayname,
                )
            )
```

- [ ] **Step 2: Add `_fetch_guest_id_map` and rewrite `_build_rsvps`**

Add this new method (place it right after `_build_guests`):

```python
    def _fetch_guest_id_map(self, mazmo_user_ids: list[MazmoUserId]) -> dict[MazmoUserId, uuid.UUID]:
        """
        Resolve mazmo_user_id -> internal guest.id for a batch of Mazmo IDs.

        Must run AFTER _upsert_guests, so it covers both guests that
        already existed and ones just inserted by that upsert.
        """
        rows = self._session.exec(
            select(Guest.mazmo_user_id, Guest.id).where(Guest.mazmo_user_id.in_(mazmo_user_ids))  # type: ignore[union-attr]
        ).all()
        return {mazmo_user_id: guest_id for mazmo_user_id, guest_id in rows}
```

Replace `_build_rsvps`:
```python
    def _build_rsvps(
        self,
        rsvps: dict[MazmoUserId, MazmoRsvpEntry],
        user_details: dict[MazmoUserId, MazmoUserEntry],
    ) -> list[MeetupRsvp]:
        """
        Build MeetupRsvp model instances from Mazmo data.
        Skips any RSVP whose user details couldn't be fetched.
        """
        result: list[MeetupRsvp] = []
        for user_id, rsvp in rsvps.items():
            if user_id not in user_details:
                continue
            result.append(
                MeetupRsvp(
                    meetup_id=self._meetup.id,
                    guest_id=user_id,
                    rsvp_time=rsvp.joinedAt,
                    cancelled_rsvp=False,
                )
            )
        return result
```
with:
```python
    def _build_rsvps(
        self,
        rsvps: dict[MazmoUserId, MazmoRsvpEntry],
        user_details: dict[MazmoUserId, MazmoUserEntry],
        guest_id_map: dict[MazmoUserId, uuid.UUID],
    ) -> list[MeetupRsvp]:
        """
        Build MeetupRsvp model instances from Mazmo data.
        Skips any RSVP whose user details couldn't be fetched.
        """
        result: list[MeetupRsvp] = []
        for user_id, rsvp in rsvps.items():
            if user_id not in user_details:
                continue
            guest_id = guest_id_map.get(user_id)
            if guest_id is None:
                log.warning("No internal guest id found for mazmo_user_id=%d after upsert - skipping", user_id)
                continue
            result.append(
                MeetupRsvp(
                    meetup_id=self._meetup.id,
                    guest_id=guest_id,
                    rsvp_time=rsvp.joinedAt,
                    cancelled_rsvp=False,
                )
            )
        return result
```

- [ ] **Step 3: Rewrite `sync()` to sequence guest upsert before RSVP building**

Replace:
```python
        guests_to_insert = self._build_guests(rsvps, user_details)
        rsvps_to_upsert = self._build_rsvps(rsvps, user_details)

        if not guests_to_insert:
            log.warning("All RSVPs lacked user detail - nothing inserted.")
            return SyncResponse(
                inserted=0,
                skipped=len(rsvps),
                total_in_db=self._count_rsvps(),
            )

        # First upsert guests (identity), then upsert RSVPs
        self._upsert_guests(guests_to_insert)
        inserted = self._upsert_rsvps(rsvps_to_upsert)
        self._update_cancelled_rsvps(set(rsvps.keys()))
```
with:
```python
        guests_to_insert = self._build_guests(rsvps, user_details)

        if not guests_to_insert:
            log.warning("All RSVPs lacked user detail - nothing inserted.")
            return SyncResponse(
                inserted=0,
                skipped=len(rsvps),
                total_in_db=self._count_rsvps(),
            )

        # Guests must be upserted (and their internal ids resolved) before
        # RSVPs can be built, since MeetupRsvp.guest_id is now the internal
        # UUID, not the raw mazmo_user_id.
        self._upsert_guests(guests_to_insert)
        guest_id_map = self._fetch_guest_id_map(list(rsvps.keys()))
        rsvps_to_upsert = self._build_rsvps(rsvps, user_details, guest_id_map)
        inserted = self._upsert_rsvps(rsvps_to_upsert)
        self._update_cancelled_rsvps(guest_id_map)
```

- [ ] **Step 4: Rewrite `_update_cancelled_rsvps`**

Replace:
```python
    def _update_cancelled_rsvps(self, current_ids: set[MazmoUserId]) -> None:
        """
        Flip `cancelled_rsvp` for RSVPs whose status changed FOR THIS MEETUP.
        - RSVPs no longer in Mazmo's list are marked as cancelled.
        Only writes rows where the status actually changed.
        """
        all_rsvps = self._session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id)).all()
        changed = 0
        for rsvp in all_rsvps:
            should_be_cancelled = rsvp.guest_id not in current_ids
            if rsvp.cancelled_rsvp != should_be_cancelled:
                rsvp.cancelled_rsvp = should_be_cancelled
                self._session.add(rsvp)
                changed += 1

        if changed:
            self._session.commit()
            log.info(
                "Updated cancelled_rsvp status for %d RSVP(s) in meetup %s",
                changed,
                self._meetup.id,
            )
```
with:
```python
    def _update_cancelled_rsvps(self, guest_id_map: dict[MazmoUserId, uuid.UUID]) -> None:
        """
        Flip `cancelled_rsvp` for RSVPs whose status changed FOR THIS MEETUP.
        - RSVPs whose guest is not in guest_id_map.values() (i.e. not in
          the latest Mazmo fetch) are marked as cancelled.
        Only writes rows where the status actually changed.

        NOTE (preserved from before this refactor): this also re-flags
        walk-in RSVPs as cancelled_rsvp=True if that guest never RSVPed
        on Mazmo, whether or not they have a Mazmo account. This was
        already true for Mazmo-linked walk-ins before this change; it now
        applies uniformly to manual (no-Mazmo) walk-ins too. cancelled_rsvp
        does not block check-in (has_arrived is separate and untouched by
        sync), so this is informational, not a functional regression.
        """
        current_guest_ids = set(guest_id_map.values())
        all_rsvps = self._session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id)).all()
        changed = 0
        for rsvp in all_rsvps:
            should_be_cancelled = rsvp.guest_id not in current_guest_ids
            if rsvp.cancelled_rsvp != should_be_cancelled:
                rsvp.cancelled_rsvp = should_be_cancelled
                self._session.add(rsvp)
                changed += 1

        if changed:
            self._session.commit()
            log.info(
                "Updated cancelled_rsvp status for %d RSVP(s) in meetup %s",
                changed,
                self._meetup.id,
            )
```

- [ ] **Step 5: Add the `uuid` import**

At the top of `app/services/sync.py`, add:
```python
import uuid
```
(alongside the existing `import structlog`).

- [ ] **Step 6: Run the sync service tests**

```bash
run-tests tests/test_sync_service.py -v
```

Expected: FAIL at this point (tests still use old field names / assumptions) - this is fixed in the next step.

- [ ] **Step 7: Update `tests/test_sync_service.py` assertions**

Open `tests/test_sync_service.py` and update every assertion that reads `guest.username` to `guest.mazmo_handle`, and every direct construction of `MeetupRsvp(guest_id=<mazmo_user_id>)` to use the created `Guest.id` instead (mirroring the `make_rsvp` fixture from Task 3). Follow the exact pattern already applied in Task 3's `make_rsvp` fix.

- [ ] **Step 8: Run the sync service tests again**

```bash
run-tests tests/test_sync_service.py tests/test_sync.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/services/sync.py tests/test_sync_service.py
git commit -m "feat: resolve mazmo_user_id to internal guest id during sync"
```

---

## Task 6: Events schema and router (`app/schemas/events.py`, `app/routers/events.py`)

**Files:**
- Modify: `app/schemas/events.py` (`EventGuestPublic`, `EventLogQuery.guest_id`)
- Modify: `app/routers/events.py` (`guest_id` query/path params, `_build_event_query`)

**Interfaces:**
- Consumes: `Guest.id`, `Guest.mazmo_user_id`, `Guest.mazmo_handle` from Task 2.
- Produces: `EventGuestPublic` with `id: uuid.UUID`; `_build_event_query(..., guest_id: uuid.UUID | None = None, ...)`.

- [ ] **Step 1: Update `EventGuestPublic` in `app/schemas/events.py`**

Replace:
```python
class EventGuestPublic(BaseModel):
    """
    Minimal guest representation in event logs.

    Provides enough info to identify the guest without full profile data.
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str
```
with:
```python
class EventGuestPublic(BaseModel):
    """
    Minimal guest representation in event logs.

    Provides enough info to identify the guest without full profile data.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
```

Add `import uuid` at the top of `app/schemas/events.py`.

- [ ] **Step 2: Update `EventLogQuery.guest_id`**

Replace:
```python
    guest_id: int | None = Field(
        default=None,
        description="Filter by guest's mazmo_user_id",
    )
```
with:
```python
    guest_id: uuid.UUID | None = Field(
        default=None,
        description="Filter by guest's internal id",
    )
```

- [ ] **Step 3: Update `app/routers/events.py`**

In `_build_event_query`, replace:
```python
    guest_id: int | None = None,
```
with:
```python
    guest_id: uuid.UUID | None = None,
```

In `list_all_events` and `list_staff_events`, replace both occurrences of:
```python
    guest_id: int | None = Query(default=None, description="Filter by guest mazmo_user_id"),
```
with:
```python
    guest_id: uuid.UUID | None = Query(default=None, description="Filter by guest id"),
```

In `list_guest_events`, replace:
```python
async def list_guest_events(
    org_id: uuid.UUID,
    guest_id: int,
```
with:
```python
async def list_guest_events(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
```

And replace the 404 detail message:
```python
            detail=(
                f"Guest with mazmo_user_id={guest_id} not found. "
                f"They may not have RSVPed to any meetup yet. "
                f"List all guests via GET /guests/ to find valid IDs."
            ),
```
with:
```python
            detail=(
                f"Guest with id={guest_id} not found. "
                f"They may not have RSVPed to any meetup yet. "
                f"List all guests via GET /guests/ to find valid IDs."
            ),
```

- [ ] **Step 4: Update `tests/test_events.py`**

Every call site that builds a guest-filtered query with an int `guest_id` (e.g. `?guest_id=1`) must instead pass the guest's `.id` (UUID) from `make_guest`. Update those call sites accordingly (search for `guest_id=` and `guest.mazmo_user_id` in the file).

- [ ] **Step 5: Run the events tests**

```bash
run-tests tests/test_events.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/events.py app/routers/events.py tests/test_events.py
git commit -m "feat: filter events by internal guest id instead of mazmo_user_id"
```

---

## Task 7: Guests router rewrite (`app/routers/guests.py`)

**Files:**
- Modify: `app/routers/guests.py` (full rewrite)
- Modify: `app/openapi_examples/_constants.py` (`GUEST_NORMAL`, `GUEST_NORMAL_2`, `GUEST_IN_ORG_NOT_BANNED`, `GUEST_IN_ORG_BANNED`, `GUEST_BANNED_FULL`, `EVENT_CHECKIN`, `EVENT_BAN`, `MEETUP_GUEST_WALKIN`, `CHECKIN_RESPONSE_EXAMPLE`, `PAYMENT_RESPONSE_EXAMPLE`)
- Modify: `app/openapi_examples/_error_responses.py` (`error_404_guest`, `error_404_guest_username` -> `error_404_guest_mazmo_handle`, `error_409_guest_already_exists`)
- Modify: `app/openapi_examples/guests_examples.py` (full rewrite)
- Modify: `tests/test_guests.py` (guest-identity sections rewritten; ban/unban sections only need the `mazmo_user_id` -> `id` swap, done in Task 9 since ban endpoints live in `organizations.py`)

**Interfaces:**
- Consumes: `Guest`, `EventLog`, `EventType`, all schemas from Task 4, `MazmoClient` (unchanged).
- Produces: all 8 `/guests/*` endpoints described in the spec.

- [ ] **Step 1: Rewrite `app/routers/guests.py`**

```python
"""
Guests router - global guest identity management.

POST  /guests/mazmo                          -> create a guest by Mazmo username (approved user)
POST  /guests/manual                         -> create a guest without a Mazmo account (approved user)
GET   /guests/                               -> list all known guests, optional ?q= search (approved user)
GET   /guests/{guest_id}                     -> get a single guest by internal id (approved user)
GET   /guests/by-mazmo-handle/{mazmo_handle} -> get a single guest by Mazmo handle (approved user)
PATCH /guests/{guest_id}/link-mazmo          -> link an existing guest to a Mazmo account (approved user)
PATCH /guests/{guest_id}/unlink-mazmo        -> unlink a guest's Mazmo account (approved user)
PATCH /guests/{guest_id}                     -> edit displayname/instagram_username (approved user)

Ban management is org-scoped and lives under /organizations/{org_id}/guests/...
"""

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
from app.models.models import EventLog, EventType, Guest, User
from app.openapi_examples.guests_examples import (
    CREATE_MANUAL_GUEST_REQUEST_EXAMPLES,
    CREATE_MANUAL_GUEST_RESPONSES,
    CREATE_MAZMO_GUEST_REQUEST_EXAMPLES,
    CREATE_MAZMO_GUEST_RESPONSES,
    GET_GUEST_BY_MAZMO_HANDLE_RESPONSES,
    GET_GUEST_RESPONSES,
    LINK_MAZMO_REQUEST_EXAMPLES,
    LINK_MAZMO_RESPONSES,
    LIST_GUESTS_RESPONSES,
    UNLINK_MAZMO_RESPONSES,
    UPDATE_GUEST_REQUEST_EXAMPLES,
    UPDATE_GUEST_RESPONSES,
)
from app.schemas import (
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestListResponse,
    GuestPublic,
    LinkMazmoRequest,
    UpdateGuestRequest,
)
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/guests", tags=["guests"])


def _get_guest_or_404(session: Session, guest_id: uuid.UUID) -> Guest:
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Guest with id={guest_id} does not exist in our database. "
                f"Guests are added when they RSVP to a meetup and we sync from Mazmo, "
                f"or when registered manually via POST /guests/mazmo or POST /guests/manual."
            ),
        )
    return guest


# -- Create guest by Mazmo username --------------------------------------------


@router.post(
    "/mazmo",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest by Mazmo username",
    responses=CREATE_MAZMO_GUEST_RESPONSES,
)
async def create_guest_from_mazmo(
    request: Annotated[CreateGuestRequest, Body(openapi_examples=CREATE_MAZMO_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Register a guest using their Mazmo username handle.

    Looks up the canonical Mazmo user ID and profile data automatically,
    so staff at the door only need to know the handle (e.g. "cindydark").

    Returns 404 if the username doesn't exist on Mazmo.
    Returns 409 if that mazmo_user_id is already registered.
    Returns 504 if Mazmo is unreachable.
    """
    try:
        async with MazmoClient(settings) as client:
            mazmo_user = await client.fetch_user_by_username(request.username)
    except MazmoNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(f"Cannot create guest: failed to connect to Mazmo API. Error: {exc}. Try again in a few moments."),
        ) from exc
    except MazmoAPIError as exc:
        if isinstance(exc.__cause__, httpx.HTTPStatusError) and exc.__cause__.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"Username '{request.username}' was not found on Mazmo. Check the spelling and try again."),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mazmo API returned an error: {exc}",
        ) from exc

    existing = session.exec(select(Guest).where(Guest.mazmo_user_id == mazmo_user.mazmo_user_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot create guest: mazmo_user_id={mazmo_user.mazmo_user_id} already exists "
                f"in the system as '{existing.displayname}' (id={existing.id}). "
                f"If you want to add them to a meetup, use "
                f"POST /organizations/{{org_id}}/meetups/{{meetup_id}}/guests/{existing.id}/add-walkin."
            ),
        )

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

    log.info(
        "Guest created by Mazmo username lookup",
        staff=staff.username,
        guest_id=str(guest.id),
        mazmo_handle=guest.mazmo_handle,
    )

    return guest


# -- Create guest without a Mazmo account --------------------------------------


@router.post(
    "/manual",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest without a Mazmo account",
    responses=CREATE_MANUAL_GUEST_RESPONSES,
)
async def create_manual_guest(
    request: Annotated[CreateManualGuestRequest, Body(openapi_examples=CREATE_MANUAL_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Register a guest who does not have a Mazmo account.

    No dedup check is performed: there is no external identifier to
    deduplicate against, so two guests with the same displayname can
    coexist. Use PATCH /guests/{guest_id}/link-mazmo later if this guest
    turns out to have (or creates) a Mazmo account.
    """
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

    log.info(
        "Manual guest created",
        staff=staff.username,
        guest_id=str(guest.id),
        displayname=guest.displayname,
    )

    return guest


# -- List guests ----------------------------------------------------------------


@router.get(
    "/",
    response_model=GuestListResponse,
    summary="List all known guests (identity only)",
    responses=LIST_GUESTS_RESPONSES,
)
async def list_guests(
    q: str | None = Query(
        default=None,
        description="Filter by displayname or mazmo_handle (case-insensitive substring)",
    ),
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> GuestListResponse:
    """List all guests in the system (identity only, no RSVP state)."""
    query = select(Guest)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Guest.displayname.ilike(pattern), Guest.mazmo_handle.ilike(pattern)))  # type: ignore[union-attr]

    guests = session.exec(query.order_by(Guest.displayname)).all()  # type: ignore[attr-defined]
    return GuestListResponse(
        total=len(guests),
        guests=[GuestPublic.model_validate(g) for g in guests],
    )


# -- Get single guest -------------------------------------------------------------


@router.get(
    "/{guest_id}",
    response_model=GuestPublic,
    summary="Get a single guest's identity",
    responses=GET_GUEST_RESPONSES,
)
async def get_guest(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their internal id."""
    return _get_guest_or_404(session, guest_id)


# -- Get guest by Mazmo handle ----------------------------------------------------


@router.get(
    "/by-mazmo-handle/{mazmo_handle}",
    response_model=GuestPublic,
    summary="Get a single guest by Mazmo handle",
    responses=GET_GUEST_BY_MAZMO_HANDLE_RESPONSES,
)
async def get_guest_by_mazmo_handle(
    mazmo_handle: str,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their Mazmo handle. Guests without Mazmo never match."""
    guest = session.exec(select(Guest).where(Guest.mazmo_handle == mazmo_handle)).first()
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No guest with Mazmo handle '{mazmo_handle}' found in the system. "
                f"They may not have RSVPed to any meetup yet, or may not have a Mazmo "
                f"account at all. Use POST /guests/mazmo to register them by handle, "
                f"or POST /guests/manual if they don't use Mazmo."
            ),
        )
    return guest


# -- Link an existing guest to a Mazmo account -------------------------------------


@router.patch(
    "/{guest_id}/link-mazmo",
    response_model=GuestPublic,
    summary="Link an existing guest to a Mazmo account",
    responses=LINK_MAZMO_RESPONSES,
)
async def link_guest_to_mazmo(
    guest_id: uuid.UUID,
    request: Annotated[LinkMazmoRequest, Body(openapi_examples=LINK_MAZMO_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Attach a Mazmo account to a guest created without one.

    Overwrites mazmo_user_id, mazmo_handle, and displayname with the
    Mazmo profile data. instagram_username is left untouched.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already linked, or if the Mazmo account
    is already linked to a different guest (no automatic merge).
    """
    guest = _get_guest_or_404(session, guest_id)

    if guest.mazmo_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: guest '{guest.displayname}' (id={guest_id}) is already "
                f"linked to Mazmo handle '@{guest.mazmo_handle}'. Unlink first via "
                f"PATCH /guests/{guest_id}/unlink-mazmo if you need to change it."
            ),
        )

    try:
        async with MazmoClient(settings) as client:
            mazmo_user = await client.fetch_user_by_username(request.username)
    except MazmoNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(f"Cannot link: failed to connect to Mazmo API. Error: {exc}. Try again in a few moments."),
        ) from exc
    except MazmoAPIError as exc:
        if isinstance(exc.__cause__, httpx.HTTPStatusError) and exc.__cause__.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"Username '{request.username}' was not found on Mazmo. Check the spelling and try again."),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mazmo API returned an error: {exc}",
        ) from exc

    existing = session.exec(select(Guest).where(Guest.mazmo_user_id == mazmo_user.mazmo_user_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: mazmo_user_id={mazmo_user.mazmo_user_id} already belongs to "
                f"guest '{existing.displayname}' (id={existing.id}). Merging two guests is not "
                f"supported; pick the correct guest or fix the Mazmo handle."
            ),
        )

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

    log.info(
        "Guest linked to Mazmo",
        staff=staff.username,
        guest_id=str(guest.id),
        mazmo_handle=guest.mazmo_handle,
    )

    return guest


# -- Unlink a guest's Mazmo account -----------------------------------------------


@router.patch(
    "/{guest_id}/unlink-mazmo",
    response_model=GuestPublic,
    summary="Unlink a guest's Mazmo account",
    responses=UNLINK_MAZMO_RESPONSES,
)
async def unlink_guest_mazmo(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Detach a guest's Mazmo account, e.g. to undo a link made by mistake.

    displayname is NOT reverted to any prior value (no name history is
    kept) - it stays as whatever it was, and can be corrected afterward
    via PATCH /guests/{guest_id}. The freed mazmo_user_id can be linked
    to this guest again, or to a different one.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently linked.
    """
    guest = _get_guest_or_404(session, guest_id)

    if guest.mazmo_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unlink: guest '{guest.displayname}' (id={guest_id}) "
                f"is not linked to a Mazmo account."
            ),
        )

    previous_mazmo_user_id = guest.mazmo_user_id
    guest.mazmo_user_id = None
    guest.mazmo_handle = None

    event = EventLog(
        event_type=EventType.GUEST_MAZMO_UNLINKED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest unlinked from Mazmo",
        staff=staff.username,
        guest_id=str(guest.id),
        previous_mazmo_user_id=previous_mazmo_user_id,
    )

    return guest


# -- Edit guest -------------------------------------------------------------------


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
    explicitly as null clears it (only instagram_username can be null -
    displayname is required, so sending it as null is rejected by the
    schema before this function runs). This distinction uses
    payload.model_fields_set, since payload.instagram_username is None in
    both the "omitted" and "explicitly cleared" cases.

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

- [ ] **Step 2: Update `app/openapi_examples/_constants.py`**

Replace `GUEST_NORMAL`:
```python
GUEST_NORMAL = {
    "mazmo_user_id": 12345,
    "username": "fiestero_feliz",
    "displayname": "Juan El Fiestero",
}
```
with (using a fixed example UUID reused across the file for consistency, matching the existing `ORG_UUID`/`MEETUP_UUID` pattern):
```python
GUEST_UUID = "e5f6a7b8-c9d0-1234-efab-345678901234"
GUEST_UUID_2 = "f6a7b8c9-d0e1-2345-fabc-456789012345"

GUEST_NORMAL = {
    "id": GUEST_UUID,
    "mazmo_user_id": 12345,
    "mazmo_handle": "fiestero_feliz",
    "displayname": "Juan El Fiestero",
    "instagram_username": "juan.fiestero",
}
```

Replace `GUEST_NORMAL_2`:
```python
GUEST_NORMAL_2 = {
    "mazmo_user_id": 12346,
    "username": "bailarina_nocturna",
    "displayname": "Ana Bailarina",
}
```
with:
```python
GUEST_NORMAL_2 = {
    "id": GUEST_UUID_2,
    "mazmo_user_id": 12346,
    "mazmo_handle": "bailarina_nocturna",
    "displayname": "Ana Bailarina",
    "instagram_username": None,
}
```

Add, right after `GUEST_NORMAL_2`, an example of a manual (no-Mazmo) guest, used by the new endpoints' examples:
```python
GUEST_MANUAL_UUID = "a7b8c9d0-e1f2-3456-abcd-567890123456"

GUEST_MANUAL = {
    "id": GUEST_MANUAL_UUID,
    "mazmo_user_id": None,
    "mazmo_handle": None,
    "displayname": "Recien Llegado Sin Mazmo",
    "instagram_username": "recien.llegado",
}
```

Replace `GUEST_IN_ORG_NOT_BANNED` (uses spread of `GUEST_NORMAL`, no change needed to the definition itself since it already spreads the updated dict) - **no edit required**, it inherits the new fields automatically via `**GUEST_NORMAL`.

Replace `GUEST_IN_ORG_BANNED`:
```python
GUEST_IN_ORG_BANNED = {
    "mazmo_user_id": 99999,
    "username": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "is_banned": True,
}
```
with:
```python
GUEST_BANNED_UUID = "b8c9d0e1-f2a3-4567-bcde-678901234567"

GUEST_IN_ORG_BANNED = {
    "id": GUEST_BANNED_UUID,
    "mazmo_user_id": 99999,
    "mazmo_handle": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "instagram_username": None,
    "is_banned": True,
}
```

Replace `GUEST_BANNED_FULL`:
```python
GUEST_BANNED_FULL = {
    "mazmo_user_id": 99999,
    "username": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "banned_at": TIMESTAMP_2024_03_20,
    "banned_reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
    "banned_by_id": 1,
}
```
with:
```python
GUEST_BANNED_FULL = {
    "id": GUEST_BANNED_UUID,
    "mazmo_user_id": 99999,
    "mazmo_handle": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "instagram_username": None,
    "banned_at": TIMESTAMP_2024_03_20,
    "banned_reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
    "banned_by_id": 1,
}
```

Replace the `"guest": {...}` blocks inside `EVENT_CHECKIN` and `EVENT_BAN` the same way (add `"id"`, rename `"username"` to `"mazmo_handle"`). For `EVENT_CHECKIN`'s guest block:
```python
    "guest": {
        "mazmo_user_id": 12345,
        "username": "fiestero_feliz",
        "displayname": "Juan El Fiestero",
    },
```
becomes:
```python
    "guest": {
        "id": GUEST_UUID,
        "mazmo_user_id": 12345,
        "mazmo_handle": "fiestero_feliz",
        "displayname": "Juan El Fiestero",
    },
```
Apply the equivalent transform to `EVENT_BAN`'s guest block (using `GUEST_BANNED_UUID` / `99999` / `"usuario_problematico"` / `"Persona Conflictiva"`).

Replace `MEETUP_GUEST_WALKIN`'s guest block:
```python
MEETUP_GUEST_WALKIN = {
    "guest": {
        "mazmo_user_id": 55555,
        "username": "recien_llegado",
        "displayname": "Recién Llegado",
        "is_banned": False,
    },
    "rsvp": RSVP_WALKIN,
}
```
with:
```python
GUEST_WALKIN_UUID = "c9d0e1f2-a3b4-5678-cdef-789012345678"

MEETUP_GUEST_WALKIN = {
    "guest": {
        "id": GUEST_WALKIN_UUID,
        "mazmo_user_id": 55555,
        "mazmo_handle": "recien_llegado",
        "displayname": "Recien Llegado",
        "instagram_username": None,
        "is_banned": False,
    },
    "rsvp": RSVP_WALKIN,
}
```

`PAYMENT_RESPONSE_EXAMPLE` and `CHECKIN_RESPONSE_EXAMPLE` both spread `GUEST_NORMAL` (`"guest": GUEST_NORMAL`) - **no edit required**, same reasoning as `GUEST_IN_ORG_NOT_BANNED`.

- [ ] **Step 3: Update `app/openapi_examples/_error_responses.py`**

Update `error_404_guest()`'s example detail text (replace `mazmo_user_id=99999` / `POST /guests/` references):
```python
                                "detail": (
                                    "Guest with mazmo_user_id=99999 does not exist in our database. "
                                    "Guests are added when they RSVP to a meetup and we sync from Mazmo, "
                                    "or when registered manually via POST /guests/. "
                                    "Try POST /meetups/{meetup_id}/sync, POST /guests/, or verify the mazmo_user_id."
```
becomes:
```python
                                "detail": (
                                    "Guest with id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 does not exist in our "
                                    "database. Guests are added when they RSVP to a meetup and we sync from "
                                    "Mazmo, or when registered manually via POST /guests/mazmo or "
                                    "POST /guests/manual. Try POST /meetups/{meetup_id}/sync, or verify the id."
```

Rename `error_404_guest_username()` to `error_404_guest_mazmo_handle()` and update its example detail (replace `POST /guests/` with `POST /guests/mazmo` or `POST /guests/manual`):
```python
def error_404_guest_username() -> ResponsesDict:
    """404 - No guest with that username in our system. Only for GET /guests/by-username/{username}."""
    return {
        404: {
            "description": "Guest not found by username",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_username_not_found": {
                            "summary": "Username not registered in this system",
                            "value": {
                                "detail": (
                                    "No guest with username 'unknownuser' found in the system. "
                                    "They may not have RSVPed to any meetup yet. "
                                    "Use POST /guests/ to register them if they're at the door."
                                )
```
becomes:
```python
def error_404_guest_mazmo_handle() -> ResponsesDict:
    """404 - No guest with that Mazmo handle. Only for GET /guests/by-mazmo-handle/{mazmo_handle}."""
    return {
        404: {
            "description": "Guest not found by Mazmo handle",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_handle_not_found": {
                            "summary": "Handle not registered in this system",
                            "value": {
                                "detail": (
                                    "No guest with Mazmo handle 'unknownuser' found in the system. "
                                    "They may not have RSVPed to any meetup yet, or may not have a Mazmo "
                                    "account at all. Use POST /guests/mazmo to register them by handle, "
                                    "or POST /guests/manual if they don't use Mazmo."
                                )
```
(keep the rest of the function body, including the closing brackets, unchanged - only the name and the quoted text change).

Update `error_409_guest_already_exists()`'s example detail (replace the `add-walkin` URL, which used a bare `mazmo_user_id`):
```python
                                "detail": (
                                    "Cannot create guest: mazmo_user_id=12345 already exists "
                                    "in the system as 'fiestero_feliz'. "
                                    "If you want to add them to a meetup, use "
                                    "POST /meetups/{meetup_id}/guests/12345/add-walkin."
```
becomes:
```python
                                "detail": (
                                    "Cannot create guest: mazmo_user_id=12345 already exists "
                                    "in the system as 'Juan El Fiestero' (id=e5f6a7b8-c9d0-1234-efab-345678901234). "
                                    "If you want to add them to a meetup, use "
                                    "POST /organizations/{org_id}/meetups/{meetup_id}/guests/"
                                    "e5f6a7b8-c9d0-1234-efab-345678901234/add-walkin."
```

- [ ] **Step 4: Rewrite `app/openapi_examples/guests_examples.py`**

```python
"""
OpenAPI examples for guests router endpoints.

Endpoints:
  POST  /guests/mazmo                          - Create a guest by Mazmo username
  POST  /guests/manual                         - Create a guest without a Mazmo account
  GET   /guests/                               - List all known guests, optional ?q= search
  GET   /guests/{guest_id}                     - Get a single guest by internal id
  GET   /guests/by-mazmo-handle/{mazmo_handle} - Get a single guest by Mazmo handle
  PATCH /guests/{guest_id}/link-mazmo          - Link an existing guest to a Mazmo account
  PATCH /guests/{guest_id}/unlink-mazmo        - Unlink a guest's Mazmo account
  PATCH /guests/{guest_id}                     - Edit displayname/instagram_username

Ban management lives in the organizations router:
  GET   /organizations/{org_id}/guests/banned
  PATCH /organizations/{org_id}/guests/{id}/ban
  PATCH /organizations/{org_id}/guests/{id}/unban
"""

from typing import Any

from app.openapi_examples._constants import (
    GUEST_MANUAL,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_guest,
    error_404_guest_mazmo_handle,
    error_404_mazmo_username_not_found,
    error_409_guest_already_exists,
    error_422_validation_username,
    error_504_mazmo_create_guest,
)

# -- POST /guests/mazmo ---------------------------------------------------------

CREATE_MAZMO_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "by_username": {
        "summary": "Look up by Mazmo handle",
        "description": "Staff knows the handle but not the numeric ID",
        "value": {"username": "cindydark"},
    },
    "with_instagram": {
        "summary": "Look up by Mazmo handle, with Instagram",
        "value": {"username": "cindydark", "instagram_username": "cindy.dark"},
    },
}

CREATE_MAZMO_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created from Mazmo profile",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Guest successfully registered from Mazmo lookup",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_mazmo_username_not_found(),
    **error_409_guest_already_exists(),
    **error_422_validation_username(),
    **error_504_mazmo_create_guest(),
}

# -- POST /guests/manual ---------------------------------------------------------

CREATE_MANUAL_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "basic": {
        "summary": "Guest without a Mazmo account",
        "description": "Someone at the door who doesn't have a Mazmo profile",
        "value": {"displayname": "Recien Llegado Sin Mazmo"},
    },
    "with_instagram": {
        "summary": "With Instagram handle",
        "value": {"displayname": "Recien Llegado Sin Mazmo", "instagram_username": "recien.llegado"},
    },
}

CREATE_MANUAL_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created without a Mazmo account",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Manual guest successfully registered",
                        "value": GUEST_MANUAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# -- GET /guests/ -----------------------------------------------------------------

LIST_GUESTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All known guests (global, no ban context)",
        "content": {
            "application/json": {
                "examples": {
                    "guests_list": {
                        "summary": "Known guests",
                        "description": "Identity only - ban status is org-scoped and not included here",
                        "value": {
                            "total": 2,
                            "guests": [GUEST_NORMAL_2, GUEST_NORMAL],
                        },
                    },
                    "empty": {
                        "summary": "No guests yet",
                        "description": "No guests in the system until a meetup is synced",
                        "value": {"total": 0, "guests": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# -- GET /guests/{guest_id} --------------------------------------------------------

GET_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest found",
        "content": {
            "application/json": {
                "examples": {
                    "guest": {
                        "summary": "Guest identity",
                        "description": "Ban status is not included - check org-scoped endpoints for ban info",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# -- GET /guests/by-mazmo-handle/{mazmo_handle} -------------------------------------

GET_GUEST_BY_MAZMO_HANDLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest found",
        "content": {
            "application/json": {
                "examples": {
                    "guest": {
                        "summary": "Guest identity",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest_mazmo_handle(),
}

# -- PATCH /guests/{guest_id}/link-mazmo ----------------------------------------

LINK_MAZMO_REQUEST_EXAMPLES: dict[str, Any] = {
    "link": {
        "summary": "Link to a Mazmo account",
        "value": {"username": "cindydark"},
    },
}

LINK_MAZMO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest linked to Mazmo",
        "content": {
            "application/json": {
                "examples": {
                    "linked": {
                        "summary": "Guest now has a Mazmo account attached",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
    **error_404_mazmo_username_not_found(),
    **error_409_guest_already_exists(),
    **error_504_mazmo_create_guest(),
}

# -- PATCH /guests/{guest_id}/unlink-mazmo --------------------------------------

UNLINK_MAZMO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest unlinked from Mazmo",
        "content": {
            "application/json": {
                "examples": {
                    "unlinked": {
                        "summary": "Guest no longer has a Mazmo account attached",
                        "value": GUEST_MANUAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# -- PATCH /guests/{guest_id} -----------------------------------------------------

UPDATE_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "update_name": {
        "summary": "Fix a typo in the display name",
        "value": {"displayname": "Nombre Corregido"},
    },
    "add_instagram": {
        "summary": "Add an Instagram handle after the fact",
        "value": {"instagram_username": "nuevo.handle"},
    },
    "clear_instagram": {
        "summary": "Remove the Instagram handle",
        "description": "Sending instagram_username as null clears it. Omitting the key entirely leaves it untouched.",
        "value": {"instagram_username": None},
    },
}

UPDATE_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest updated",
        "content": {
            "application/json": {
                "examples": {
                    "updated": {
                        "summary": "Guest identity after the edit",
                        "value": GUEST_NORMAL,
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

- [ ] **Step 5: Rewrite the guest-identity sections of `tests/test_guests.py`**

Replace the "List guests", "Get single guest", "Get guest by username" and "Create guest" sections (everything from the top of the file down to, but not including, `# -- Ban guest (org-scoped) --`) with:

```python
"""Tests for the /guests router and org-scoped ban endpoints.

Guest identity endpoints (/guests/*) are global.
Ban management is org-scoped: /organizations/{org_id}/guests/{id}/ban|unban
and /organizations/{org_id}/guests/banned.
"""

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, OrgRole
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_org, make_org_member

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
    meetup, or may not use Mazmo at all.
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
    assert data["displayname"] == "⛜️Lissandra⛜️"
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
    assert data["displayname"] == "⛜️Lissandra⛜️"


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
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_MAZMO_LINKED)
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
```

Leave everything from `# -- Ban guest (org-scoped) --` onward untouched for now - it is updated in Task 9 (it still references `guest.mazmo_user_id` in URLs, which is fixed there).

- [ ] **Step 6: Run the guest identity tests**

```bash
run-tests tests/test_guests.py -v -k "not ban and not unban and not Banned"
```

Expected: PASS. (Ban/unban tests in the same file still fail until Task 9 - that's expected.)

- [ ] **Step 7: Commit**

```bash
git add app/routers/guests.py app/openapi_examples/_constants.py app/openapi_examples/_error_responses.py app/openapi_examples/guests_examples.py tests/test_guests.py
git commit -m "feat: split guest creation into /guests/mazmo and /guests/manual, add link/unlink-mazmo and edit endpoints"
```

---

## Task 8: `meetups.py` path param rename

**Files:**
- Modify: `app/routers/meetups.py` (docstring, `_refetch_guest_or_500`, `list_meetup_guests`, `add_walkin_guest`, `checkin_guest`, `undo_checkin_guest`, `mark_guest_paid`, `undo_guest_payment`)
- Modify: `app/openapi_examples/meetups_examples.py` (comment headers only - the response bodies already come from `_constants.py`, updated in Task 7)
- Modify: `tests/test_meetups.py`

**Interfaces:**
- Consumes: `Guest.id` (UUID) from Task 2.
- Produces: same 6 endpoints, now keyed by `{guest_id}` (UUID) instead of `{mazmo_user_id}` (int).

- [ ] **Step 1: Update the module docstring**

Replace lines 9-13:
```
POST  /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/add-walkin  -> add walk-in (org member)
POST  /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/checkin     -> check in (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/undo-checkin -> undo check-in (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/payment      -> mark paid (org admin)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/payment/undo -> undo payment mark (org admin)
```
with (identical content - `{id}` already reads generically, no change needed here; leave as-is).

- [ ] **Step 2: Import `Guest.id` type and drop the unused `MazmoUserId` casts**

Replace:
```python
from app.domain_types import MazmoUserId
```
Remove this import entirely - after this task, `meetups.py` no longer constructs `MazmoUserId(...)` anywhere (guest_id is now a plain `uuid.UUID`).

- [ ] **Step 3: Update `_refetch_guest_or_500`**

Replace:
```python
def _refetch_guest_or_500(session: Session, mazmo_user_id: int, *, action: str) -> Guest:
    """Fetch a guest right after a commit succeeded, or raise 500 (should never happen)."""
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Internal error: {action} succeeded but guest record "
                f"(mazmo_user_id={mazmo_user_id}) could not be fetched. "
                f"This should never happen - please report this bug. "
                f"The {action} WAS recorded in the database."
            ),
        )
    return guest
```
with:
```python
def _refetch_guest_or_500(session: Session, guest_id: uuid.UUID, *, action: str) -> Guest:
    """Fetch a guest right after a commit succeeded, or raise 500 (should never happen)."""
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Internal error: {action} succeeded but guest record "
                f"(id={guest_id}) could not be fetched. "
                f"This should never happen - please report this bug. "
                f"The {action} WAS recorded in the database."
            ),
        )
    return guest
```

- [ ] **Step 4: Update `list_meetup_guests`**

Replace:
```python
    # Batch-load bans to avoid N+1 queries
    guest_ids = [rsvp.guest_id for rsvp in rsvps]
    banned_ids: set[int] = set()
    if guest_ids:
        bans = session.exec(
            select(OrganizationBan)
            .where(OrganizationBan.org_id == org_id)
            .where(OrganizationBan.guest_id.in_(guest_ids))  # type: ignore[union-attr]
        ).all()
        banned_ids = {int(ban.guest_id) for ban in bans}

    guests = [
        MeetupGuestPublic(
            guest=GuestWithBanPublic(
                mazmo_user_id=rsvp.guest.mazmo_user_id,
                username=rsvp.guest.username,
                displayname=rsvp.guest.displayname,
                is_banned=int(rsvp.guest_id) in banned_ids,
            ),
            rsvp=RsvpPublic.model_validate(rsvp),
        )
        for rsvp in rsvps
    ]
```
with:
```python
    # Batch-load bans to avoid N+1 queries
    guest_ids = [rsvp.guest_id for rsvp in rsvps]
    banned_ids: set[uuid.UUID] = set()
    if guest_ids:
        bans = session.exec(
            select(OrganizationBan)
            .where(OrganizationBan.org_id == org_id)
            .where(OrganizationBan.guest_id.in_(guest_ids))  # type: ignore[union-attr]
        ).all()
        banned_ids = {ban.guest_id for ban in bans}

    guests = [
        MeetupGuestPublic(
            guest=GuestWithBanPublic(
                id=rsvp.guest.id,
                mazmo_user_id=rsvp.guest.mazmo_user_id,
                mazmo_handle=rsvp.guest.mazmo_handle,
                displayname=rsvp.guest.displayname,
                instagram_username=rsvp.guest.instagram_username,
                is_banned=rsvp.guest_id in banned_ids,
            ),
            rsvp=RsvpPublic.model_validate(rsvp),
        )
        for rsvp in rsvps
    ]
```

- [ ] **Step 5: Update `add_walkin_guest`**

Replace the signature and path:
```python
@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/add-walkin",
    response_model=MeetupGuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Add a walk-in guest to this meetup",
    responses=ADD_WALKIN_RESPONSES,
)
async def add_walkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> MeetupGuestPublic:
```
with:
```python
@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/add-walkin",
    response_model=MeetupGuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Add a walk-in guest to this meetup",
    responses=ADD_WALKIN_RESPONSES,
)
async def add_walkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> MeetupGuestPublic:
```

Replace the body:
```python
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot add walk-in: guest mazmo_user_id={mazmo_user_id} does not exist in the system. "
                f"Register them first via POST /guests/ (username lookup) or sync a meetup they've RSVPed to."
            ),
        )

    existing = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup_id).where(MeetupRsvp.guest_id == mazmo_user_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"already has an RSVP for this meetup. "
                f"They may have RSVPed on Mazmo or been added as a walk-in previously."
            ),
        )

    rsvp = MeetupRsvp(
        meetup_id=meetup_id,
        guest_id=MazmoUserId(mazmo_user_id),
        rsvp_time=datetime.now(UTC),
        cancelled_rsvp=False,
        is_walkin=True,
    )

    event = EventLog(
        event_type=EventType.WALKIN,
        actor_id=staff.id,
        guest_id=MazmoUserId(mazmo_user_id),
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"already has an RSVP for this meetup (concurrent request)."
            ),
        ) from None
    session.refresh(rsvp)

    log.info(
        "Walk-in guest added",
        staff=staff.username,
        guest=guest.username,
        guest_id=mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
    )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()

    return MeetupGuestPublic(
        guest=GuestWithBanPublic(
            mazmo_user_id=guest.mazmo_user_id,
            username=guest.username,
            displayname=guest.displayname,
            is_banned=ban is not None,
        ),
        rsvp=RsvpPublic.model_validate(rsvp),
    )
```
with:
```python
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot add walk-in: guest id={guest_id} does not exist in the system. "
                f"Register them first via POST /guests/mazmo, POST /guests/manual, or sync "
                f"a meetup they've RSVPed to."
            ),
        )

    existing = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup_id).where(MeetupRsvp.guest_id == guest_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.displayname}' (id={guest_id}) "
                f"already has an RSVP for this meetup. "
                f"They may have RSVPed on Mazmo or been added as a walk-in previously."
            ),
        )

    rsvp = MeetupRsvp(
        meetup_id=meetup_id,
        guest_id=guest_id,
        rsvp_time=datetime.now(UTC),
        cancelled_rsvp=False,
        is_walkin=True,
    )

    event = EventLog(
        event_type=EventType.WALKIN,
        actor_id=staff.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.displayname}' (id={guest_id}) "
                f"already has an RSVP for this meetup (concurrent request)."
            ),
        ) from None
    session.refresh(rsvp)

    log.info(
        "Walk-in guest added",
        staff=staff.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
    )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()

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

- [ ] **Step 6: Update `checkin_guest`**

Replace the signature/path (`{mazmo_user_id}` -> `{guest_id}`, `mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)]` -> `guest_id: uuid.UUID`), then in the body replace every `mazmo_user_id` reference with `guest_id`, and every `rsvp.guest.username` / `.username` reference on a guest with `.displayname`. Concretely:

```python
@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/checkin",
    response_model=CheckInResponse,
    summary="Check in a guest at this meetup",
    responses=CHECKIN_RESPONSES,
)
async def checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> CheckInResponse:
```
becomes:
```python
@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/checkin",
    response_model=CheckInResponse,
    summary="Check in a guest at this meetup",
    responses=CHECKIN_RESPONSES,
)
async def checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> CheckInResponse:
```

In the body:
```python
    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .with_for_update()
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot check in: guest mazmo_user_id={mazmo_user_id} is not RSVPed. "
                f"Either: (1) they haven't RSVPed on Mazmo yet, "
                f"(2) RSVP list needs syncing - try POST /organizations/{org_id}/meetups/{meetup_id}/sync, "
                f"or (3) wrong ID - check GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is already checked in. They arrived at {rsvp.arrival_time} "
                f"(arrival #{rsvp.arrival_order}). "
                f"To undo this, use PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{mazmo_user_id}/undo-checkin."
            ),
        )

    if meetup.requires_payment and not rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"has not paid the entrance fee for '{meetup.name}'. "
                f"Mark the payment first via PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{mazmo_user_id}/payment."
            ),
        )

    rsvp.has_arrived = True
    rsvp.checked_in_by_id = staff.id

    event = EventLog(
        event_type=EventType.CHECK_IN,
        actor_id=staff.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()
    session.refresh(rsvp)

    guest = _refetch_guest_or_500(session, mazmo_user_id, action="check-in")

    log.info(
        "Check-in recorded",
        staff=staff.username,
        guest=guest.username,
        guest_id=mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        arrival_order=rsvp.arrival_order,
    )
```
becomes:
```python
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
                f"Cannot check in: guest id={guest_id} is not RSVPed. "
                f"Either: (1) they haven't RSVPed on Mazmo yet, "
                f"(2) RSVP list needs syncing - try POST /organizations/{org_id}/meetups/{meetup_id}/sync, "
                f"or (3) wrong ID - check GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.displayname}' (id={guest_id}) "
                f"is already checked in. They arrived at {rsvp.arrival_time} "
                f"(arrival #{rsvp.arrival_order}). "
                f"To undo this, use PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{guest_id}/undo-checkin."
            ),
        )

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

    rsvp.has_arrived = True
    rsvp.checked_in_by_id = staff.id

    event = EventLog(
        event_type=EventType.CHECK_IN,
        actor_id=staff.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()
    session.refresh(rsvp)

    guest = _refetch_guest_or_500(session, guest_id, action="check-in")

    log.info(
        "Check-in recorded",
        staff=staff.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        arrival_order=rsvp.arrival_order,
    )
```

- [ ] **Step 7: Update `undo_checkin_guest`**

Replace:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/undo-checkin",
    response_model=GuestPublic,
    summary="Undo a guest check-in at this meetup",
    responses=UNDO_CHECKIN_RESPONSES,
)
async def undo_checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    request: Annotated[UndoCheckInRequest, Body(openapi_examples=UNDO_CHECKIN_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> Guest:
    """
    Undo a check-in for a guest at this meetup.

    Use this when a guest was checked in by mistake. Requires a reason
    for the audit trail. Clears has_arrived, arrival_time, arrival_order,
    and checked_in_by_id.

    Returns 404 if guest not RSVPed to this meetup.
    Returns 409 if guest is not currently checked in.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot undo check-in: guest mazmo_user_id={mazmo_user_id} is not RSVPed "
                f"to this meetup. Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if not rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot undo check-in: guest '{rsvp.guest.username}' "
                f"(mazmo_user_id={mazmo_user_id}) is not currently checked in. "
                f"They may have been un-checked by someone else. "
                f"See event log: GET /organizations/{org_id}/events/meetups/{meetup_id}?type=CHECK_IN,UNDO_CHECK_IN"
            ),
        )

    rsvp.has_arrived = False
    rsvp.arrival_time = None
    rsvp.arrival_order = None
    rsvp.checked_in_by_id = None

    event = EventLog(
        event_type=EventType.UNDO_CHECK_IN,
        actor_id=staff.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=request.reason,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    guest = _refetch_guest_or_500(session, mazmo_user_id, action="undo")

    log.info(
        "Check-in undone",
        staff=staff.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        reason=request.reason,
    )
    return guest
```
with:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/undo-checkin",
    response_model=GuestPublic,
    summary="Undo a guest check-in at this meetup",
    responses=UNDO_CHECKIN_RESPONSES,
)
async def undo_checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    request: Annotated[UndoCheckInRequest, Body(openapi_examples=UNDO_CHECKIN_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> Guest:
    """
    Undo a check-in for a guest at this meetup.

    Use this when a guest was checked in by mistake. Requires a reason
    for the audit trail. Clears has_arrived, arrival_time, arrival_order,
    and checked_in_by_id.

    Returns 404 if guest not RSVPed to this meetup.
    Returns 409 if guest is not currently checked in.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == guest_id)
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot undo check-in: guest id={guest_id} is not RSVPed "
                f"to this meetup. Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if not rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot undo check-in: guest '{rsvp.guest.displayname}' "
                f"(id={guest_id}) is not currently checked in. "
                f"They may have been un-checked by someone else. "
                f"See event log: GET /organizations/{org_id}/events/meetups/{meetup_id}?type=CHECK_IN,UNDO_CHECK_IN"
            ),
        )

    rsvp.has_arrived = False
    rsvp.arrival_time = None
    rsvp.arrival_order = None
    rsvp.checked_in_by_id = None

    event = EventLog(
        event_type=EventType.UNDO_CHECK_IN,
        actor_id=staff.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=request.reason,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    guest = _refetch_guest_or_500(session, guest_id, action="undo")

    log.info(
        "Check-in undone",
        staff=staff.username,
        guest=guest.displayname,
        guest_id=str(guest.id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        reason=request.reason,
    )
    return guest
```

- [ ] **Step 8: Update `mark_guest_paid`**

Replace:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/payment",
    response_model=PaymentResponse,
    summary="Mark a guest's entrance as paid for this meetup (org admin only)",
    responses=MARK_PAYMENT_RESPONSES,
)
async def mark_guest_paid(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> PaymentResponse:
    """
    Mark a guest's entrance fee as paid for this specific meetup.

    Payment is handled externally by the organizer (cash, transfer, etc.);
    this just records that it happened so check-in can be enforced.

    Returns 409 if the meetup doesn't require payment, if the guest already
    paid, or if the meetup is finalized. Returns 404 if not RSVPed.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)
    _raise_if_finalized(meetup)

    if not meetup.requires_payment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot mark payment: meetup '{meetup.name}' does not require payment. "
                f"Enable it first via PATCH /organizations/{org_id}/meetups/{meetup_id}/enable-payment."
            ),
        )

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .with_for_update()
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot mark payment: guest mazmo_user_id={mazmo_user_id} is not RSVPed. "
                f"Either: (1) they haven't RSVPed on Mazmo yet, "
                f"(2) RSVP list needs syncing - try POST /organizations/{org_id}/meetups/{meetup_id}/sync, "
                f"or (3) wrong ID - check GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot mark payment: guest '{rsvp.guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"already paid at {rsvp.paid_at}. "
                f"To undo this, use PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{mazmo_user_id}/payment/undo."
            ),
        )

    rsvp.has_paid = True
    rsvp.paid_at = datetime.now(UTC)
    rsvp.paid_by_id = admin.id

    # Capture what the response/log need before commit expires rsvp's
    # attributes (including the .guest relationship). paid_at is set in
    # Python above, not by a DB trigger, so there's nothing to refresh.
    guest_public = GuestPublic.model_validate(rsvp.guest)
    guest_username = rsvp.guest.username
    paid_at = rsvp.paid_at

    event = EventLog(
        event_type=EventType.PAYMENT_RECORDED,
        actor_id=admin.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    log.info(
        "Payment recorded",
        admin=admin.username,
        guest=guest_username,
        guest_id=mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
    )

    return PaymentResponse(
        guest=guest_public,
        paid_at=paid_at,
        paid_by=CheckedInByPublic.model_validate(admin),
    )
```
with:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/payment",
    response_model=PaymentResponse,
    summary="Mark a guest's entrance as paid for this meetup (org admin only)",
    responses=MARK_PAYMENT_RESPONSES,
)
async def mark_guest_paid(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> PaymentResponse:
    """
    Mark a guest's entrance fee as paid for this specific meetup.

    Payment is handled externally by the organizer (cash, transfer, etc.);
    this just records that it happened so check-in can be enforced.

    Returns 409 if the meetup doesn't require payment, if the guest already
    paid, or if the meetup is finalized. Returns 404 if not RSVPed.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)
    _raise_if_finalized(meetup)

    if not meetup.requires_payment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot mark payment: meetup '{meetup.name}' does not require payment. "
                f"Enable it first via PATCH /organizations/{org_id}/meetups/{meetup_id}/enable-payment."
            ),
        )

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
                f"Cannot mark payment: guest id={guest_id} is not RSVPed. "
                f"Either: (1) they haven't RSVPed on Mazmo yet, "
                f"(2) RSVP list needs syncing - try POST /organizations/{org_id}/meetups/{meetup_id}/sync, "
                f"or (3) wrong ID - check GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot mark payment: guest '{rsvp.guest.displayname}' (id={guest_id}) "
                f"already paid at {rsvp.paid_at}. "
                f"To undo this, use PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{guest_id}/payment/undo."
            ),
        )

    rsvp.has_paid = True
    rsvp.paid_at = datetime.now(UTC)
    rsvp.paid_by_id = admin.id

    # Capture what the response/log need before commit expires rsvp's
    # attributes (including the .guest relationship). paid_at is set in
    # Python above, not by a DB trigger, so there's nothing to refresh.
    guest_public = GuestPublic.model_validate(rsvp.guest)
    guest_displayname = rsvp.guest.displayname
    paid_at = rsvp.paid_at

    event = EventLog(
        event_type=EventType.PAYMENT_RECORDED,
        actor_id=admin.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    log.info(
        "Payment recorded",
        admin=admin.username,
        guest=guest_displayname,
        guest_id=str(guest_id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
    )

    return PaymentResponse(
        guest=guest_public,
        paid_at=paid_at,
        paid_by=CheckedInByPublic.model_validate(admin),
    )
```

- [ ] **Step 9: Update `undo_guest_payment`**

Replace:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/payment/undo",
    response_model=GuestPublic,
    summary="Undo a guest's payment mark for this meetup (org admin only)",
    responses=UNDO_PAYMENT_RESPONSES,
)
async def undo_guest_payment(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    request: Annotated[UndoPaymentRequest, Body(openapi_examples=UNDO_PAYMENT_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> Guest:
    """
    Undo a payment mark for a guest at this meetup.

    Use this when a payment was marked by mistake. Requires a reason for
    the audit trail. Clears has_paid, paid_at, and paid_by_id.

    Returns 404 if guest not RSVPed to this meetup.
    Returns 409 if the guest is not currently marked as paid.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .with_for_update()
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot undo payment: guest mazmo_user_id={mazmo_user_id} is not RSVPed "
                f"to this meetup. Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if not rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot undo payment: guest '{rsvp.guest.username}' "
                f"(mazmo_user_id={mazmo_user_id}) is not currently marked as paid. "
                f"They may have had their payment undone by someone else. "
                f"See event log: GET /organizations/{org_id}/events/meetups/{meetup_id}"
                f"?type=PAYMENT_RECORDED,PAYMENT_REVOKED"
            ),
        )

    rsvp.has_paid = False
    rsvp.paid_at = None
    rsvp.paid_by_id = None

    event = EventLog(
        event_type=EventType.PAYMENT_REVOKED,
        actor_id=admin.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=request.reason,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    guest = _refetch_guest_or_500(session, mazmo_user_id, action="undo")

    log.info(
        "Payment undone",
        admin=admin.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        reason=request.reason,
    )
    return guest
```
with:
```python
@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/payment/undo",
    response_model=GuestPublic,
    summary="Undo a guest's payment mark for this meetup (org admin only)",
    responses=UNDO_PAYMENT_RESPONSES,
)
async def undo_guest_payment(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    guest_id: uuid.UUID,
    request: Annotated[UndoPaymentRequest, Body(openapi_examples=UNDO_PAYMENT_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> Guest:
    """
    Undo a payment mark for a guest at this meetup.

    Use this when a payment was marked by mistake. Requires a reason for
    the audit trail. Clears has_paid, paid_at, and paid_by_id.

    Returns 404 if guest not RSVPed to this meetup.
    Returns 409 if the guest is not currently marked as paid.
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
                f"Cannot undo payment: guest id={guest_id} is not RSVPed "
                f"to this meetup. Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if not rsvp.has_paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot undo payment: guest '{rsvp.guest.displayname}' "
                f"(id={guest_id}) is not currently marked as paid. "
                f"They may have had their payment undone by someone else. "
                f"See event log: GET /organizations/{org_id}/events/meetups/{meetup_id}"
                f"?type=PAYMENT_RECORDED,PAYMENT_REVOKED"
            ),
        )

    rsvp.has_paid = False
    rsvp.paid_at = None
    rsvp.paid_by_id = None

    event = EventLog(
        event_type=EventType.PAYMENT_REVOKED,
        actor_id=admin.id,
        guest_id=guest_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=request.reason,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    guest = _refetch_guest_or_500(session, guest_id, action="undo")

    log.info(
        "Payment undone",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest.id),
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        reason=request.reason,
    )
    return guest
```

The `Field` import from `pydantic` stays - it is still used by `UndoCheckInRequest.reason` and `UndoPaymentRequest.reason`.

- [ ] **Step 10: Verify with basedpyright and grep**

```bash
basedpyright app/routers/meetups.py
grep -n "mazmo_user_id\|\.username\b" app/routers/meetups.py
```

Expected: `basedpyright` clean; the `grep` should return nothing (every reference has moved to `guest_id`/`.displayname`), confirming no leftover references.

- [ ] **Step 11: Update `tests/test_meetups.py`**

Every test that builds a URL like `f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.mazmo_user_id}/checkin"` (and the equivalent for add-walkin, undo-checkin, payment, payment/undo) must use `guest.id` instead. Every assertion on response JSON that reads `data["guest"]["username"]` must read `data["guest"]["mazmo_handle"]` instead. Search the file for `mazmo_user_id` and `["username"]` and update each occurrence following that pattern.

- [ ] **Step 12: Run the meetups tests**

```bash
run-tests tests/test_meetups.py -v
```

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add app/routers/meetups.py tests/test_meetups.py
git commit -m "feat: use internal guest id instead of mazmo_user_id in meetup guest endpoints"
```

---

## Task 9: `organizations.py` path param rename (ban/unban)

**Files:**
- Modify: `app/routers/organizations.py` (`list_banned_guests`, `ban_guest`, `unban_guest`)
- Modify: `tests/test_guests.py` (ban/unban sections left untouched by Task 7)
- Modify: `tests/test_organizations.py` if it references guest ban endpoints

**Interfaces:**
- Consumes: `Guest.id` (UUID) from Task 2.
- Produces: `PATCH /organizations/{org_id}/guests/{guest_id}/ban`, `PATCH /organizations/{org_id}/guests/{guest_id}/unban`.

- [ ] **Step 1: Remove the unused `MazmoUserId` import**

Remove:
```python
from app.domain_types import MazmoUserId
```
(no longer needed once `guest_id` is a plain `uuid.UUID`).

- [ ] **Step 2: Update `list_banned_guests`**

Replace:
```python
    guests = []
    for ban in bans:
        guest = session.get(Guest, ban.guest_id)
        if guest:
            guests.append(
                BannedGuestPublic(
                    mazmo_user_id=guest.mazmo_user_id,
                    username=guest.username,
                    displayname=guest.displayname,
                    banned_at=ban.banned_at,
                    banned_reason=ban.reason,
                    banned_by_id=ban.banned_by_id,
                )
            )
```
with:
```python
    guests = []
    for ban in bans:
        guest = session.get(Guest, ban.guest_id)
        if guest:
            guests.append(
                BannedGuestPublic(
                    id=guest.id,
                    mazmo_user_id=guest.mazmo_user_id,
                    mazmo_handle=guest.mazmo_handle,
                    displayname=guest.displayname,
                    instagram_username=guest.instagram_username,
                    banned_at=ban.banned_at,
                    banned_reason=ban.reason,
                    banned_by_id=ban.banned_by_id,
                )
            )
```

- [ ] **Step 3: Update `ban_guest`**

Replace the path and signature:
```python
@router.patch(
    "/organizations/{org_id}/guests/{mazmo_user_id}/ban",
    response_model=BannedGuestPublic,
    summary="Ban a guest in this organization (org admin only)",
    responses=BAN_GUEST_RESPONSES,
)
async def ban_guest(
    org_id: uuid.UUID,
    mazmo_user_id: int,
    payload: Annotated[BanGuestRequest, Body(openapi_examples=BAN_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> BannedGuestPublic:
```
with:
```python
@router.patch(
    "/organizations/{org_id}/guests/{guest_id}/ban",
    response_model=BannedGuestPublic,
    summary="Ban a guest in this organization (org admin only)",
    responses=BAN_GUEST_RESPONSES,
)
async def ban_guest(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    payload: Annotated[BanGuestRequest, Body(openapi_examples=BAN_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> BannedGuestPublic:
```

Replace the body:
```python
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot ban guest: mazmo_user_id={mazmo_user_id} does not exist in our database. "
                f"Guests are added via Mazmo sync or manually via POST /guests/."
            ),
        )

    existing_ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()
    if existing_ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot ban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is already banned in this organization. They were banned on {existing_ban.banned_at} "
                f"for reason: '{existing_ban.reason}'. "
                f"To update the ban reason, unban first via "
                f"PATCH /organizations/{org_id}/guests/{mazmo_user_id}/unban, then re-ban."
            ),
        )

    ban = OrganizationBan(
        org_id=org_id,
        guest_id=MazmoUserId(mazmo_user_id),
        banned_by_id=admin.id,
        banned_at=datetime.now(UTC),
        reason=payload.reason,
    )
    event = EventLog(
        event_type=EventType.BAN,
        actor_id=admin.id,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=org_id,
        reason=payload.reason,
    )

    session.add(ban)
    session.add(event)
    session.commit()
    session.refresh(ban)

    log.info(
        "Guest banned",
        admin=admin.username,
        guest=guest.username,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=str(org_id),
        reason=payload.reason,
    )

    return BannedGuestPublic(
        mazmo_user_id=guest.mazmo_user_id,
        username=guest.username,
        displayname=guest.displayname,
        banned_at=ban.banned_at,
        banned_reason=ban.reason,
        banned_by_id=ban.banned_by_id,
    )
```
with:
```python
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot ban guest: id={guest_id} does not exist in our database. "
                f"Guests are added via Mazmo sync or manually via POST /guests/mazmo or POST /guests/manual."
            ),
        )

    existing_ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()
    if existing_ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot ban guest: '{guest.displayname}' (id={guest_id}) "
                f"is already banned in this organization. They were banned on {existing_ban.banned_at} "
                f"for reason: '{existing_ban.reason}'. "
                f"To update the ban reason, unban first via "
                f"PATCH /organizations/{org_id}/guests/{guest_id}/unban, then re-ban."
            ),
        )

    ban = OrganizationBan(
        org_id=org_id,
        guest_id=guest_id,
        banned_by_id=admin.id,
        banned_at=datetime.now(UTC),
        reason=payload.reason,
    )
    event = EventLog(
        event_type=EventType.BAN,
        actor_id=admin.id,
        guest_id=guest_id,
        org_id=org_id,
        reason=payload.reason,
    )

    session.add(ban)
    session.add(event)
    session.commit()
    session.refresh(ban)

    log.info(
        "Guest banned",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        org_id=str(org_id),
        reason=payload.reason,
    )

    return BannedGuestPublic(
        id=guest.id,
        mazmo_user_id=guest.mazmo_user_id,
        mazmo_handle=guest.mazmo_handle,
        displayname=guest.displayname,
        instagram_username=guest.instagram_username,
        banned_at=ban.banned_at,
        banned_reason=ban.reason,
        banned_by_id=ban.banned_by_id,
    )
```

- [ ] **Step 4: Update `unban_guest`**

Replace:
```python
@router.patch(
    "/organizations/{org_id}/guests/{mazmo_user_id}/unban",
    response_model=GuestPublic,
    summary="Unban a guest in this organization (org admin only)",
    responses=UNBAN_GUEST_RESPONSES,
)
async def unban_guest(
    org_id: uuid.UUID,
    mazmo_user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> GuestPublic:
    """
    Unban a guest in this organization. Removes the ban record (history stays in event log).

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently banned in this organization.
    """
    _get_org_or_404(session, org_id)

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot unban guest: mazmo_user_id={mazmo_user_id} does not exist. "
                f"Double-check the ID via GET /guests/ or GET /organizations/{org_id}/guests/banned."
            ),
        )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()
    if not ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is not currently banned in this organization. "
                f"They may have been unbanned by another admin. "
                f"Check audit trail at GET /organizations/{org_id}/events/guests/{mazmo_user_id}."
            ),
        )

    event = EventLog(
        event_type=EventType.UNBAN,
        actor_id=admin.id,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=org_id,
    )

    session.delete(ban)
    session.add(event)
    session.commit()

    log.info(
        "Guest unbanned",
        admin=admin.username,
        guest=guest.username,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=str(org_id),
    )

    return GuestPublic.model_validate(guest)
```
with:
```python
@router.patch(
    "/organizations/{org_id}/guests/{guest_id}/unban",
    response_model=GuestPublic,
    summary="Unban a guest in this organization (org admin only)",
    responses=UNBAN_GUEST_RESPONSES,
)
async def unban_guest(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> GuestPublic:
    """
    Unban a guest in this organization. Removes the ban record (history stays in event log).

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently banned in this organization.
    """
    _get_org_or_404(session, org_id)

    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot unban guest: id={guest_id} does not exist. "
                f"Double-check the ID via GET /guests/ or GET /organizations/{org_id}/guests/banned."
            ),
        )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()
    if not ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unban guest: '{guest.displayname}' (id={guest_id}) "
                f"is not currently banned in this organization. "
                f"They may have been unbanned by another admin. "
                f"Check audit trail at GET /organizations/{org_id}/events/guests/{guest_id}."
            ),
        )

    event = EventLog(
        event_type=EventType.UNBAN,
        actor_id=admin.id,
        guest_id=guest_id,
        org_id=org_id,
    )

    session.delete(ban)
    session.add(event)
    session.commit()

    log.info(
        "Guest unbanned",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        org_id=str(org_id),
    )

    return GuestPublic.model_validate(guest)
```

- [ ] **Step 5: Verify with basedpyright and grep**

```bash
basedpyright app/routers/organizations.py
grep -n "mazmo_user_id\|\.username\b" app/routers/organizations.py
```

Expected: clean (the only remaining `.username` hits should be on `User` objects like `admin.username`/`target.username`, not `Guest`).

- [ ] **Step 6: Update the ban/unban sections of `tests/test_guests.py`**

In the sections from `# -- Ban guest (org-scoped) --` through the end of the file (left untouched by Task 7), replace every `guest.mazmo_user_id` used in a ban/unban URL with `guest.id`, and every `make_guest(session, mazmo_user_id=N, username="...")` call with `make_guest(session, mazmo_user_id=N, mazmo_handle="...")`. Update JSON assertions that read `data["mazmo_user_id"]`/`data["username"]` from a `BannedGuestPublic`/`GuestPublic` response to also check `data["id"]`/`data["mazmo_handle"]` where the test's purpose calls for it (existing assertions on `mazmo_user_id` stay valid since that field still exists, just nullable now).

- [ ] **Step 7: Run the full guests test file**

```bash
run-tests tests/test_guests.py -v
```

Expected: PASS (all of it, including ban/unban now).

- [ ] **Step 8: Run `tests/test_organizations.py`**

```bash
run-tests tests/test_organizations.py -v
```

If any test there references a guest ban path with `mazmo_user_id`, apply the same `guest.id` swap as Step 6.

- [ ] **Step 9: Commit**

```bash
git add app/routers/organizations.py tests/test_guests.py tests/test_organizations.py
git commit -m "feat: use internal guest id instead of mazmo_user_id in ban/unban endpoints"
```

---

## Task 10: External docs (`/home/krapp/dev/vanta/docs`)

**Files:**
- Modify: `/home/krapp/dev/vanta/docs/docs/business-logic/guests.md`
- Modify: `/home/krapp/dev/vanta/docs/docs/technical/database-schema.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: docs that match the shipped schema and API.

- [ ] **Step 1: Update `guests.md`**

In the "Identidad" section, add `mazmo_handle` (renamed from `username`) and `instagram_username` as nullable fields, and state explicitly that `mazmo_user_id`/`mazmo_handle` can both be `None` for a guest without Mazmo. In "De donde vienen los guests", add a third path: "Creacion manual sin Mazmo" (`POST /guests/manual`), alongside the existing sync and `POST /guests/mazmo` (renamed from `POST /guests/`) paths. Add a new section "Vinculo con Mazmo" documenting `PATCH /guests/{id}/link-mazmo` and `PATCH /guests/{id}/unlink-mazmo`, including that `id` never changes across a link/unlink/relink cycle and that linking to an already-claimed `mazmo_user_id` returns 409 with no automatic merge.

- [ ] **Step 2: Update `database-schema.md`**

In the `erDiagram` block, update the `guests` entity:
```
    guests {
        int mazmo_user_id PK
        varchar username
        varchar displayname
    }
```
becomes:
```
    guests {
        uuid id PK
        int mazmo_user_id "nullable, unique"
        varchar mazmo_handle "nullable"
        varchar displayname
        varchar instagram_username "nullable"
    }
```

Update `meetup_rsvps.guest_id`, `organization_bans.guest_id`, `event_log.guest_id` from `int` to `uuid` in the diagram.

In the `guests` table section, replace the row describing `mazmo_user_id PK` and rewrite the prose ("La PK es el propio ID de Mazmo...") to describe the new `id` UUID PK, with `mazmo_user_id`/`mazmo_handle` as nullable unique/indexed columns. Add a row for `instagram_username`.

Update the `meetup_rsvps`, `organization_bans`, `event_log` table sections: `guest_id` type changes from `integer` to `uuid`, and the "Constraints" column's `FK -> guests.mazmo_user_id` references become `FK -> guests.id`.

Add a row to "Historial de migraciones":
```
| 0016 | Guest identity decoupled from Mazmo: guests.id (UUID) as PK, mazmo_user_id/mazmo_handle nullable, instagram_username, guest_id FKs retargeted from mazmo_user_id to id |
```

- [ ] **Step 3: Commit (in the docs repo)**

```bash
cd /home/krapp/dev/vanta/docs
git add docs/business-logic/guests.md docs/technical/database-schema.md
git commit -m "docs: guests can now exist without a Mazmo account"
cd /home/krapp/dev/vanta/alter-tracker-backend
```

---

## Task 11: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

```bash
run-tests
```

Expected: 100% pass, zero skips beyond any pre-existing ones unrelated to guests.

- [ ] **Step 2: Coverage sanity check**

```bash
coverage
```

Expected: no drop in overall coverage; new endpoints (`/guests/manual`, `link-mazmo`, `unlink-mazmo`, edit, search) show meaningful coverage from Task 7's tests.

- [ ] **Step 3: Lint and type-check the whole project**

```bash
lint
basedpyright
```

Expected: clean. Fix anything flagged (in particular: unused imports left over from removing `MazmoUserId` casts in `meetups.py`/`organizations.py`, and the removed `Annotated[int, Field(gt=0, le=2_147_483_647)]` import if `Field` becomes unused anywhere).

- [ ] **Step 4: Regenerate `openapi.json`**

The pre-commit hook already regenerates it on each commit (seen in Task 1's commit output: "generate openapi.json"), but confirm it's current:

```bash
git status
```

Expected: no uncommitted `openapi.json` diff.

- [ ] **Step 5: Manual smoke test against the dev server**

```bash
dev-backend
```

In another terminal, using `staff_headers`-equivalent auth (register/login a real dev user, or use `seed-admin`):

```bash
curl -X POST http://localhost:8000/guests/manual \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"displayname": "Smoke Test Guest"}'
```

Expected: `201` with a UUID `id`, `mazmo_user_id: null`, `mazmo_handle: null`.

```bash
curl -X PATCH "http://localhost:8000/guests/$GUEST_ID/link-mazmo" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"username": "<a-real-mazmo-username>"}'
```

Expected: `200` with `mazmo_user_id` and `mazmo_handle` populated.

- [ ] **Step 6: Final commit if anything changed during verification**

```bash
git add -A
git commit -m "chore: final lint/type-check fixes for guest identity refactor"
```

(Skip if Steps 1-5 required no changes.)
