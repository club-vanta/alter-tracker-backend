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

from app.models.models import GuestAttendanceStatus, GuestType
from app.schemas.events import EventActorPublic


def _strip_leading_at(value: str | None) -> str | None:
    """Normalizes an Instagram handle so '@user' and 'user' store the same way."""
    if value is None:
        return None
    return value.removeprefix("@")


class GuestMazmoProfilePublic(BaseModel):
    """
    Snapshot of extended Mazmo profile data for a linked, synced guest.

    mazmo_suspended/mazmo_banned are Mazmo's own account-level flags -
    unrelated to this app's own ban system (OrganizationBan / the
    is_banned field on GuestWithBanPublic). Prefixed even here to avoid
    that confusion once this is flattened into JSON. mazmo_deleted means
    the account no longer exists on Mazmo (its mazmo.net link 404s).
    """

    model_config = ConfigDict(from_attributes=True)

    avatar_url: str | None
    age: int | None
    gender: str | None
    pronoun: str | None
    mazmo_suspended: bool
    mazmo_banned: bool
    mazmo_deleted: bool
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


class GuestTypeUpdateRequest(BaseModel):
    """Request body for changing a guest's category at a specific meetup."""

    guest_type: GuestType


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

    Used in the org-scoped banned guests list endpoint. mazmo_profile
    follows the same rules as GuestPublic.mazmo_profile.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
    instagram_username: str | None
    mazmo_profile: GuestMazmoProfilePublic | None = None
    banned_at: datetime
    banned_reason: str
    banned_by_id: int | None


class BannedGuestListResponse(BaseModel):
    """Response for listing all banned guests in an organization."""

    total: int
    guests: list[BannedGuestPublic]


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


# -- Meetup attendance -------------------------------------------------------------


class GuestMeetupAttendancePublic(BaseModel):
    """
    One meetup of the org that a guest signed up for or walked into, with
    its attendance outcome (see app.services.attendance).

    rsvp_time for a WALKIN is when staff added them at the door, not a
    sign-up.
    """

    meetup_id: uuid.UUID
    name: str
    date: datetime
    status: GuestAttendanceStatus
    rsvp_time: datetime
    arrival_time: datetime | None = None
    guest_type: str = "NORMAL"


class GuestAttendanceSummary(BaseModel):
    """
    Counts of a guest's meetups in one org by status.

    attended includes walk-ins; walkin is the subset of attended who came
    without signing up. attended + no_show + cancelled + upcoming == total.
    """

    attended: int
    walkin: int
    no_show: int
    cancelled: int
    upcoming: int


class GuestMeetupAttendanceListResponse(BaseModel):
    """
    Every meetup of one org a guest has an RSVP row for, newest first.

    Not paginated: a guest attends at most a few dozen meetups of one org.
    """

    total: int
    summary: GuestAttendanceSummary
    meetups: list[GuestMeetupAttendancePublic]
