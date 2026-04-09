"""
Guest and RSVP-related schemas.

These schemas handle guest identity, RSVP state, and check-in responses.
Guest identity is separate from RSVP state - a guest can RSVP to multiple meetups.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GuestPublic(BaseModel):
    """
    A Mazmo user's identity (cached locally).

    This is identity-only data - no RSVP or check-in state.
    The same guest can appear at multiple meetups with different RSVP states.
    Maps directly to the Guest model via from_attributes.

    Includes is_banned so the frontend can render banned guests differently
    (e.g., name in red).
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str
    is_banned: bool = False


class GuestListResponse(BaseModel):
    """
    Response for listing all known guests.

    Returns identity-only data. For RSVP state at a specific meetup,
    use the /meetups/{id}/guests endpoint instead.
    """

    total: int
    guests: list[GuestPublic]


class RsvpPublic(BaseModel):
    """
    Event-specific RSVP state for a guest at a meetup.

    This data lives in the MeetupRsvp association table, not on the Guest.
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

    Nests the guest identity and their RSVP state separately for clarity.
    Frontend can access guest.displayname vs rsvp.has_arrived.
    """

    guest: GuestPublic
    rsvp: RsvpPublic


class MeetupGuestListResponse(BaseModel):
    """
    Response for listing guests at a specific meetup.

    Each entry includes both the guest's identity and their RSVP state
    for this particular meetup.
    """

    total: int
    guests: list[MeetupGuestPublic]


class CheckedInByPublic(BaseModel):
    """
    Minimal staff representation for check-in attribution.

    Only includes the essential fields needed to identify who performed
    a check-in, without exposing sensitive fields like approval status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class CheckInResponse(BaseModel):
    """
    Response after successfully checking in a guest.

    Confirms the check-in with the guest's identity and their assigned
    arrival order for this meetup. arrival_order is gap-free per meetup.
    Includes the staff member who performed the check-in for audit purposes.
    """

    guest: GuestPublic
    arrival_order: int
    arrival_time: datetime
    checked_in_by: CheckedInByPublic


# ── Manual guest creation ─────────────────────────────────────────────────────


class CreateGuestRequest(BaseModel):
    """
    Request body for creating a guest by Mazmo username.

    Staff only need to know the handle (e.g. "cindydark"). The endpoint looks
    up the canonical ID and profile data from Mazmo automatically.
    """

    username: str = Field(
        min_length=1,
        max_length=255,
        description="Mazmo username to look up (e.g. 'cindydark')",
    )


# ── Ban-related schemas ───────────────────────────────────────────────────────


class BanGuestRequest(BaseModel):
    """Request body for banning a guest."""

    reason: str = Field(min_length=5, max_length=500)


class BannedGuestPublic(BaseModel):
    """
    Extended guest info including ban details for the banned list.

    Used in the banned guests list endpoint where admins need to see
    the full audit trail (when banned, why, by whom).
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str
    is_banned: bool
    banned_at: datetime | None
    banned_reason: str | None
    banned_by_id: int | None


class BannedGuestListResponse(BaseModel):
    """Response for listing all banned guests."""

    total: int
    guests: list[BannedGuestPublic]
