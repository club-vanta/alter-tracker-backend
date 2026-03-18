from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, Relationship, SQLModel

from app.services.mazmo import MazmoUserId

# ── Python enum (validation only, not stored in Postgres as a type) ───────────


class PossibleRoles(StrEnum):
    """
    Used for Python/Pydantic validation only.
    The actual role name is stored as a VARCHAR in the `user_roles` table
    and referenced via a foreign key from `users.role_id`.
    """

    STAFF = "STAFF"
    ADMIN = "ADMIN"


# ── Role table ────────────────────────────────────────────────────────────────


class Role(SQLModel, table=True):
    """
    Lookup table for staff roles.
    Seeded once with STAFF and ADMIN rows.
    `users.role_id` is a foreign key to this table.
    """

    __tablename__ = "user_roles"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, max_length=32)

    # Back-reference
    users: list["User"] = Relationship(back_populates="role")


# ── User table ────────────────────────────────────────────────────────────────


class User(SQLModel, table=True):
    """
    Represents an event staff member / volunteer account.

    New registrations start with `is_approved = False`.
    An admin must flip this flag before the user can log in.
    `role_id` is a FK to `user_roles`, defaulting to the STAFF row.
    """

    __tablename__ = "staff_users"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    hashed_password: str
    is_approved: bool = Field(default=False)
    role_id: int = Field(foreign_key="user_roles.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationship - loads the full Role object
    role: Role | None = Relationship(back_populates="users")


# ── Guest table ───────────────────────────────────────────────────────────────


class Guest(SQLModel, table=True):
    """
    Represents an RSVP'd guest fetched from the Mazmo platform.

    Primary key is Mazmo's own user ID so that the Postgres upsert
    (INSERT ... ON CONFLICT DO NOTHING) is idempotent.

    CRITICAL: The upsert NEVER overwrites `has_arrived`, `arrival_time`,
    or `arrival_order`. These are set only by the door tracker check-in flow.

    `arrival_order` uses a DB-level sequence to guarantee strict, gap-free
    ordering even under concurrent check-ins from multiple staff phones.
    """

    __tablename__ = "guests"  # type: ignore[assignment]

    mazmo_user_id: MazmoUserId = Field(primary_key=True)
    username: str = Field(index=True)
    displayname: str
    rsvp_time: datetime

    # ── Door tracker fields (mutated only by check-in endpoint) ──────────────
    has_arrived: bool = Field(default=False, index=True)
    arrival_time: datetime | None = None

    # Atomic, sequential arrival number. Enforced unique at the DB level.
    # NULL until the guest checks in.
    arrival_order: int | None = Field(default=None, unique=True)
