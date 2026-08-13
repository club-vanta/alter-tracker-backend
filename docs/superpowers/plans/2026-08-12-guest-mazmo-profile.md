# Guest Mazmo Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist 6 extended Mazmo profile fields (avatar, age, gender, pronoun, and Mazmo's own suspended/banned flags) per linked guest, sourced from sync, from linking a Mazmo account, and from creating a guest by Mazmo username, and expose them via `GuestPublic.mazmo_profile` (inherited by `GuestWithBanPublic`, and explicitly wired into the meetup guest list and walk-in endpoints that build it by hand).

**Architecture:** A new `GuestMazmoProfile` table (1:1 with `Guest`, `guest_id` as its own primary key - a new pattern in this codebase) stores a flat, unversioned snapshot. `MazmoUserEntry` (`app/schemas/mazmo.py`) is extended with the 6 new fields and becomes the single parser both `fetch_users()` (batch, used by sync) and `fetch_user_by_username()` (single lookup, used by link-mazmo and create-guest-from-mazmo) validate through - replacing `fetch_user_by_username()`'s old hand-rolled parsing, which silently dropped everything except `id`/`username`/`displayname`. Four write paths keep the table current: the sync's unconditional bulk upsert (every guest in the batch, every sync, regardless of whether displayname changed), `link-mazmo`'s upsert and `create-guest-from-mazmo`'s insert (both from the same lookup response already in memory, no extra Mazmo call), and `unlink-mazmo`'s delete. `GuestPublic.mazmo_profile` exposes the data, with a `selectinload` fix on the guests router to avoid N+1. `app/routers/meetups.py`'s `list_meetup_guests` and `add_walkin_guest` build `GuestWithBanPublic` via explicit keyword arguments rather than `.model_validate()`, so they separately pass `mazmo_profile=...` through, with the equivalent nested `selectinload` fix applied to `list_meetup_guests`'s query.

**Tech Stack:** FastAPI, SQLModel (Pydantic v2 + SQLAlchemy 2.0), PostgreSQL (`INSERT ... ON CONFLICT ... DO UPDATE`), Alembic, pytest (real Postgres test DB, no mocked sessions), httpx (mocked in tests).

## Prerequisite

**This plan assumes `docs/superpowers/plans/2026-08-12-guest-displayname-history.md` has already been fully implemented** (all of its tasks committed - `GuestDisplaynameHistory` table, `GuestDisplaynameSource` enum, the rewritten atomic `_upsert_guests()` in `app/services/sync.py`, the `staff` rename in `update_guest`, and the `GuestDisplaynameHistory`/`GUEST_DISPLAYNAME_CHANGED` writes in `link_guest_to_mazmo`). Task 1, Step 1 below verifies this before making any changes. Every code block in this plan that shows "before" state for a file already touched by that other plan reflects **that plan's end state**, not today's un-modified file - this was confirmed by reading that plan's Task 3 (sync), Task 4 (`update_guest`), and Task 5 (`link_guest_to_mazmo`) in full.

## Global Constraints

- ASCII-only in all code, comments, and docstrings you write - no em-dashes, no Unicode arrows, no smart quotes. Use `-` and `->`. (CLAUDE.md rule 9.)
- Never `DELETE` real `Guest`/`User` data. `GuestMazmoProfile` rows ARE deleted by `unlink-mazmo` - that is the one intentional exception in this plan, explicitly required by the approved spec ("se borra la fila de GuestMazmoProfile de ese guest").
- `GuestMazmoProfile` is a plain snapshot with **no audit trail** - unlike `GuestDisplaynameHistory`, this plan writes no `EventLog` entries and no history table for these 6 fields. This is a deliberate, spec-stated decision ("Sin historico/versionado de estos 6 campos - snapshot plano, decision explicita"), not an oversight.
- Services (`app/services/`) never import `fastapi` or raise `HTTPException`. This plan's only service-layer change (`app/services/sync.py`) must keep that invariant.
- `basedpyright` must pass in strict mode; use `# type: ignore[specific-code]` only where the existing code already does (e.g. raw `pg_insert` statements passed to `session.exec`).
- Run `uv run ruff format` and `uv run ruff check` before each commit.
- All new tests use the real Postgres test database via the `session`/`client` fixtures in `tests/conftest.py`. Never mock the DB.

---

## Design decisions this plan makes (scope calls and spec-gap resolutions)

The approved spec at `docs/superpowers/specs/2026-08-12-guest-mazmo-profile-design.md` leaves a few things underspecified, or illustrates code in a way that doesn't match this repo's actual established conventions. Each is resolved here, deliberately, so no task below contains an unresolved ambiguity:

1. **`MazmoUserWithId` stays flat (no nested `profile: MazmoUserEntry` field).** The spec says `fetch_user_by_username()` should validate via `MazmoUserEntry.model_validate()` and "extraer el id numerico aparte," but doesn't dictate the exact return shape. This plan extends the existing `MazmoUserWithId` `NamedTuple` with 6 new flat fields (`avatar`, `age`, `gender`, `pronoun`, `suspended`, `banned`) copied from the validated `MazmoUserEntry`, rather than nesting the whole entry under a `profile` attribute. This keeps every existing call site (`mazmo_user.username`, `mazmo_user.displayname` in `create_guest_from_mazmo` and `link_guest_to_mazmo`) working unchanged, and keeps the existing `SimpleNamespace`-based test mocks (`tests/conftest.py`'s `mock_mazmo_for_guests`, and two inline overrides in `tests/test_guest_displayname_history.py`) valid with only additive changes (new attributes), not a structural rewrite. `MazmoUserEntry.model_validate()` remains the single place that defines which fields are read from Mazmo, satisfying the spec's actual stated goal.

2. **`POST /guests/mazmo` (`create_guest_from_mazmo`) DOES populate `GuestMazmoProfile` at creation time.** The approved spec's `POST /guests/mazmo` section states this explicitly: this endpoint also calls `fetch_user_by_username()` and has the full profile in memory, so it creates the `GuestMazmoProfile` row in the same commit as `Guest` and `EventLog(GUEST_CREATED, ...)` - same pattern as `link-mazmo`, no extra Mazmo call. Handled in Task 5 alongside `link-mazmo`, since both endpoints share the same `MazmoUserWithId` -> `GuestMazmoProfile` construction logic.

3. **The N+1 `selectinload` fix is scoped to `app/routers/guests.py`; `app/routers/meetups.py`'s meetup guest list endpoint needs no fix.** The spec says "todo router que devuelva `GuestPublic` (list y detail)" but then gives exactly one concrete, "puntualmente" actionable instance (`GET /guests/`) and exactly one N+1 test, both scoped to `guests.py`. `GuestWithBanPublic` (used by `app/routers/meetups.py`'s `GET /organizations/{org_id}/meetups/{meetup_id}/guests` guest list, in `list_meetup_guests`) does inherit the new `mazmo_profile` field via its `GuestPublic` base class, but `list_meetup_guests` builds `GuestWithBanPublic(id=..., mazmo_user_id=..., mazmo_handle=..., displayname=..., instagram_username=..., is_banned=...)` via explicit keyword arguments (not `GuestWithBanPublic.model_validate(rsvp.guest)`), and never passes `mazmo_profile`. Since the field has a `= None` default, the ORM attribute `rsvp.guest.mazmo_profile` is never read, so no lazy-load and no N+1 query is ever triggered there - the response's `mazmo_profile` is simply always `null` for this endpoint today, a data-completeness gap (out of scope here), not a performance one. `add_walkin_guest`'s single-guest response builds `GuestWithBanPublic` the same explicit-keyword way, so the same reasoning applies. No `selectinload` fix is needed in `meetups.py`.

   **Update:** the approved spec has since been extended with a new "Exposicion en `GuestWithBanPublic`" section that explicitly closes this exact data-completeness gap (it is the view staff use at the door, the original reason for this whole feature). Task 8 below adds the `mazmo_profile=...` keyword to both `GuestWithBanPublic` constructions in `meetups.py` and, because that makes `rsvp.guest.mazmo_profile` an attribute that actually gets read for the first time, extends `list_meetup_guests`'s existing `.options(selectinload(MeetupRsvp.guest))` to `.options(selectinload(MeetupRsvp.guest).selectinload(Guest.mazmo_profile))` to avoid the N+1 this reasoning predicted would appear the moment the attribute was read. The analysis above (why this was a data-completeness gap, not a performance one, *prior to* that task) still stands as the reasoning for why Tasks 1-7 left `meetups.py` untouched.

4. **`GuestMazmoProfilePublic` and the `mazmo_profile` field on `GuestPublic` use plain Pydantic `BaseModel`, not `SQLModel`.** The spec's illustrative code sample writes `class GuestMazmoProfilePublic(SQLModel): model_config = ConfigDict(from_attributes=True)`, matching CLAUDE.md's generic schema example. However, `app/schemas/guests.py`'s actual, established convention (read in full before writing this plan) uses plain `BaseModel` with `ConfigDict(from_attributes=True)` throughout - `GuestPublic` itself is `BaseModel`, not `SQLModel`. This plan follows the real file's convention over the spec's illustrative snippet.

5. **Test mock updates required in files this plan does not otherwise touch, to avoid `AttributeError`s once the prerequisite plan's code reads the new fields off them.** Once `link_guest_to_mazmo` (Task 5) reads `mazmo_user.avatar`/`.age`/etc., and `GuestSyncer._upsert_mazmo_profiles` (Task 4) reads `user.avatar`/`.age`/etc. off `user_details` values, every test mock that stands in for a Mazmo user response needs those attributes present (mocks are `SimpleNamespace`, which raises `AttributeError` on missing attributes, unlike a real `MazmoUserEntry` with defaults). This plan updates: `tests/conftest.py`'s module-level `FAKE_USERS` dict and `mock_mazmo_for_guests` fixture (Task 3), and two identical inline `SimpleNamespace(...)` overrides inside `tests/test_guest_displayname_history.py` (also Task 3, fixed via one `replace_all` edit since both occurrences are byte-identical). If the prerequisite plan's own file differs even slightly from what is quoted here, search for `SimpleNamespace(\n        mazmo_user_id=39119, username="cindydark", displayname="Matching Name"\n    )` (or the collapsed one-line form) in that file rather than trusting the exact line numbers below.

