"""
Guest and RSVP-related schemas.

Guest identity is now separate from ban status - bans are per-org.
GuestPublic contains identity only (no is_banned).
GuestWithBanPublic adds org-scoped ban status for endpoints that need it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GuestPublic(BaseModel):
    """
    A Mazmo user's identity (cached locally).

    Identity-only - no RSVP or ban state. Bans are per-org and are
    included only in org-scoped endpoints via GuestWithBanPublic.
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str


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


# ── Manual guest creation ─────────────────────────────────────────────────────


class CreateGuestRequest(BaseModel):
    """Request body for creating a guest by Mazmo username."""

    username: str = Field(
        min_length=1,
        max_length=255,
        description="Mazmo username to look up (e.g. 'cindydark')",
    )


# ── Ban-related schemas ───────────────────────────────────────────────────────


class BanGuestRequest(BaseModel):
    """Request body for banning a guest within an organization."""

    reason: str = Field(min_length=5, max_length=500)


class BannedGuestPublic(BaseModel):
    """
    Guest info with ban details, sourced from organization_bans.

    Used in the org-scoped banned guests list endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str
    banned_at: datetime
    banned_reason: str
    banned_by_id: int | None


class BannedGuestListResponse(BaseModel):
    """Response for listing all banned guests in an organization."""

    total: int
    guests: list[BannedGuestPublic]
