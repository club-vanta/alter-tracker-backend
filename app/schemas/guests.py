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

from app.models.models import GuestType


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