---

## Task 1: Data model - `GuestMazmoProfile` table + `Guest.mazmo_profile` relationship

**Files:**
- Modify: `app/models/models.py` (`Guest` class's relationship list; new section after the `GuestDisplaynameHistory` section)
- Test: `tests/test_guest_mazmo_profile.py` (new file)

**Interfaces:**
- Produces: `GuestMazmoProfile` (table, fields `guest_id: uuid.UUID` (PK), `avatar_url: str | None`, `age: int | None`, `gender: str | None`, `pronoun: str | None`, `mazmo_suspended: bool`, `mazmo_banned: bool`, `synced_at: datetime`), `Guest.mazmo_profile` relationship.
- Consumes: `GuestDisplaynameHistory` (from the prerequisite plan) only as a text anchor to locate the insertion point - no functional dependency.

- [ ] **Step 1: Verify the prerequisite plan has been implemented**

Run:

```bash
grep -n "class GuestDisplaynameHistory" app/models/models.py
grep -n "displayname_history: list" app/models/models.py
grep -n "def _upsert_mazmo_profiles\|def _upsert_guests" app/services/sync.py
grep -n "staff: User = Depends(get_approved_user)" app/routers/guests.py
```

Expected: the first two greps each find one match in `app/models/models.py`; the third finds `_upsert_guests` (not yet `_upsert_mazmo_profiles` - that's Task 4 of THIS plan); the fourth finds at least 2 matches (`update_guest` and `link_guest_to_mazmo` both take `staff`, not `_staff`, after the prerequisite plan's rename). If any of these come back empty, STOP - the prerequisite plan has not been fully implemented yet. Implement it first via `docs/superpowers/plans/2026-08-12-guest-displayname-history.md`, then resume here.

- [ ] **Step 2: Add the `mazmo_profile` relationship to `Guest`**

In `app/models/models.py`, find the `Guest` class's relationship list, which (after the prerequisite plan) reads:

```python
    org_bans: list["OrganizationBan"] = Relationship(back_populates="guest")
    displayname_history: list["GuestDisplaynameHistory"] = Relationship(back_populates="guest")
```

Change it to:

```python
    org_bans: list["OrganizationBan"] = Relationship(back_populates="guest")
    displayname_history: list["GuestDisplaynameHistory"] = Relationship(back_populates="guest")
    mazmo_profile: "GuestMazmoProfile | None" = Relationship(back_populates="guest")
```

- [ ] **Step 3: Add the `GuestMazmoProfile` table**

Still in `app/models/models.py`, find the tail of the `GuestDisplaynameHistory` section (added by the prerequisite plan), which reads:

```python
    guest: Guest = Relationship(back_populates="displayname_history")
    actor: Optional["User"] = Relationship()


# ── Event Log ─────────────────────────────────────────────────────────────────
```

Change it to:

```python
    guest: Guest = Relationship(back_populates="displayname_history")
    actor: Optional["User"] = Relationship()


# ── Guest Mazmo Profile ──────────────────────────────────────────────────────


class GuestMazmoProfile(SQLModel, table=True):
    """
    Snapshot of extended Mazmo profile data for a linked guest.

    1:1 with Guest - guest_id IS the primary key directly, not a
    surrogate id. This is a new pattern in this codebase: the other
    2-entity association tables (UserOrganization, MeetupRsvp) use a
    composite PK of 2 FKs instead, but this is a genuine 1:1
    relationship where guest_id already identifies the row without
    ambiguity.

    No history/versioning - unlike GuestDisplaynameHistory, this is a
    plain snapshot that gets overwritten on every sync or link-mazmo.

    mazmo_suspended/mazmo_banned are prefixed even within this
    already-Mazmo-specific table: once this is flattened into a JSON API
    response, the field name travels without the table's context -
    {"banned": false} on its own could be confused with this app's own
    OrganizationBan, which has nothing to do with Mazmo's account state.

    gender/pronoun are free-text (str | None), not one of this
    codebase's own StrEnum values - Mazmo controls that vocabulary and
    can add values without this code needing to change, the same
    reasoning already applied to EventType/OrgRole not being native
    Postgres ENUMs.

    avatar_url stores only the "default" size/format of the avatar
    object Mazmo returns (which has 4 sizes x 2 formats) - sufficient
    for a single admin-page image, no responsive-image use case exists
    yet.

    synced_at records when this snapshot was last refreshed - this data
    can drift from Mazmo's live state between syncs.
    """

    __tablename__ = "guest_mazmo_profile"  # type: ignore[assignment]

    guest_id: uuid.UUID = Field(foreign_key="guests.id", primary_key=True)
    avatar_url: str | None = Field(default=None)
    age: int | None = Field(default=None)
    gender: str | None = Field(default=None, max_length=32)
    pronoun: str | None = Field(default=None, max_length=32)
    mazmo_suspended: bool = Field(default=False)
    mazmo_banned: bool = Field(default=False)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    guest: Guest = Relationship(back_populates="mazmo_profile")


# ── Event Log ─────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Create the test file with a model round-trip sanity check**

Create `tests/test_guest_mazmo_profile.py`:

```python
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
```

- [ ] **Step 5: Run the new test**

Run: `uv run pytest tests/test_guest_mazmo_profile.py -v`
Expected: PASS (`SQLModel.metadata.create_all()` in `setup_test_database` picks up the new table automatically since it iterates all registered `SQLModel` subclasses).

- [ ] **Step 6: Run the full test suite to confirm nothing broke**

Run: `uv run pytest tests/ -v`
Expected: all previously-passing tests still PASS.

- [ ] **Step 7: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add app/models/models.py tests/test_guest_mazmo_profile.py
git commit -m "feat: add GuestMazmoProfile table and Guest.mazmo_profile relationship"
```

---

## Task 2: Alembic migration - create `guest_mazmo_profile` table

**Files:**
- Create: `alembic/versions/0019_guest_mazmo_profile.py`

**Interfaces:**
- Consumes: nothing from other tasks (self-contained DDL).
- Produces: the `guest_mazmo_profile` table in any *real* database this migration is applied to. The test database used by `pytest` does not get its schema from this migration (see Task 1's note about `SQLModel.metadata.create_all()`).

- [ ] **Step 1: Confirm the current migration head**

Run: `ls alembic/versions/ | sort | tail -3`
Expected: the highest-numbered file is `0018_guest_displayname_history.py` (from the prerequisite plan - renumbered to 0018 after `docs/superpowers/plans/2026-08-12-guest-type-payment-exemption.md`'s migration took 0017). If it is not, STOP and re-check the Prerequisite section above.

- [ ] **Step 2: Create the migration file**

Create `alembic/versions/0019_guest_mazmo_profile.py`:

```python
"""guest mazmo profile table

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-12

Adds guest_mazmo_profile: a 1:1 snapshot of extended Mazmo profile data
(avatar, age, gender, pronoun, suspended, banned) for a linked guest.
guest_id is the primary key directly (not a surrogate id) since this is
a genuine 1:1 relationship - see the GuestMazmoProfile model docstring
in app/models/models.py for why this differs from this codebase's other
association tables.

No backfill: this data can only be obtained via a live call to Mazmo,
there is nothing to derive it from in existing data. The table starts
empty and fills in as each linked guest appears in a future sync (or is
re-linked via link-mazmo).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0019"
down_revision: str = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_mazmo_profile",
        sa.Column("guest_id", UUID(as_uuid=True), nullable=False),
        sa.Column("avatar_url", sa.String(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(length=32), nullable=True),
        sa.Column("pronoun", sa.String(length=32), nullable=True),
        sa.Column("mazmo_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mazmo_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guest_id"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("guest_mazmo_profile")
```

- [ ] **Step 3: Apply the migration to the dev database and verify the round trip**

Run: `uv run alembic upgrade head`
Expected: succeeds, no errors. `guest_mazmo_profile` now exists in the dev DB (`alter_event_tracker`), empty.

Run: `uv run alembic downgrade -1`
Expected: succeeds, drops the table cleanly.

Run: `uv run alembic upgrade head` again
Expected: succeeds again.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0019_guest_mazmo_profile.py
git commit -m "feat: add guest_mazmo_profile migration"
```

---

## Task 3: Unify Mazmo user parsing - `MazmoUserEntry` gets 6 new fields

**This is the highest-risk task in this plan** - it touches the shared schema both sync and link-mazmo depend on, and requires updating test mocks in files this plan doesn't otherwise own, to avoid breaking them once later tasks read the new fields (see Design Decision 5 above).

**Files:**
- Modify: `app/schemas/mazmo.py` (add `MazmoAvatarEntry`, extend `MazmoUserEntry`)
- Modify: `app/schemas/__init__.py` (export `MazmoAvatarEntry`)
- Modify: `app/services/mazmo.py` (extend `MazmoUserWithId`, rewrite `fetch_user_by_username`)
- Modify: `tests/conftest.py` (`FAKE_USERS`, `mock_mazmo_for_guests` fixture)
- Modify: `tests/test_guest_displayname_history.py` (2 `SimpleNamespace` overrides)
- Test: `tests/test_mazmo.py` (append)

**Interfaces:**
- Produces: `MazmoAvatarEntry` (schema, field `default: str`), `MazmoUserEntry` extended with `avatar: MazmoAvatarEntry | None`, `age: int | None`, `gender: str | None`, `pronoun: str | None`, `suspended: bool`, `banned: bool` (all with safe defaults). `MazmoUserWithId` extended with the same 6 fields. `fetch_user_by_username(username: str) -> MazmoUserWithId` now returns all 9 fields (was 3).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Add `MazmoAvatarEntry` and extend `MazmoUserEntry` in `app/schemas/mazmo.py`**

Change:

```python
class MazmoUserEntry(BaseModel):
    """
    One entry in the /users response dict returned by Mazmo.

    Shape from Mazmo:
    {
        "195749": { "username": "alice", "displayname": "Alice W." },
        "153151": { "username": "bob", "displayname": "Bob" },
        ...
    }

    Note: The keys are string representations of user IDs.
    """

    model_config = ConfigDict(strict=False)

    username: str
    displayname: str
```

to:

```python
class MazmoAvatarEntry(BaseModel):
    """
    The `avatar` object Mazmo returns for a user profile.

    Mazmo's avatar object actually has 4 sizes x 2 formats - only
    `default` is modeled here, the only size/format this app has a use
    case for (a single image on an admin page, not responsive images).
    Extra keys in the real payload (other sizes/formats) are ignored,
    not validation errors - BaseModel allows extra fields by default.
    """

    model_config = ConfigDict(strict=False)

    default: str


class MazmoUserEntry(BaseModel):
    """
    One entry in the /users response dict returned by Mazmo, and also
    the shape of the single-user /users/{username} response body (used
    by MazmoClient.fetch_user_by_username - see MazmoUserWithId in
    app/services/mazmo.py). This is the single place that defines which
    fields are read from Mazmo user data for both endpoints.

    Shape from Mazmo (batch /users endpoint):
    {
        "195749": {
            "username": "alice", "displayname": "Alice W.",
            "avatar": {"default": "https://..."}, "age": 29,
            "gender": "female", "pronoun": "she/her",
            "suspended": false, "banned": false
        },
        ...
    }

    Note: The keys are string representations of user IDs.

    avatar/age/gender/pronoun/suspended/banned are all optional (or
    default to a safe value) since Mazmo may not have them set for a
    given user. suspended/banned are Mazmo's own account-level flags,
    unrelated to this app's own ban system - see GuestMazmoProfile in
    app/models/models.py.
    """

    model_config = ConfigDict(strict=False)

    username: str
    displayname: str
    avatar: MazmoAvatarEntry | None = None
    age: int | None = None
    gender: str | None = None
    pronoun: str | None = None
    suspended: bool = False
    banned: bool = False
```

- [ ] **Step 2: Export `MazmoAvatarEntry` from `app/schemas/__init__.py`**

Change:

```python
from app.schemas.mazmo import MazmoRsvpEntry, MazmoUserEntry
```

to:

```python
from app.schemas.mazmo import MazmoAvatarEntry, MazmoRsvpEntry, MazmoUserEntry
```

And in the `__all__` list, change:

```python
    "LinkMazmoRequest",
    "MazmoRsvpEntry",
    "MazmoUserEntry",
```

to:

```python
    "LinkMazmoRequest",
    "MazmoAvatarEntry",
    "MazmoRsvpEntry",
    "MazmoUserEntry",
```

- [ ] **Step 3: Extend `MazmoUserWithId` and rewrite `fetch_user_by_username` in `app/services/mazmo.py`**

Change the import line:

```python
from app.schemas import MazmoRsvpEntry, MazmoUserEntry
```

to:

```python
from app.schemas import MazmoAvatarEntry, MazmoRsvpEntry, MazmoUserEntry
```

Change:

```python
class MazmoUserWithId(NamedTuple):
    """
    Combined result from the single-user lookup endpoint.

    NamedTuple is a tuple subclass with named fields — fields are accessible
    by name (user.username) or by index (user[1]). Immutable: you cannot
    reassign fields after construction. Used here as a lightweight internal
    struct; no Pydantic validation needed since the data comes from the API
    already parsed.
    """

    mazmo_user_id: MazmoUserId
    username: str
    displayname: str
```

to:

```python
class MazmoUserWithId(NamedTuple):
    """
    Combined result from the single-user lookup endpoint.

    NamedTuple is a tuple subclass with named fields - fields are accessible
    by name (user.username) or by index (user[1]). Immutable: you cannot
    reassign fields after construction. Used here as a lightweight internal
    struct; no Pydantic validation needed since the data comes from the API
    already parsed.

    mazmo_user_id is the one field the single-user response body has
    that MazmoUserEntry doesn't model - the batch /users endpoint
    returns it as the dict key instead of a body field, so it stays out
    of MazmoUserEntry to keep that schema identical between both
    endpoints. Every other field here (username, displayname, avatar,
    age, gender, pronoun, suspended, banned) is exactly what
    MazmoUserEntry.model_validate() extracted - MazmoUserEntry is the
    single place that defines what fields are read from Mazmo user
    data, so a field added there is never silently dropped by one of
    the two parsing paths and not the other.
    """

    mazmo_user_id: MazmoUserId
    username: str
    displayname: str
    avatar: MazmoAvatarEntry | None
    age: int | None
    gender: str | None
    pronoun: str | None
    suspended: bool
    banned: bool
```

Change:

```python
    async def fetch_user_by_username(self, username: str) -> MazmoUserWithId:
        """
        Looks up a Mazmo user by their username handle.

        Args:
            username: Mazmo username, e.g. "cindydark"

        Returns:
            MazmoUserWithId with mazmo_user_id, username, and displayname.

        Raises:
            MazmoNetworkError: If Mazmo API is unreachable.
            MazmoAPIError: If Mazmo API returns an error status (including 404).
        """
        url = f"{self._settings.mazmo_base_url}/users/{username}"
        try:
            resp = await self._client.get(url)
            self._raise_for_status(resp, context=f"fetch user by username '{username}'")
        except httpx.HTTPStatusError as exc:
            raise MazmoAPIError(f"Mazmo returned {exc.response.status_code} for username '{username}'") from exc
        except httpx.RequestError as exc:
            raise MazmoNetworkError(f"Cannot reach Mazmo: {exc}") from exc

        data = resp.json()
        return MazmoUserWithId(
            mazmo_user_id=MazmoUserId(int(data["id"])),
            username=data["username"],
            displayname=data["displayname"],
        )
```

to:

```python
    async def fetch_user_by_username(self, username: str) -> MazmoUserWithId:
        """
        Looks up a Mazmo user by their username handle.

        Args:
            username: Mazmo username, e.g. "cindydark"

        Returns:
            MazmoUserWithId with mazmo_user_id and the full profile
            (username, displayname, avatar, age, gender, pronoun,
            suspended, banned) - parsed via MazmoUserEntry.model_validate(),
            the same schema fetch_users() uses for the batch endpoint.

        Raises:
            MazmoNetworkError: If Mazmo API is unreachable.
            MazmoAPIError: If Mazmo API returns an error status (including 404).
        """
        url = f"{self._settings.mazmo_base_url}/users/{username}"
        try:
            resp = await self._client.get(url)
            self._raise_for_status(resp, context=f"fetch user by username '{username}'")
        except httpx.HTTPStatusError as exc:
            raise MazmoAPIError(f"Mazmo returned {exc.response.status_code} for username '{username}'") from exc
        except httpx.RequestError as exc:
            raise MazmoNetworkError(f"Cannot reach Mazmo: {exc}") from exc

        data = resp.json()
        profile = MazmoUserEntry.model_validate(data)
        return MazmoUserWithId(
            mazmo_user_id=MazmoUserId(int(data["id"])),
            username=profile.username,
            displayname=profile.displayname,
            avatar=profile.avatar,
            age=profile.age,
            gender=profile.gender,
            pronoun=profile.pronoun,
            suspended=profile.suspended,
            banned=profile.banned,
        )
```

- [ ] **Step 4: Update `tests/conftest.py`'s `FAKE_USERS` so existing sync tests don't break in later tasks**

`FAKE_USERS` currently reads:

```python
FAKE_USERS = {
    111: SimpleNamespace(username="alice", displayname="Alice"),
    222: SimpleNamespace(username="bob", displayname="Bob"),
}
```

Change it to:

```python
FAKE_USERS = {
    111: SimpleNamespace(
        username="alice",
        displayname="Alice",
        avatar=None,
        age=None,
        gender=None,
        pronoun=None,
        suspended=False,
        banned=False,
    ),
    222: SimpleNamespace(
        username="bob",
        displayname="Bob",
        avatar=None,
        age=None,
        gender=None,
        pronoun=None,
        suspended=False,
        banned=False,
    ),
}
```

This is not exercised by any code yet (Task 4 is what will read these new attributes), but fixing it now keeps all mock data consistent in one place.

- [ ] **Step 5: Update `tests/conftest.py`'s `mock_mazmo_for_guests` fixture default**

Change:

```python
        # Default happy-path: cindydark found on Mazmo
        mock_instance.fetch_user_by_username.return_value = SimpleNamespace(
            mazmo_user_id=39119,
            username="cindydark",
            displayname="⚜️Lissandra⚜️",
        )
```

to:

```python
        # Default happy-path: cindydark found on Mazmo, full profile
        mock_instance.fetch_user_by_username.return_value = SimpleNamespace(
            mazmo_user_id=39119,
            username="cindydark",
            displayname="⚜️Lissandra⚜️",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/39119/default.jpg"),
            age=29,
            gender="female",
            pronoun="she/her",
            suspended=False,
            banned=False,
        )
```

- [ ] **Step 6: Fix the 2 incomplete `SimpleNamespace` overrides in `tests/test_guest_displayname_history.py`**

That file (created by the prerequisite plan) has two tests that each override `mock_mazmo_for_guests.fetch_user_by_username.return_value` with an identical, byte-for-byte copy of this snippet:

```python
    mock_mazmo_for_guests.fetch_user_by_username.return_value = SimpleNamespace(
        mazmo_user_id=39119, username="cindydark", displayname="Matching Name"
    )
```

(One inside `test_link_mazmo_with_same_displayname_creates_no_history_row`, the other inside `test_link_mazmo_with_unchanged_displayname_creates_only_mazmo_linked_eventlog`.) Since both are identical, use a single `replace_all` edit to change both occurrences to:

```python
    mock_mazmo_for_guests.fetch_user_by_username.return_value = SimpleNamespace(
        mazmo_user_id=39119,
        username="cindydark",
        displayname="Matching Name",
        avatar=None,
        age=None,
        gender=None,
        pronoun=None,
        suspended=False,
        banned=False,
    )
```

If a grep for the exact snippet above doesn't find it (the prerequisite plan's file differs from what was quoted when this plan was written), search `tests/test_guest_displayname_history.py` for `fetch_user_by_username.return_value = SimpleNamespace` instead, and apply the same fix (add the 6 missing attributes) to every match missing them.

- [ ] **Step 7: Run the full test suite to confirm nothing broke yet**

Run: `uv run pytest tests/ -v`
Expected: all tests still PASS (Steps 4-6 are purely additive - nothing yet reads the new attributes, since Tasks 4 and 5 haven't landed).

- [ ] **Step 8: Add the new unit tests to `tests/test_mazmo.py`**

Add `MazmoUserEntry` to the imports - change:

```python
from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.services.mazmo import (
    MazmoAPIError,
    MazmoClient,
    MazmoNetworkError,
    _batched,
)
```

to:

```python
from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.schemas import MazmoUserEntry
from app.services.mazmo import (
    MazmoAPIError,
    MazmoClient,
    MazmoNetworkError,
    _batched,
)
```

Then append to the end of `tests/test_mazmo.py`:

```python
# -- MazmoUserEntry profile fields -----------------------------------------------


def test_mazmo_user_entry_parses_new_profile_fields():
    """
    Verify MazmoUserEntry.model_validate() extracts avatar.default, age,
    gender, pronoun, suspended, and banned from a full Mazmo user payload.

    WHY: These 6 fields feed GuestMazmoProfile - this is the first
    parsing step in the whole feature's data path.
    """
    payload = {
        "username": "cindydark",
        "displayname": "Lissandra",
        "avatar": {
            "default": "https://cdn.mazmo.net/avatars/39119/default.jpg",
            "small": "https://cdn.mazmo.net/avatars/39119/small.jpg",
        },
        "age": 29,
        "gender": "female",
        "pronoun": "she/her",
        "suspended": False,
        "banned": False,
    }

    entry = MazmoUserEntry.model_validate(payload)

    assert entry.username == "cindydark"
    assert entry.displayname == "Lissandra"
    assert entry.avatar is not None
    assert entry.avatar.default == "https://cdn.mazmo.net/avatars/39119/default.jpg"
    assert entry.age == 29
    assert entry.gender == "female"
    assert entry.pronoun == "she/her"
    assert entry.suspended is False
    assert entry.banned is False


def test_mazmo_user_entry_tolerates_missing_optional_profile_fields():
    """
    Verify a payload missing age/gender/pronoun/avatar still validates,
    with those fields defaulting to None (and suspended/banned to False).
    """
    payload = {"username": "plainuser", "displayname": "Plain User"}

    entry = MazmoUserEntry.model_validate(payload)

    assert entry.avatar is None
    assert entry.age is None
    assert entry.gender is None
    assert entry.pronoun is None
    assert entry.suspended is False
    assert entry.banned is False


# -- fetch_user_by_username unified parsing --------------------------------------


@pytest.mark.asyncio
async def test_fetch_user_by_username_returns_full_profile_data():
    """
    Verify MazmoClient.fetch_user_by_username() returns the numeric id
    and all 6 new profile fields, not just username/displayname.

    WHY: Before the fetch_user_by_username/fetch_users parsing
    unification, this method hand-parsed only id/username/displayname
    and silently dropped everything else - link-mazmo would never have
    had access to these fields no matter what was added to
    MazmoUserEntry.
    """
    settings = get_settings()

    mock_response = {
        "id": 39119,
        "username": "cindydark",
        "displayname": "Lissandra",
        "avatar": {"default": "https://cdn.mazmo.net/avatars/39119/default.jpg"},
        "age": 29,
        "gender": "female",
        "pronoun": "she/her",
        "suspended": False,
        "banned": False,
    }

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.is_error = False
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            result = await client.fetch_user_by_username("cindydark")

    assert result.mazmo_user_id == MazmoUserId(39119)
    assert result.username == "cindydark"
    assert result.displayname == "Lissandra"
    assert result.avatar is not None
    assert result.avatar.default == "https://cdn.mazmo.net/avatars/39119/default.jpg"
    assert result.age == 29
    assert result.gender == "female"
    assert result.pronoun == "she/her"
    assert result.suspended is False
    assert result.banned is False
```

- [ ] **Step 9: Run the new tests**

Run: `uv run pytest tests/test_mazmo.py -v`
Expected: all PASS, including the 3 new tests.

- [ ] **Step 10: Run the full test suite again**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 11: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 12: Commit**

```bash
git add app/schemas/mazmo.py app/schemas/__init__.py app/services/mazmo.py tests/conftest.py tests/test_guest_displayname_history.py tests/test_mazmo.py
git commit -m "feat: unify Mazmo user parsing, extend MazmoUserEntry with profile fields"
```

---

## Task 4: Sync unconditionally upserts `GuestMazmoProfile` for the full batch

**Files:**
- Modify: `app/services/sync.py` (module docstring, imports, `sync()`, new `_upsert_mazmo_profiles` method)
- Test: `tests/test_sync.py` (append)

**Interfaces:**
- Consumes: `GuestMazmoProfile` (Task 1), `MazmoUserEntry` with profile fields (Task 3), `_fetch_guest_id_map(mazmo_user_ids: list[MazmoUserId]) -> dict[MazmoUserId, uuid.UUID]` (unchanged, from the prerequisite plan - covers every guest in the sync batch, not just changed ones).
- Produces: `GuestSyncer._upsert_mazmo_profiles(guest_id_map: dict[MazmoUserId, uuid.UUID], user_details: dict[MazmoUserId, MazmoUserEntry]) -> None`.

- [ ] **Step 1: Update the module docstring in `app/services/sync.py`**

Change:

```python
MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).
"""
```

to:

```python
MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).

GuestMazmoProfile table:
  INSERT ... ON CONFLICT (guest_id) DO UPDATE - unconditional, unlike the
  Guest table's displayname upsert: every guest in the sync batch gets
  this table refreshed (avatar_url, age, gender, pronoun,
  mazmo_suspended, mazmo_banned, synced_at) on every sync, whether or
  not their displayname changed - these fields can change independently
  of displayname. Runs after _fetch_guest_id_map(), since it needs the
  resolved internal guest_id for every guest in the batch, not just the
  ones _upsert_guests()'s RETURNING clause reported as changed/inserted.
"""
```

- [ ] **Step 2: Add `GuestMazmoProfile` to the models import**

Change:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    Meetup,
    MeetupRsvp,
)
```

to:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
)
```

- [ ] **Step 3: Call `_upsert_mazmo_profiles` from `sync()`**

Change:

```python
        # Guests must be upserted (and their internal ids resolved) before
        # RSVPs can be built, since MeetupRsvp.guest_id is now the internal
        # UUID, not the raw mazmo_user_id.
        self._upsert_guests(guests_to_insert)
        guest_id_map = self._fetch_guest_id_map(list(rsvps.keys()))
        rsvps_to_upsert = self._build_rsvps(rsvps, user_details, guest_id_map)
```

to:

```python
        # Guests must be upserted (and their internal ids resolved) before
        # RSVPs can be built, since MeetupRsvp.guest_id is now the internal
        # UUID, not the raw mazmo_user_id.
        self._upsert_guests(guests_to_insert)
        guest_id_map = self._fetch_guest_id_map(list(rsvps.keys()))
        self._upsert_mazmo_profiles(guest_id_map, user_details)
        rsvps_to_upsert = self._build_rsvps(rsvps, user_details, guest_id_map)
```

- [ ] **Step 4: Add the `_upsert_mazmo_profiles` method**

Find the tail of `_upsert_guests` (as rewritten by the prerequisite plan), immediately followed by `_upsert_rsvps`:

```python
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

    def _upsert_rsvps(self, rsvps: list[MeetupRsvp]) -> int:
```

Insert a new method between them, so the block becomes:

```python
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

    def _upsert_mazmo_profiles(
        self,
        guest_id_map: dict[MazmoUserId, uuid.UUID],
        user_details: dict[MazmoUserId, MazmoUserEntry],
    ) -> None:
        """
        Unconditionally upsert GuestMazmoProfile for every guest in the
        sync batch - unlike _upsert_guests, there is no "only if
        changed" gate: mazmo_suspended/age/gender/pronoun/avatar_url can
        all change independently of displayname, so a guest whose
        displayname didn't change (and therefore never appears in
        _upsert_guests's RETURNING set) must still get this table
        refreshed.

        Takes guest_id_map (built by _fetch_guest_id_map, covers every
        guest in the batch - not just the ones _upsert_guests reported
        as changed/inserted) rather than deriving guest_id from
        _upsert_guests's return value. This is exactly why this must
        run after _fetch_guest_id_map(), not right after
        _upsert_guests() - see the caller in sync().
        """
        now = datetime.now(UTC)
        profiles: list[GuestMazmoProfile] = []
        for mazmo_user_id, guest_id in guest_id_map.items():
            user = user_details.get(mazmo_user_id)
            if user is None:
                continue
            profiles.append(
                GuestMazmoProfile(
                    guest_id=guest_id,
                    avatar_url=user.avatar.default if user.avatar else None,
                    age=user.age,
                    gender=user.gender,
                    pronoun=user.pronoun,
                    mazmo_suspended=user.suspended,
                    mazmo_banned=user.banned,
                    synced_at=now,
                )
            )

        if not profiles:
            return

        rows = [p.model_dump(exclude={"guest"}) for p in profiles]
        stmt = pg_insert(GuestMazmoProfile).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["guest_id"],
            set_={
                "avatar_url": stmt.excluded.avatar_url,
                "age": stmt.excluded.age,
                "gender": stmt.excluded.gender,
                "pronoun": stmt.excluded.pronoun,
                "mazmo_suspended": stmt.excluded.mazmo_suspended,
                "mazmo_banned": stmt.excluded.mazmo_banned,
                "synced_at": stmt.excluded.synced_at,
            },
        )
        self._session.exec(stmt)  # type: ignore[arg-type]
        self._session.commit()

    def _upsert_rsvps(self, rsvps: list[MeetupRsvp]) -> int:
