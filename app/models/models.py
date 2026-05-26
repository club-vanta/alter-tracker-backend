import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass  # For forward references

from app.domain_types import MazmoUserId

# ── Python enum (validation only, not stored in Postgres as a type) ───────────


class PossibleRoles(StrEnum):
    """
    Used for Python/Pydantic validation only.
    The actual role name is stored as a VARCHAR in the `user_roles` table
    and referenced via a foreign key from `users.role_id`.
    """

    STAFF = "STAFF"
    ADMIN = "ADMIN"


class EventType(StrEnum):
    """
    Types of auditable events tracked in the event_log table.

    These events create an audit trail for important actions that affect
    guests, allowing admins to review what happened and when.
    """

    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"
    BAN = "BAN"
    UNBAN = "UNBAN"
    MEETUP_FINALIZED = "MEETUP_FINALIZED"
    MEETUP_UNFINALIZED = "MEETUP_UNFINALIZED"
    WALKIN = "WALKIN"
    GUEST_CREATED = "GUEST_CREATED"


# ── Organization table ────────────────────────────────────────────────────────


class Organization(SQLModel, table=True):
    """
    Represents a tenant organization using the app.

    Each organization has its own staff, meetups, and bans.
    Mazmo guests are global (shared across orgs) but bans are per-org.

    `slug` is used as the subdomain identifier (e.g. "club-vanta" →
    velvet.club-vanta.com). Seeded with Club Vanta (id=1) in migration 0008.
    """

    __tablename__ = "organizations"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    slug: str = Field(unique=True, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    `org_id` scopes this user to a specific organization.

    Soft-delete: Instead of deleting users, we disable them to preserve
    audit trails. A disabled user cannot log in but their data remains
    for historical reference (e.g., "who checked in this guest?").
    """

    __tablename__ = "users"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    hashed_password: str
    is_approved: bool = Field(default=False)
    role_id: int = Field(foreign_key="user_roles.id")
    org_id: int = Field(foreign_key="organizations.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Disable fields (soft-delete) ──
    is_disabled: bool = Field(default=False)
    disabled_at: datetime | None = Field(default=None)
    disabled_by_id: int | None = Field(default=None, foreign_key="users.id")
    disabled_reason: str | None = Field(default=None, max_length=500)

    # Relationship - loads the full Role object
    role: Role | None = Relationship(back_populates="users")

    # Self-referential relationship to the admin who disabled this user
    disabled_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"remote_side": "User.id", "foreign_keys": "[User.disabled_by_id]"}
    )


# ── Guests, meetups and RSVPs ─────────────────────────────────────────────────
#
# These three models form a many-to-many relationship:
#
#   Guest <──── MeetupRsvp ────> Meetup
#
# MeetupRsvp is the "association table" but it's not just a dumb link table -
# it stores RSVP-specific data (rsvp_time, has_arrived, etc.).
#
# ─────────────────────────────────────────────────────────────────────────────


class MeetupRsvp(SQLModel, table=True):
    """
    Association object representing a Guest's attendance at a specific Meetup.

    CRITICAL: The background sync upsert NEVER overwrites `has_arrived`,
    `arrival_time`, `arrival_order`, or `checked_in_by_id`. These are set
    ONLY by the door tracker check-in flow.

    `arrival_order` represents the sequence of arrival for this specific meetup.
    `checked_in_by_id` records which staff member performed the check-in.
    """

    __tablename__ = "meetup_rsvps"  # type: ignore[assignment]

    # ── Composite Primary Key ──
    # Both fields together form the PK: (meetup_id, guest_id)
    # This means a guest can only RSVP once per meetup.
    meetup_id: uuid.UUID = Field(foreign_key="meetups.id", primary_key=True)
    guest_id: MazmoUserId = Field(foreign_key="guests.mazmo_user_id", primary_key=True, sa_type=Integer)

    # ── RSVP state ──
    rsvp_time: datetime
    cancelled_rsvp: bool = Field(default=False)

    # ── Door tracker fields (set by DB trigger on check-in) ──
    has_arrived: bool = Field(default=False, index=True)
    arrival_time: datetime | None = None
    arrival_order: int | None = Field(default=None)

    # ── Walk-in flag ──
    is_walkin: bool = Field(default=False)

    # ── Check-in attribution ──
    checked_in_by_id: int | None = Field(default=None, foreign_key="users.id")

    # ── Relationships ──
    guest: "Guest" = Relationship(back_populates="rsvps")
    meetup: "Meetup" = Relationship(back_populates="rsvps")

    # Staff member who checked in this guest
    checked_in_by: Optional["User"] = Relationship()


class Guest(SQLModel, table=True):
    """
    Represents the core identity of a user fetched from the Mazmo platform.

    Primary key is Mazmo's own user ID so that the Postgres upsert
    (INSERT ... ON CONFLICT DO NOTHING) is idempotent.

    This table only holds static user data. Event-specific data (like RSVPs
    and check-in times) is handled by the MeetupRsvp link table.

    Bans are NOT stored here — they live in GuestBan (per-org). A guest
    banned in one organization is not automatically banned in another.

    DOMAIN KNOWLEDGE: A guest's `displayname` is highly mutable and changes
    frequently based on how they want to present themselves. However, their
    `username` is effectively constant and serves as their unique social handle
    (e.g., @username for tagging on the platform).
    """

    __tablename__ = "guests"  # type: ignore[assignment]

    # Using Mazmo's ID as our PK means we can do idempotent upserts:
    # INSERT ... ON CONFLICT (mazmo_user_id) DO NOTHING
    mazmo_user_id: MazmoUserId = Field(primary_key=True, sa_type=Integer)
    username: str = Field(index=True)
    displayname: str

    # ── Why two relationships? ──
    #
    # We define BOTH `rsvps` and `meetups` because they serve different purposes:
    #
    # 1. `rsvps` - Direct access to MeetupRsvp records
    # 2. `meetups` - Convenience many-to-many, skips the association table

    rsvps: list["MeetupRsvp"] = Relationship(
        back_populates="guest",
        sa_relationship_kwargs={"overlaps": "meetups,guests"},
    )

    meetups: list["Meetup"] = Relationship(
        back_populates="guests",
        link_model=MeetupRsvp,
        sa_relationship_kwargs={"overlaps": "guest,rsvps,meetup"},
    )

    bans: list["GuestBan"] = Relationship(back_populates="guest")


class GuestBan(SQLModel, table=True):
    """
    Per-organization ban record for a Mazmo guest.

    Composite PK (mazmo_user_id, org_id) ensures one ban record per guest
    per organization. A guest banned in Club Vanta is not banned in other orgs.

    The absence of a row means the guest is not banned in that org.
    """

    __tablename__ = "guest_bans"  # type: ignore[assignment]

    mazmo_user_id: MazmoUserId = Field(
        foreign_key="guests.mazmo_user_id",
        primary_key=True,
        sa_type=Integer,
    )
    org_id: int = Field(foreign_key="organizations.id", primary_key=True)
    banned_at: datetime
    banned_by_id: int = Field(foreign_key="users.id")
    banned_reason: str = Field(max_length=500)

    guest: "Guest" = Relationship(back_populates="bans")
    banned_by: Optional["User"] = Relationship()


class Meetup(SQLModel, table=True):
    """
    Represents an event tracked by the door tracker app.

    Linked to a specific Mazmo URL, which the background sync service uses
    to scrape the current RSVP list.

    `org_id` scopes the meetup to a specific organization — staff from other
    orgs cannot see or interact with this meetup.

    Once finalized, no further check-ins or syncs are allowed.
    """

    __tablename__ = "meetups"  # type: ignore[assignment]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # The Mazmo frontend URL, e.g. https://mazmo.net/community/event-name-123
    # Unique constraint prevents creating duplicate meetups for the same event.
    mazmo_meetup_url: str = Field(unique=True)

    name: str = Field(index=True)
    date: datetime
    org_id: int = Field(foreign_key="organizations.id")

    # Finalization — set once the event is over
    is_finalized: bool = Field(default=False)
    finalized_at: datetime | None = Field(default=None)

    # Same dual-relationship pattern as Guest - see comments there for details.
    rsvps: list["MeetupRsvp"] = Relationship(
        back_populates="meetup",
        sa_relationship_kwargs={"overlaps": "guests,meetups"},
    )

    guests: list["Guest"] = Relationship(
        back_populates="meetups",
        link_model=MeetupRsvp,
        sa_relationship_kwargs={"overlaps": "guest,rsvps,meetup"},
    )


# ── Event Log ─────────────────────────────────────────────────────────────────
#
# Audit trail for important actions. Every CHECK_IN, UNDO_CHECK_IN, BAN, and
# UNBAN is recorded here with who did it, when, and why.
#
# ─────────────────────────────────────────────────────────────────────────────


class EventLog(SQLModel, table=True):
    """
    Audit log entry for trackable events.

    Records who performed an action, on which guest, at which meetup (if
    applicable), and when. Used for accountability and investigation.

    `org_id` scopes audit events to an organization. Set from the acting
    user's org at event creation time.
    """

    __tablename__ = "event_log"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(max_length=32, index=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    # Organization this event belongs to (derived from actor at creation time)
    org_id: int | None = Field(default=None, foreign_key="organizations.id")

    # Who performed the action (staff/admin)
    actor_id: int | None = Field(default=None, foreign_key="users.id")

    # Target guest (for guest-related events)
    guest_id: int | None = Field(default=None, foreign_key="guests.mazmo_user_id", index=True)

    # Related meetup (for check-in events)
    meetup_id: uuid.UUID | None = Field(default=None, foreign_key="meetups.id", index=True)

    # Additional context (e.g., ban reason, undo reason)
    reason: str | None = Field(default=None, max_length=500)

    # ── Relationships ──
    actor: Optional["User"] = Relationship()
    guest: Optional["Guest"] = Relationship()
    meetup: Optional["Meetup"] = Relationship()
