"""
Meetup-related schemas.

These schemas handle meetup CRUD operations and sync responses.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator

# Pattern for validating Mazmo frontend URLs.
# Expected format: https://mazmo.net/{+community}/{thread-slug}-{id}
# Community may start with '+'. Thread ID may be numeric or alphanumeric.
# Examples:
#   https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217
#   https://mazmo.net/+eventos-reuniones-argentina/alter-tal-selmo-secret-face-opgnjcy4d0u
MAZMO_URL_PATTERN = re.compile(r"^https://mazmo\.net/\+?[\w-]+/[\w-]+$")


class MeetupPublic(BaseModel):
    """
    Public representation of a meetup.

    Returned when fetching meetup details. The mazmo_meetup_url is the
    source URL used for syncing RSVPs from the Mazmo platform.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mazmo_meetup_url: str
    date: datetime
    is_finalized: bool
    finalized_at: datetime | None
    requires_payment: bool


class MeetupListResponse(BaseModel):
    """
    Response for listing all meetups.

    Returns meetups ordered by date descending (most recent first).
    """

    total: int
    meetups: list[MeetupPublic]


class MeetupCreate(BaseModel):
    """
    Request body for creating a new meetup.

    The mazmo_meetup_url is validated to match the expected Mazmo format.
    During creation, the API calls Mazmo to fetch the event date, which
    also verifies the URL points to a real event.
    """

    name: str
    mazmo_meetup_url: HttpUrl
    requires_payment: bool = False

    @field_validator("mazmo_meetup_url")
    @classmethod
    def validate_mazmo_url_format(cls, v: HttpUrl) -> HttpUrl:
        """
        Validate that the URL matches the expected Mazmo frontend format.

        This catches malformed URLs early with a clear error message,
        before we attempt to call the Mazmo API.
        """
        url_str = str(v)
        if not MAZMO_URL_PATTERN.match(url_str):
            raise ValueError("URL must match pattern: https://mazmo.net/{community}/{thread-slug}")
        return v


class SyncResponse(BaseModel):
    """
    Response after syncing guests from Mazmo.

    Reports how many new guests/RSVPs were inserted vs already existed.
    The sync is idempotent - running it multiple times is safe.
    """

    inserted: int
    skipped: int
    total_in_db: int


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