```

- [ ] **Step 5: Run the existing sync test suite to confirm nothing broke**

Run: `uv run pytest tests/test_sync.py tests/test_sync_service.py -v`
Expected: all existing tests still PASS (Task 3, Step 4 already gave `FAKE_USERS` the attributes this new code reads).

- [ ] **Step 6: Add the new integration tests to `tests/test_sync.py`**

Add imports - change:

```python
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

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
from tests.conftest import make_guest, make_org_member, make_rsvp
```

to:

```python
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
    Organization,
    OrgRole,
)
from tests.conftest import make_guest, make_org_member, make_rsvp
```

(If `tests/test_sync.py`'s current import block differs from what's shown above because the prerequisite plan's exact edit landed slightly differently, just add `GuestMazmoProfile` to whatever multi-line `app.models.models` import already exists there, and add `from types import SimpleNamespace` alongside the other stdlib imports.)

Then append to the end of `tests/test_sync.py`:

```python
# -- GuestMazmoProfile via sync --------------------------------------------------


def test_sync_creates_guest_mazmo_profile_for_new_guest(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync creates a GuestMazmoProfile row for a brand-new guest,
    populated from the same Mazmo user details used to create the guest.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/111/default.jpg"),
            age=25,
            gender="female",
            pronoun="she/her",
            suspended=False,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob", displayname="Bob", avatar=None, age=None, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    alice = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    profile = session.get(GuestMazmoProfile, alice.id)
    assert profile is not None
    assert profile.avatar_url == "https://cdn.mazmo.net/avatars/111/default.jpg"
    assert profile.age == 25
    assert profile.gender == "female"
    assert profile.pronoun == "she/her"
    assert profile.mazmo_suspended is False
    assert profile.mazmo_banned is False


def test_sync_updates_guest_mazmo_profile_on_subsequent_sync(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify a second sync overwrites the same GuestMazmoProfile row (no
    duplicate) when Mazmo reports different age/mazmo_suspended values,
    and synced_at advances.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice", displayname="Alice", avatar=None, age=25, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
        222: SimpleNamespace(
            username="bob", displayname="Bob", avatar=None, age=None, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
    }
    resp1 = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp1.status_code == status.HTTP_200_OK

    alice = session.exec(select(Guest).where(Guest.mazmo_user_id == 111)).one()
    first_profile = session.get(GuestMazmoProfile, alice.id)
    assert first_profile is not None
    first_synced_at = first_profile.synced_at

    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice", displayname="Alice", avatar=None, age=26, gender=None, pronoun=None,
            suspended=True, banned=False,
        ),
        222: SimpleNamespace(
            username="bob", displayname="Bob", avatar=None, age=None, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
    }
    resp2 = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp2.status_code == status.HTTP_200_OK

    all_profiles = session.exec(select(GuestMazmoProfile).where(GuestMazmoProfile.guest_id == alice.id)).all()
    assert len(all_profiles) == 1
    updated_profile = all_profiles[0]
    assert updated_profile.age == 26
    assert updated_profile.mazmo_suspended is True
    assert updated_profile.synced_at >= first_synced_at


def test_sync_updates_guest_mazmo_profile_even_when_displayname_unchanged(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Regression guard: unlike GuestDisplaynameHistory, GuestMazmoProfile
    has no "only if changed" gate - a guest whose displayname is already
    up to date (and therefore produces no row in _upsert_guests's
    RETURNING set) must still get mazmo_suspended/age/etc. refreshed if
    Mazmo reports a new value for them.
    """
    alice = make_guest(session, mazmo_user_id=111, mazmo_handle="alice", displayname="Alice")
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice", displayname="Alice", avatar=None, age=None, gender=None, pronoun=None,
            suspended=True, banned=False,
        ),
        222: SimpleNamespace(
            username="bob", displayname="Bob", avatar=None, age=None, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    session.refresh(alice)
    assert alice.displayname == "Alice"
    profile = session.get(GuestMazmoProfile, alice.id)
    assert profile is not None
    assert profile.mazmo_suspended is True


def test_sync_handles_guest_with_missing_optional_profile_fields(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify sync does not fail when Mazmo omits age/gender for one guest
    in the batch - that guest's profile row gets NULLs, not an error.
    """
    mock_mazmo.fetch_users.return_value = {
        111: SimpleNamespace(
            username="alice",
            displayname="Alice",
            avatar=SimpleNamespace(default="https://cdn.mazmo.net/avatars/111/default.jpg"),
            age=25,
            gender="female",
            pronoun="she/her",
            suspended=False,
            banned=False,
        ),
        222: SimpleNamespace(
            username="bob", displayname="Bob", avatar=None, age=None, gender=None, pronoun=None,
            suspended=False, banned=False,
        ),
    }

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    bob = session.exec(select(Guest).where(Guest.mazmo_user_id == 222)).one()
    profile = session.get(GuestMazmoProfile, bob.id)
    assert profile is not None
    assert profile.avatar_url is None
    assert profile.age is None
    assert profile.gender is None
    assert profile.pronoun is None
```

- [ ] **Step 7: Run the new integration tests**

Run: `uv run pytest tests/test_sync.py -v`
Expected: all PASS, including the 4 new tests.

- [ ] **Step 8: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 9: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 10: Commit**

```bash
git add app/services/sync.py tests/test_sync.py
git commit -m "feat: sync unconditionally upserts GuestMazmoProfile for the full batch"
```

---

## Task 5: `PATCH /guests/{guest_id}/link-mazmo` and `POST /guests/mazmo` populate `GuestMazmoProfile`

**Files:**
- Modify: `app/routers/guests.py` (imports, `link_guest_to_mazmo`, `create_guest_from_mazmo`)
- Test: `tests/test_guest_mazmo_profile.py` (append)

**Interfaces:**
- Consumes: `GuestMazmoProfile` (Task 1), `MazmoUserWithId` with profile fields (Task 3).
- Produces: nothing new consumed by later tasks in this plan, other than the `GuestMazmoProfile` import this task adds to `app/routers/guests.py`, which Task 6 also relies on.

Per the approved spec's `POST /guests/mazmo` section, `create_guest_from_mazmo` also calls `fetch_user_by_username()` to create a brand-new guest directly from a Mazmo profile (not just `link-mazmo`, which attaches Mazmo to an already-existing guest). Since it has the same unified-parsing `MazmoUserWithId` result in memory, it must create the corresponding `GuestMazmoProfile` row in the same commit as the `Guest` and `EventLog(GUEST_CREATED, ...)` it already creates - same pattern as `link-mazmo`, no extra Mazmo call. This task covers both endpoints since they share the exact same construction logic.

- [ ] **Step 1: Add `GuestMazmoProfile` to the models import in `app/routers/guests.py`**

Change:

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

to:

```python
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    OrganizationBan,
    User,
)
```

(If the prerequisite plan's exact import block differs slightly, just add `GuestMazmoProfile` in alphabetical position to whatever `app.models.models` import already exists.)

- [ ] **Step 2: Populate `GuestMazmoProfile` in `link_guest_to_mazmo`**

Find the body of `link_guest_to_mazmo` (as rewritten by the prerequisite plan's Task 5), from `old_displayname = guest.displayname` through `session.refresh(guest)`:

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

Change it to:

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

    # Populate GuestMazmoProfile from the same lookup response already in
    # memory - no extra Mazmo call. Get-or-create against guest.id: a
    # guest that was previously linked then unlinked (which deletes its
    # profile row - see unlink_guest_mazmo below) has no existing row
    # here, but this stays correct even if one somehow already existed.
    profile = session.get(GuestMazmoProfile, guest.id)
    if profile is None:
        profile = GuestMazmoProfile(guest_id=guest.id)
    profile.avatar_url = mazmo_user.avatar.default if mazmo_user.avatar else None
    profile.age = mazmo_user.age
    profile.gender = mazmo_user.gender
    profile.pronoun = mazmo_user.pronoun
    profile.mazmo_suspended = mazmo_user.suspended
    profile.mazmo_banned = mazmo_user.banned
    profile.synced_at = now
    session.add(profile)

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

- [ ] **Step 3: Update the function's docstring**

Find (the prerequisite plan's version):

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

Change it to:

```python
    """
    Attach a Mazmo account to a guest created without one.

    Overwrites mazmo_user_id, mazmo_handle, and displayname with the
    Mazmo profile data. instagram_username is left untouched.

    If the incoming Mazmo displayname differs from the guest's previous
    value, writes a GuestDisplaynameHistory row (source=MAZMO_LINK) and
    an EventLog(GUEST_DISPLAYNAME_CHANGED) entry, alongside the existing
    EventLog(GUEST_MAZMO_LINKED) - same commit, same timestamp.

    Also upserts GuestMazmoProfile (avatar, age, gender, pronoun,
    mazmo_suspended, mazmo_banned) from the same Mazmo lookup response -
    unconditionally, not just when the displayname changed, and without
    any extra call to Mazmo. Same commit as everything else here.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already linked, or if the Mazmo account
    is already linked to a different guest (no automatic merge).
    """
```

- [ ] **Step 4: Populate `GuestMazmoProfile` in `create_guest_from_mazmo`**

`create_guest_from_mazmo` is untouched by the prerequisite plan, so this anchors on today's actual code. Find the tail of the function, from `guest = Guest(` through `session.refresh(guest)`:

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

Change it to:

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

    # Populate GuestMazmoProfile from the same lookup response already in
    # memory - no extra Mazmo call. Always a fresh insert here (never an
    # update): guest.id was just generated above, so no GuestMazmoProfile
    # row can already exist for it - unlike link_guest_to_mazmo, which
    # attaches Mazmo to a guest that may have been linked (and unlinked)
    # before.
    profile = GuestMazmoProfile(
        guest_id=guest.id,
        avatar_url=mazmo_user.avatar.default if mazmo_user.avatar else None,
        age=mazmo_user.age,
        gender=mazmo_user.gender,
        pronoun=mazmo_user.pronoun,
        mazmo_suspended=mazmo_user.suspended,
        mazmo_banned=mazmo_user.banned,
    )

    session.add(guest)
    session.add(event)
    session.add(profile)
    session.commit()
    session.refresh(guest)
```

- [ ] **Step 5: Update `create_guest_from_mazmo`'s docstring**

Change:

```python
    """
    Register a guest using their Mazmo username handle.

    Looks up the canonical Mazmo user ID and profile data automatically,
    so staff at the door only need to know the handle (e.g. "cindydark").

    Returns 404 if the username doesn't exist on Mazmo.
    Returns 409 if that mazmo_user_id is already registered.
    Returns 504 if Mazmo is unreachable.
    """
```

to:

```python
    """
    Register a guest using their Mazmo username handle.

    Looks up the canonical Mazmo user ID and profile data automatically,
    so staff at the door only need to know the handle (e.g. "cindydark").

    Also creates the guest's GuestMazmoProfile (avatar, age, gender,
    pronoun, mazmo_suspended, mazmo_banned) from the same Mazmo lookup
    response - same commit as the Guest and EventLog(GUEST_CREATED, ...)
    rows, no extra call to Mazmo.

    Returns 404 if the username doesn't exist on Mazmo.
    Returns 409 if that mazmo_user_id is already registered.
    Returns 504 if Mazmo is unreachable.
    """
```

- [ ] **Step 6: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py tests/test_guest_displayname_history.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 7: Add the new tests**

Append to `tests/test_guest_mazmo_profile.py`:

```python
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
```

This relies on `mock_mazmo_for_guests`'s default return value, updated in Task 3, Step 5 (`age=29`, `gender="female"`, `pronoun="she/her"`, avatar URL `https://cdn.mazmo.net/avatars/39119/default.jpg`).

Add the needed imports at the top of `tests/test_guest_mazmo_profile.py` - change:

```python
from datetime import UTC, datetime

from sqlmodel import Session

from app.models.models import GuestMazmoProfile
from tests.conftest import make_guest
```

to:

```python
import uuid
from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import GuestMazmoProfile
from tests.conftest import make_guest
```

- [ ] **Step 8: Run the new tests**

Run: `uv run pytest tests/test_guest_mazmo_profile.py -v`
Expected: all PASS, including the 2 new tests.

- [ ] **Step 9: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 10: Commit**

```bash
git add app/routers/guests.py tests/test_guest_mazmo_profile.py
git commit -m "feat: link-mazmo and create-guest-from-mazmo populate GuestMazmoProfile"
```

---

## Task 6: `PATCH /guests/{guest_id}/unlink-mazmo` deletes `GuestMazmoProfile`

**Files:**
- Modify: `app/routers/guests.py` (`unlink_guest_mazmo`)
- Test: `tests/test_guest_mazmo_profile.py` (append)

**Interfaces:**
- Consumes: `GuestMazmoProfile` (already imported into `app/routers/guests.py` by Task 5).

- [ ] **Step 1: Delete the profile row in `unlink_guest_mazmo`**

`unlink_guest_mazmo` is untouched by the prerequisite plan, so this anchors on today's actual code. Change:

```python
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
```

to:

```python
    previous_mazmo_user_id = guest.mazmo_user_id
    guest.mazmo_user_id = None
    guest.mazmo_handle = None

    event = EventLog(
        event_type=EventType.GUEST_MAZMO_UNLINKED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    # Delete-if-exists: a guest linked but never synced/re-linked has no
    # profile row yet, and that is not an error here.
    profile = session.get(GuestMazmoProfile, guest.id)
    if profile is not None:
        session.delete(profile)

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)
```

- [ ] **Step 2: Update the function's docstring**

Change:

```python
    """
    Detach a guest's Mazmo account, e.g. to undo a link made by mistake.

    displayname is NOT reverted to any prior value (no name history is
    kept) - it stays as whatever it was, and can be corrected afterward
    via PATCH /guests/{guest_id}. The freed mazmo_user_id can be linked
    to this guest again, or to a different one.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently linked.
    Returns 409 if the guest has an active ban in any organization - see
    the ban-evasion guard below for why this is blocked.
    """
```

to:

```python
    """
    Detach a guest's Mazmo account, e.g. to undo a link made by mistake.

    displayname is NOT reverted to any prior value (no name history is
    kept) - it stays as whatever it was, and can be corrected afterward
    via PATCH /guests/{guest_id}. The freed mazmo_user_id can be linked
    to this guest again, or to a different one.

    Also deletes the guest's GuestMazmoProfile row, if one exists.
    Keeping stale age/gender/avatar data around for an account that is
    no longer linked could be shown as if it were still valid.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently linked.
    Returns 409 if the guest has an active ban in any organization - see
    the ban-evasion guard below for why this is blocked.
    """
```

- [ ] **Step 3: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py -v`
Expected: all existing tests still PASS (including `test_unlink_then_relink_succeeds`, which doesn't assert on profile fields).

- [ ] **Step 4: Add the new tests**

Append to `tests/test_guest_mazmo_profile.py`:

```python
# -- PATCH /guests/{id}/unlink-mazmo deletes GuestMazmoProfile -------------------


def test_unlink_mazmo_deletes_guest_mazmo_profile(client: TestClient, staff_headers: dict, session: Session):
    """Verify unlink-mazmo deletes the guest's GuestMazmoProfile row."""
    guest = make_guest(session, mazmo_user_id=555, mazmo_handle="tobeunlinked")
    session.add(GuestMazmoProfile(guest_id=guest.id, age=40))
    session.flush()

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert session.get(GuestMazmoProfile, guest.id) is None


def test_unlink_mazmo_on_guest_without_profile_row_succeeds(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify unlinking a guest that was linked but never synced (so it has
    no GuestMazmoProfile row yet) does not fail.
    """
    guest = make_guest(session, mazmo_user_id=556, mazmo_handle="neversynced")

    resp = client.patch(f"/guests/{guest.id}/unlink-mazmo", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert session.get(GuestMazmoProfile, guest.id) is None
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/test_guest_mazmo_profile.py -v`
Expected: all PASS, including the 2 new tests.

- [ ] **Step 6: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add app/routers/guests.py tests/test_guest_mazmo_profile.py
git commit -m "feat: unlink-mazmo deletes GuestMazmoProfile"
```

---

## Task 7: Expose `mazmo_profile` on `GuestPublic`, fix N+1 in `app/routers/guests.py`

**Files:**
- Modify: `app/schemas/guests.py` (new `GuestMazmoProfilePublic`, `GuestPublic.mazmo_profile`)
- Modify: `app/schemas/__init__.py` (export `GuestMazmoProfilePublic`)
- Modify: `app/routers/guests.py` (imports, `_get_guest_or_404`, `list_guests`, `get_guest_by_mazmo_handle`)
- Test: `tests/test_guest_mazmo_profile.py` (append)

**Interfaces:**
- Produces: `GuestMazmoProfilePublic` (schema, fields `avatar_url: str | None`, `age: int | None`, `gender: str | None`, `pronoun: str | None`, `mazmo_suspended: bool`, `mazmo_banned: bool`, `synced_at: datetime`), `GuestPublic.mazmo_profile: GuestMazmoProfilePublic | None`.
- Consumes: `Guest.mazmo_profile` relationship (Task 1).

- [ ] **Step 1: Add `GuestMazmoProfilePublic` and the `mazmo_profile` field to `app/schemas/guests.py`**

Change:

```python
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
```

to:

```python
class GuestMazmoProfilePublic(BaseModel):
    """
    Snapshot of extended Mazmo profile data for a linked, synced guest.

    mazmo_suspended/mazmo_banned are Mazmo's own account-level flags -
    unrelated to this app's own ban system (OrganizationBan / the
    is_banned field on GuestWithBanPublic). Prefixed even here to avoid
    that confusion once this is flattened into JSON.
    """

    model_config = ConfigDict(from_attributes=True)

    avatar_url: str | None
    age: int | None
    gender: str | None
    pronoun: str | None
    mazmo_suspended: bool
    mazmo_banned: bool
    synced_at: datetime


class GuestPublic(BaseModel):
    """
    A guest's identity (cached locally, may or may not have a Mazmo account).

    Identity-only - no RSVP or ban state. Bans are per-org and are
    included only in org-scoped endpoints via GuestWithBanPublic.

    mazmo_profile is None both for guests never linked to Mazmo, and for
    linked guests that haven't appeared in a sync or link-mazmo call yet
    - two different situations, same null result here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
    instagram_username: str | None
    mazmo_profile: GuestMazmoProfilePublic | None = None
```

- [ ] **Step 2: Export `GuestMazmoProfilePublic` from `app/schemas/__init__.py`**

By the time this task runs, both prerequisite plans have already touched this same import block: `docs/superpowers/plans/2026-08-12-guest-type-payment-exemption.md`'s Task 3 Step 4 inserted `GuestTypeUpdateRequest` (alphabetically after `GuestPublic`, before `GuestWithBanPublic`), and `docs/superpowers/plans/2026-08-12-guest-displayname-history.md`'s Task 7 Step 2 inserted `GuestDisplaynameHistoryListResponse`/`GuestDisplaynameHistoryPublic` (alphabetically before `GuestListResponse`). Change:

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
    GuestMazmoProfilePublic,
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

(If the prerequisite plans' exact edits landed slightly differently, just add `GuestMazmoProfilePublic` in alphabetical position - after `GuestListResponse`, before `GuestPublic` - to whatever `app.schemas.guests` import block already exists, without removing or duplicating any of the prior plans' names.)

And in `__all__`, change:

```python
    "GuestDisplaynameHistoryListResponse",
    "GuestDisplaynameHistoryPublic",
    "GuestListResponse",
    "GuestPublic",
    "GuestTypeStats",
    "GuestTypeUpdateRequest",
    "GuestWithBanPublic",
```

to:

```python
    "GuestDisplaynameHistoryListResponse",
    "GuestDisplaynameHistoryPublic",
    "GuestListResponse",
    "GuestMazmoProfilePublic",
    "GuestPublic",
    "GuestTypeStats",
    "GuestTypeUpdateRequest",
    "GuestWithBanPublic",
```

- [ ] **Step 3: Import `selectinload` in `app/routers/guests.py`**

Change:

```python
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
```

to:

```python
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
```

- [ ] **Step 4: Eager-load `mazmo_profile` in `_get_guest_or_404`**

Change:

```python
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
```

to:

```python
def _get_guest_or_404(session: Session, guest_id: uuid.UUID) -> Guest:
    guest = session.exec(
        select(Guest).where(Guest.id == guest_id).options(selectinload(Guest.mazmo_profile))
    ).first()
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
```

This changes the eager-loading behavior of `get_guest`, `link_guest_to_mazmo`, `unlink_guest_mazmo`, and `update_guest` - all of which call `_get_guest_or_404` - without changing any of their other behavior.

- [ ] **Step 5: Eager-load `mazmo_profile` in `list_guests`**

Change:

```python
    query = select(Guest)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Guest.displayname.ilike(pattern), Guest.mazmo_handle.ilike(pattern)))  # type: ignore[union-attr]
