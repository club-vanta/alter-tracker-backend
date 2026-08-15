import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Column, DateTime, Integer
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass

from app.domain_types import MazmoUserId

# ── Global role enum (Python validation only, not a Postgres type) ────────────


class PossibleRoles(StrEnum):
    """
    Global roles stored in user_roles / users.role_id.

    USER:       Regular approved account. Actual capabilities come from
                user_organizations.role (per-org STAFF or ADMIN).
    SITE_ADMIN: Superadmin. Bypasses all org membership checks, can create
                and manage organizations.
    """

    USER = "USER"
    SITE_ADMIN = "SITE_ADMIN"


class OrgRole(StrEnum):
    """
    Per-organization roles stored as VARCHAR in user_organizations.role.
    These are independent of the global PossibleRoles.
    """

    STAFF = "STAFF"
    ADMIN = "ADMIN"


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
    GUEST_DISPLAYNAME_CHANGED = "GUEST_DISPLAYNAME_CHANGED"
    PAYMENT_RECORDED = "PAYMENT_RECORDED"
    PAYMENT_REVOKED = "PAYMENT_REVOKED"
    PAYMENT_REQUIREMENT_ENABLED = "PAYMENT_REQUIREMENT_ENABLED"
    PAYMENT_REQUIREMENT_DISABLED = "PAYMENT_REQUIREMENT_DISABLED"
    GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"


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


# ── Role lookup table ─────────────────────────────────────────────────────────


class Role(SQLModel, table=True):
    """
    Lookup table for global staff roles (USER, SITE_ADMIN).
    Seeded by migrations. users.role_id is a FK to this table.
    """

    __tablename__ = "user_roles"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, max_length=32)

    users: list["User"] = Relationship(back_populates="role")


# ── User table ────────────────────────────────────────────────────────────────


class User(SQLModel, table=True):
    """
    Staff/admin account.

    New registrations start with is_approved = False.
    Global role is USER or SITE_ADMIN. Per-org capabilities (STAFF/ADMIN)
    live in user_organizations.

    Soft-delete: disabled users cannot log in but their audit trail is preserved.
    """

    __tablename__ = "users"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    hashed_password: str
    is_approved: bool = Field(default=False)
    role_id: int = Field(foreign_key="user_roles.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    is_disabled: bool = Field(default=False)
    disabled_at: datetime | None = Field(default=None)
    disabled_by_id: int | None = Field(default=None, foreign_key="users.id")
    disabled_reason: str | None = Field(default=None, max_length=500)

    # ── Password recovery ──
    recovery_code: str | None = Field(default=None, max_length=6)
    recovery_code_created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    recovery_code_used: bool = Field(default=False)

    role: Role | None = Relationship(back_populates="users")

    disabled_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"remote_side": "User.id", "foreign_keys": "[User.disabled_by_id]"}
    )

    org_memberships: list["UserOrganization"] = Relationship(back_populates="user")


# ── Organization ──────────────────────────────────────────────────────────────