```

to:

```python
    query = select(Guest).options(selectinload(Guest.mazmo_profile))
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Guest.displayname.ilike(pattern), Guest.mazmo_handle.ilike(pattern)))  # type: ignore[union-attr]
```

- [ ] **Step 6: Eager-load `mazmo_profile` in `get_guest_by_mazmo_handle`**

Change:

```python
    guest = session.exec(select(Guest).where(Guest.mazmo_handle == mazmo_handle)).first()
```

to:

```python
    guest = session.exec(
        select(Guest).where(Guest.mazmo_handle == mazmo_handle).options(selectinload(Guest.mazmo_profile))
    ).first()
```

- [ ] **Step 7: Run the existing guests test suite to confirm nothing broke**

Run: `uv run pytest tests/test_guests.py tests/test_guest_displayname_history.py tests/test_guest_mazmo_profile.py -v`
Expected: all PASS.

- [ ] **Step 8: Add the new tests**

Append to `tests/test_guest_mazmo_profile.py`. First, add the remaining imports needed for this task's tests - change:

```python
import uuid
from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import GuestMazmoProfile
from tests.conftest import make_guest
```

to:

```python
import uuid
from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.models.models import GuestMazmoProfile
from tests.conftest import make_guest, test_engine
```

Then append:

```python
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

    statements: list[str] = []

    def _listener(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", _listener)
    try:
        resp = client.get("/guests/", headers=staff_headers)
    finally:
        event.remove(test_engine, "before_cursor_execute", _listener)

    assert resp.status_code == status.HTTP_200_OK
    profile_statements = [s for s in statements if "guest_mazmo_profile" in s.lower()]
    assert len(profile_statements) == 1
```

- [ ] **Step 9: Run the new tests**

Run: `uv run pytest tests/test_guest_mazmo_profile.py -v`
Expected: all PASS, including the 4 new tests.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 11: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 12: Commit**

```bash
git add app/schemas/guests.py app/schemas/__init__.py app/routers/guests.py tests/test_guest_mazmo_profile.py
git commit -m "feat: expose GuestPublic.mazmo_profile, fix N+1 in guests router"
```

---

## Task 8: Expose `mazmo_profile` on `GuestWithBanPublic` in `app/routers/meetups.py`

**Files:**
- Modify: `app/routers/meetups.py` (imports, `list_meetup_guests`, `add_walkin_guest`)
- Test: `tests/test_meetups.py` (append)

**Interfaces:**
- Consumes: `GuestMazmoProfilePublic` (Task 7), `Guest.mazmo_profile` relationship (Task 1).
- No schema change in `app/schemas/guests.py`: `GuestWithBanPublic(GuestPublic)` already inherits `mazmo_profile: GuestMazmoProfilePublic | None = None` from `GuestPublic` the moment Task 7 lands (Pydantic v2 subclasses inherit parent field declarations automatically - verified directly: a `B(A)` subclass with `A.y: int | None = None` accepts both `B(x=1)` and `B(x=1, y=...)` without redeclaring `y`). The only reason the field reads as `null` in `list_meetup_guests`/`add_walkin_guest` today is that both build `GuestWithBanPublic` via explicit keyword arguments and neither passes `mazmo_profile` - a call-site gap, not a schema gap. This matches what the spec itself states plainly: "no se construye via `.model_validate()`... Si no se toca, `mazmo_profile` quedaria siempre implicitamente `None` ahi... porque el dato nunca llega a esa respuesta."

- [ ] **Step 1: Pass `mazmo_profile` in `list_meetup_guests` and extend its `selectinload`**

In `app/routers/meetups.py`, change:

```python
    rsvps = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
        .order_by(MeetupRsvp.rsvp_time)  # type: ignore[attr-defined]
    ).all()

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

to:

```python
    rsvps = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .options(selectinload(MeetupRsvp.guest).selectinload(Guest.mazmo_profile))  # type: ignore[arg-type]
        .order_by(MeetupRsvp.rsvp_time)  # type: ignore[attr-defined]
    ).all()

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
                mazmo_profile=GuestMazmoProfilePublic.model_validate(rsvp.guest.mazmo_profile)
                if rsvp.guest.mazmo_profile
                else None,
            ),
            rsvp=RsvpPublic.model_validate(rsvp),
        )
        for rsvp in rsvps
    ]
```

Only the query's `.options(...)` line and the added `mazmo_profile=...` keyword change - nothing else in this function moves.

- [ ] **Step 2: Pass `mazmo_profile` in `add_walkin_guest`**

Still in `app/routers/meetups.py`, change:

```python
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

to:

```python
    return MeetupGuestPublic(
        guest=GuestWithBanPublic(
            id=guest.id,
            mazmo_user_id=guest.mazmo_user_id,
            mazmo_handle=guest.mazmo_handle,
            displayname=guest.displayname,
            instagram_username=guest.instagram_username,
            is_banned=ban is not None,
            mazmo_profile=GuestMazmoProfilePublic.model_validate(guest.mazmo_profile) if guest.mazmo_profile else None,
        ),
        rsvp=RsvpPublic.model_validate(rsvp),
    )