class Organization(SQLModel, table=True):
    """
    An organization scopes meetups, events, and bans.

    Each org has its own guest list (via meetup RSVPs), its own ban list,
    and its own event log. Users access orgs via user_organizations with
    a per-org role (STAFF or ADMIN).

    Guests themselves are global - the same guest identity can appear in
    multiple organizations.
    """

    __tablename__ = "organizations"  # type: ignore[assignment]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True, max_length=128)
    slug: str = Field(unique=True, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by_id: int | None = Field(default=None, foreign_key="users.id")

    memberships: list["UserOrganization"] = Relationship(back_populates="org")
    meetups: list["Meetup"] = Relationship(back_populates="org")
    bans: list["OrganizationBan"] = Relationship(back_populates="org")


# ── UserOrganization (per-org membership + role) ──────────────────────────────


class UserOrganization(SQLModel, table=True):
    """
    Membership of a user in an organization, with a per-org role.

    role is OrgRole.STAFF or OrgRole.ADMIN (stored as VARCHAR).
    A user can be ADMIN in one org and STAFF in another.
    """

    __tablename__ = "user_organizations"  # type: ignore[assignment]

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", primary_key=True)
    role: str = Field(max_length=32)

    user: User = Relationship(back_populates="org_memberships")
    org: Organization = Relationship(back_populates="memberships")


# ── Guests, meetups and RSVPs ─────────────────────────────────────────────────
#
# These three models form a many-to-many relationship:
#
#   Guest <---- MeetupRsvp ----> Meetup
#
# MeetupRsvp is the association table but it also stores RSVP-specific data.
#
# ─────────────────────────────────────────────────────────────────────────────


class MeetupRsvp(SQLModel, table=True):
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

    __tablename__ = "meetup_rsvps"  # type: ignore[assignment]

    meetup_id: uuid.UUID = Field(foreign_key="meetups.id", primary_key=True)
    guest_id: uuid.UUID = Field(foreign_key="guests.id", primary_key=True)

    rsvp_time: datetime
    cancelled_rsvp: bool = Field(default=False)

    has_arrived: bool = Field(default=False, index=True)
    arrival_time: datetime | None = None
    arrival_order: int | None = Field(default=None)

    is_walkin: bool = Field(default=False)

    checked_in_by_id: int | None = Field(default=None, foreign_key="users.id")

    # Payment tracking - only relevant when the parent meetup.requires_payment
    # is True. Set manually by an org admin, never by Mazmo sync.
    has_paid: bool = Field(default=False, index=True)
    paid_at: datetime | None = None
    paid_by_id: int | None = Field(default=None, foreign_key="users.id")

    # Guest category for this specific meetup - only relevant for the
    # payment gate. Set manually by an org admin, never by Mazmo sync.
    guest_type: str = Field(default=GuestType.NORMAL.value, max_length=16, index=True)

    guest: "Guest" = Relationship(back_populates="rsvps")
    meetup: "Meetup" = Relationship(back_populates="rsvps")
    # Two FKs to users.id on this table now (checked_in_by_id, paid_by_id),
    # so each relationship must explicitly pick its own FK column or
    # SQLAlchemy can't tell them apart.
    checked_in_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[MeetupRsvp.checked_in_by_id]"}
    )
    paid_by: Optional["User"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[MeetupRsvp.paid_by_id]"})


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
    displayname_history: list["GuestDisplaynameHistory"] = Relationship(back_populates="guest")
    mazmo_profile: Optional["GuestMazmoProfile"] = Relationship(back_populates="guest")


class Meetup(SQLModel, table=True):
    """
    An event tracked by the door tracker app, scoped to an organization.

    Linked to a specific Mazmo URL for syncing RSVPs.
    Once finalized, no further check-ins or syncs are allowed.
    """

    __tablename__ = "meetups"  # type: ignore[assignment]

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    mazmo_meetup_url: str = Field(unique=True)
    name: str = Field(index=True)
    date: datetime

    is_finalized: bool = Field(default=False)
    finalized_at: datetime | None = Field(default=None)

    requires_payment: bool = Field(default=False)

    org: Organization = Relationship(back_populates="meetups")

    rsvps: list["MeetupRsvp"] = Relationship(
        back_populates="meetup",
        sa_relationship_kwargs={"overlaps": "guests,meetups"},
    )

    guests: list["Guest"] = Relationship(
        back_populates="meetups",
        link_model=MeetupRsvp,
        sa_relationship_kwargs={"overlaps": "guest,rsvps,meetup"},
    )


# ── OrganizationBan ───────────────────────────────────────────────────────────


class OrganizationBan(SQLModel, table=True):
    """
    Records an active ban of a guest within a specific organization.

    One row = one active ban. Unban deletes the row (history is in event_log).
    Unique constraint on (org_id, guest_id) ensures one active ban per guest per org.
    """

    __tablename__ = "organization_bans"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    guest_id: uuid.UUID = Field(foreign_key="guests.id", index=True)
    banned_by_id: int | None = Field(default=None, foreign_key="users.id")
    banned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = Field(max_length=500)

    org: Organization = Relationship(back_populates="bans")
    guest: Guest = Relationship(back_populates="org_bans")
    banned_by: Optional["User"] = Relationship()


# ── Guest Displayname History ────────────────────────────────────────────────────


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


class EventLog(SQLModel, table=True):
    """
    Audit log entry for trackable events, scoped to an organization.

    org_id is NULL only for global events: GUEST_CREATED, GUEST_MAZMO_LINKED,
    GUEST_MAZMO_UNLINKED, and GUEST_DISPLAYNAME_CHANGED. All other event
    types have an org_id.
    """

    __tablename__ = "event_log"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(max_length=32, index=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    org_id: uuid.UUID | None = Field(default=None, foreign_key="organizations.id", index=True)
    actor_id: int | None = Field(default=None, foreign_key="users.id")
    guest_id: uuid.UUID | None = Field(default=None, foreign_key="guests.id", index=True)
    meetup_id: uuid.UUID | None = Field(default=None, foreign_key="meetups.id", index=True)
    reason: str | None = Field(default=None, max_length=500)

    org: Optional["Organization"] = Relationship()
    actor: Optional["User"] = Relationship()
    guest: Optional["Guest"] = Relationship()
    meetup: Optional["Meetup"] = Relationship()