```

`guest` here comes from a plain `session.get(Guest, guest_id)` a few lines above (single guest, not a list) - no `selectinload` needed, reading `guest.mazmo_profile` triggers at most one lazy-load query for this one guest, matching the spec's own reasoning ("no hay riesgo de N+1 real").

- [ ] **Step 3: Import `GuestMazmoProfilePublic` in `app/routers/meetups.py`**

Change:

```python
from app.schemas import (
    CheckedInByPublic,
    CheckInResponse,
    GuestPublic,
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

to:

```python
from app.schemas import (
    CheckedInByPublic,
    CheckInResponse,
    GuestMazmoProfilePublic,
    GuestPublic,
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

(`selectinload` is already imported at the top of this file from Task-1-era code, and `Guest` is already imported from `app.models.models` - no other import changes needed.)

- [ ] **Step 4: Run the existing meetups test suite to confirm nothing broke**

Run: `uv run pytest tests/test_meetups.py -v`
Expected: all existing tests still PASS (including `test_list_meetup_guests_includes_is_banned_field`, which doesn't assert on `mazmo_profile`).

- [ ] **Step 5: Add the new tests**

In `tests/test_meetups.py`, change the imports at the top from:

```python
from app.models.models import EventLog, EventType, MeetupRsvp, Organization, OrgRole, User
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_meetup, make_org_member, make_rsvp
```

to:

```python
from sqlalchemy import event

from app.models.models import EventLog, EventType, GuestMazmoProfile, MeetupRsvp, Organization, OrgRole, User
from app.services.mazmo import MazmoAPIError, MazmoNetworkError
from tests.conftest import make_guest, make_meetup, make_org_member, make_rsvp, test_engine
```

(Keep this import ordered per this file's existing groups - stdlib/third-party first, then `app.*`, then `tests.*` - `sqlalchemy` goes with the other third-party imports near the top of the file, alongside `pytest`/`fastapi`/`sqlmodel`.)

Then append, after the existing `# -- List meetup guests --` and `# -- Add walk-in guest --` test blocks (near the end of their respective sections, right before the next `# --` section header each) or simply at the end of the file - either placement is fine, since these are independent, self-contained test functions:

```python
# -- Exposure of mazmo_profile on GuestWithBanPublic --------------------------


def test_meetup_guest_list_includes_mazmo_profile_when_linked_and_synced(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify GET .../meetups/{id}/guests includes mazmo_profile for a
    guest that is linked to Mazmo and has been synced.

    WHY: list_meetup_guests builds GuestWithBanPublic by hand via
    keyword arguments, not .model_validate() - unlike GuestPublic's
    other consumers, the field has to be wired through explicitly or it
    silently stays null forever, regardless of what's in the DB.
    """
    guest = make_guest(session, mazmo_user_id=801, mazmo_handle="synced_meetup_guest")
    session.add(
        GuestMazmoProfile(
            guest_id=guest.id,
            avatar_url="https://cdn.mazmo.net/avatars/801/default.jpg",
            age=27,
            gender="female",
            pronoun="she/her",
            mazmo_suspended=False,
            mazmo_banned=False,
        )
    )
    make_rsvp(session, meetup=meetup, guest=guest)
    session.flush()

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    profile = resp.json()["guests"][0]["guest"]["mazmo_profile"]
    assert profile is not None
    assert profile["avatar_url"] == "https://cdn.mazmo.net/avatars/801/default.jpg"
    assert profile["age"] == 27
    assert profile["gender"] == "female"
    assert profile["pronoun"] == "she/her"
    assert profile["mazmo_suspended"] is False
    assert profile["mazmo_banned"] is False


def test_meetup_guest_list_mazmo_profile_null_when_not_linked(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """Verify mazmo_profile is null in the meetup guest list for a guest with no GuestMazmoProfile row."""
    guest = make_guest(session, mazmo_user_id=802, mazmo_handle="unsynced_meetup_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["guests"][0]["guest"]["mazmo_profile"] is None


def test_walkin_guest_response_includes_mazmo_profile_when_linked_and_synced(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify the add-walkin response includes mazmo_profile for a guest
    that is linked to Mazmo and has been synced.

    WHY: add_walkin_guest builds GuestWithBanPublic the same
    hand-assembled way as list_meetup_guests - same gap, same fix.
    """
    guest = make_guest(session, mazmo_user_id=803, mazmo_handle="walkin_with_profile")
    session.add(GuestMazmoProfile(guest_id=guest.id, age=45, mazmo_suspended=True))
    session.flush()

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    profile = resp.json()["guest"]["mazmo_profile"]
    assert profile is not None
    assert profile["age"] == 45
    assert profile["mazmo_suspended"] is True


def test_meetup_guest_list_does_not_trigger_n_plus_1_for_mazmo_profile(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify GET .../meetups/{id}/guests loads mazmo_profile for every
    RSVPed guest via a single extra query (the nested selectinload), not
    one query per guest.
    """
    for i in range(810, 816):
        guest = make_guest(session, mazmo_user_id=i, mazmo_handle=f"meetup_guest{i}")
        session.add(GuestMazmoProfile(guest_id=guest.id, age=i - 800))
        make_rsvp(session, meetup=meetup, guest=guest)
    session.flush()

    statements: list[str] = []

    def _listener(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", _listener)
    try:
        resp = client.get(
            f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
            headers=staff_headers,
        )
    finally:
        event.remove(test_engine, "before_cursor_execute", _listener)

    assert resp.status_code == status.HTTP_200_OK
    profile_statements = [s for s in statements if "guest_mazmo_profile" in s.lower()]
    assert len(profile_statements) == 1
```

- [ ] **Step 6: Run the new tests**

Run: `uv run pytest tests/test_meetups.py -v -k mazmo_profile`
Expected: all 4 new tests PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 8: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 9: Run ruff**

Run: `uv run ruff format . && uv run ruff check .`
Expected: no formatting changes needed (or apply them), no lint errors.

- [ ] **Step 10: Commit**

```bash
git add app/routers/meetups.py tests/test_meetups.py
git commit -m "feat: expose GuestWithBanPublic.mazmo_profile in meetup guest list and walk-in endpoints"
```

---

## Task 9: End-to-end lifecycle test

**Files:**
- Test: `tests/test_guest_mazmo_profile.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-8. This task adds no new production code, only a test exercising the full stack together.

- [ ] **Step 1: Add the remaining imports needed for the E2E test**

Change the top of `tests/test_guest_mazmo_profile.py` from:

```python
import uuid
from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.models.models import GuestMazmoProfile
from tests.conftest import make_guest, test_engine
```

to:

```python
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.models.models import GuestMazmoProfile, Meetup, Organization
from tests.conftest import make_guest, test_engine
```

- [ ] **Step 2: Add the E2E test**

Append to `tests/test_guest_mazmo_profile.py`:

```python
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
```

- [ ] **Step 3: Run the new test**

Run: `uv run pytest tests/test_guest_mazmo_profile.py::test_link_sync_unlink_mazmo_profile_lifecycle_end_to_end -v`
Expected: PASS.

- [ ] **Step 4: Run the full test suite one final time**

Run: `uv run pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 5: Run basedpyright**

Run: `uv run basedpyright`
Expected: no new errors.

- [ ] **Step 6: Run ruff**

Run: `uv run ruff format . && uv run ruff check .`
Expected: no formatting changes needed (or apply them), no lint errors.

- [ ] **Step 7: Commit**

```bash
git add tests/test_guest_mazmo_profile.py
git commit -m "test: add guest Mazmo profile end-to-end lifecycle test"
```

---

## Self-Review

**1. Spec coverage:**

- `GuestMazmoProfile` table (`guest_id` PK, `avatar_url`/`age`/`gender`/`pronoun`/`mazmo_suspended`/`mazmo_banned`/`synced_at`, all shape decisions documented in the class docstring) - Task 1, Step 3. Covered.
- `Guest.mazmo_profile` relationship - Task 1, Step 2. Covered.
- Migration, no backfill, FK to `guests.id` - Task 2. Covered.
- `MazmoAvatarEntry` + `MazmoUserEntry`'s 6 new optional fields - Task 3, Step 1. Covered.
- Unified parsing: `fetch_user_by_username()` validates through `MazmoUserEntry.model_validate()` instead of hand-rolled parsing, extracts `id` separately - Task 3, Step 3. Covered.
- Sync's unconditional bulk upsert (`INSERT ... ON CONFLICT (guest_id) DO UPDATE`, every guest in the batch regardless of displayname change) - Task 4, Step 4. Covered.
- `link-mazmo` upserts `GuestMazmoProfile` from the already-in-memory lookup, no extra Mazmo call - Task 5, Step 2. Covered.
- `POST /guests/mazmo` (`create_guest_from_mazmo`) creates `GuestMazmoProfile` in the same commit as `Guest`/`EventLog(GUEST_CREATED, ...)` - Task 5, Step 4. Covered.
- `unlink-mazmo` deletes the profile row, delete-if-exists, runs after the ban-evasion guard - Task 6, Step 1. Covered.
- `GuestMazmoProfilePublic` schema + `GuestPublic.mazmo_profile` (`None` for both "never linked" and "linked but not yet synced") - Task 7, Step 1. Covered.
- N+1 fix on `GET /guests/` (`list_guests`) plus the same fix on `_get_guest_or_404` and `get_guest_by_mazmo_handle` - Task 7, Steps 3-6. Covered.
- **`GuestWithBanPublic` exposure (new spec section "Exposicion en `GuestWithBanPublic`")** - `mazmo_profile=GuestMazmoProfilePublic.model_validate(rsvp.guest.mazmo_profile) if rsvp.guest.mazmo_profile else None` in `list_meetup_guests`, the matching nested `selectinload(MeetupRsvp.guest).selectinload(Guest.mazmo_profile)`, and the equivalent `mazmo_profile=...` kwarg in `add_walkin_guest` (no `selectinload` needed there - single guest) - Task 8, Steps 1-2. Covered.
- Out-of-scope items respected: no history/versioning table for these 6 fields, no "refresh now" endpoint, no retroactive backfill for guests not in an upcoming meetup, no frontend page design. Confirmed no task does any of these.
- Every test named in the spec's "Tests" section has a corresponding step: 3 unit (Task 3, Step 8), 4 sync integration (Task 4, Step 6), 1 link-mazmo (Task 5, Step 7), 1 create-from-mazmo (Task 5, Step 7), 2 unlink-mazmo (Task 6, Step 4), 4 `GuestPublic` exposure (Task 7, Step 8), **4 `GuestWithBanPublic` exposure (Task 8, Step 5)**, 1 E2E (Task 9, Step 2) = 20 spec items, all present.

**2. Placeholder scan:** No "TBD"/"TODO"/"handle appropriately" language anywhere in the steps above, including the new Task 8. The one place Task 8 asks the implementer to pick between two equally-valid options (Step 5's note that the new tests can go at the end of the file or interleaved after their matching existing sections) gives both options explicitly and states they are behaviorally equivalent - not a gap.

**3. Type consistency:** `GuestMazmoProfilePublic` (defined once, in Task 7 Step 1) is constructed identically in both places that build it by hand: Task 8 Step 1's `list_meetup_guests` (`GuestMazmoProfilePublic.model_validate(rsvp.guest.mazmo_profile) if rsvp.guest.mazmo_profile else None`) and Task 8 Step 2's `add_walkin_guest` (`GuestMazmoProfilePublic.model_validate(guest.mazmo_profile) if guest.mazmo_profile else None`) - same `.model_validate()` call, same `if ... else None` guard, same source attribute name (`.mazmo_profile`) read off a `Guest` ORM instance either way. `GuestWithBanPublic.mazmo_profile` itself is not redeclared anywhere - Task 8 relies on the field `GuestPublic` already declares in Task 7 Step 1, inherited automatically by the `GuestWithBanPublic(GuestPublic)` subclass, which keeps exactly one place in the codebase (`app/schemas/guests.py`) owning that field's type (`GuestMazmoProfilePublic | None = None`) rather than two schemas independently declaring the same annotation and risking drift. The nested `selectinload(MeetupRsvp.guest).selectinload(Guest.mazmo_profile)` added in Task 8 Step 1 walks the exact same `Guest.mazmo_profile` relationship (Task 1 Step 2) that `app/routers/guests.py`'s `selectinload(Guest.mazmo_profile)` calls walk in Task 7 - same relationship, same target type, two different starting points (`Guest` directly vs. `MeetupRsvp.guest`).

**Resolved during design (no longer a discrepancy):** the spec originally scoped the N+1 `selectinload` fix and the `mazmo_profile` field wiring to `app/routers/guests.py` only (see Design decision 3 above), reasoning that `app/routers/meetups.py`'s `GuestWithBanPublic` construction was a pre-existing data-completeness gap, not something this plan needed to touch. The spec was subsequently extended with the "Exposicion en `GuestWithBanPublic`" section specifically to close that gap - staff at the door use this endpoint, and leaving `mazmo_profile` permanently `null` there would have made the whole feature invisible in the one view it was originally requested for. Task 8 above implements that extension; Design decision 3's original reasoning is left in place (annotated with an "Update" note) since it correctly explains the state of the code through Task 7, not an error to be silently erased.
