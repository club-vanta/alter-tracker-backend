"""
Meetup-related schemas.

These schemas handle meetup CRUD operations and sync responses.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator, model_validator

from app.models.models import WeatherStatus

# Pattern for validating Mazmo frontend URLs.
# Expected format: https://mazmo.net/{+community}/{thread-slug}-{id}
# Community may start with '+'. Thread ID may be numeric or alphanumeric.
# Examples:
#   https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217
#   https://mazmo.net/+eventos-reuniones-argentina/alter-tal-selmo-secret-face-opgnjcy4d0u
MAZMO_URL_PATTERN = re.compile(r"^https://mazmo\.net/\+?[\w-]+/[\w-]+$")

MAX_LOCATION_LABEL_LENGTH = 255


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
    location_label: str | None
    latitude: float | None
    longitude: float | None


class MeetupLocationUpdate(BaseModel):
    """
    Request body for setting or clearing a meetup's location.

    All three keys are required. Send them all null to clear the location,
    or all set to store it: a label of 1..255 characters (trimmed), a latitude
    in [-90, 90] and a longitude in [-180, 180].
    """

    location_label: str | None
    latitude: float | None
    longitude: float | None

    @model_validator(mode="after")
    def _validate_location(self) -> "MeetupLocationUpdate":
        label, latitude, longitude = self.location_label, self.latitude, self.longitude
        if label is None and latitude is None and longitude is None:
            return self
        if label is None or latitude is None or longitude is None:
            raise ValueError(
                "Cannot set location: location_label, latitude and longitude must be set together. "
                "Send all three, or all null to clear the location."
            )
        label = label.strip()
        if not 1 <= len(label) <= MAX_LOCATION_LABEL_LENGTH:
            raise ValueError(
                f"Cannot set location: location_label must have 1 to {MAX_LOCATION_LABEL_LENGTH} characters "
                f"after trimming, got {len(label)}. Shorten the label or send all three fields as null to clear it."
            )
        if not -90 <= latitude <= 90:
            raise ValueError(f"Cannot set location: latitude must be between -90 and 90, got {latitude}.")
        if not -180 <= longitude <= 180:
            raise ValueError(f"Cannot set location: longitude must be between -180 and 180, got {longitude}.")
        self.location_label = label
        return self


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


class GuestMazmoRefreshResponse(BaseModel):
    """
    Response after refreshing every Mazmo-linked guest from Mazmo.

    total counts guests with a mazmo_user_id. not_found counts the ones
    Mazmo's /users endpoint left out of its response; those keep their
    previous data.
    """

    total: int
    refreshed: int
    not_found: int


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


class MeetupWeatherPublic(BaseModel):
    """
    Weather during the first 6 hours of a meetup, from Open-Meteo.

    Each float summarizes the hours of the window [date, date + 6h). A null
    means the provider had no value for that series anywhere in the window
    (shown as "no data"), never zero:
    - avg_temperature, avg_apparent_temperature: degrees Celsius, mean.
    - total_precipitation_mm: millimetres, sum. Null when unknown.
    - max_wind_kmh: km/h, highest hour.
    - rained: total_precipitation_mm >= 0.2. Null exactly when
      total_precipitation_mm is null: unknown is not the same as dry.

    fetched_at is when the snapshot was taken; source names the provider
    dataset. The raw hourly arrays are stored but not returned.
    """

    model_config = ConfigDict(from_attributes=True)

    avg_temperature: float | None
    avg_apparent_temperature: float | None
    total_precipitation_mm: float | None
    max_wind_kmh: float | None
    rained: bool | None
    fetched_at: datetime
    source: str


class MeetupWeatherStatusPublic(BaseModel):
    """
    Weather of a meetup plus where it stands.

    status is OK (weather is set), NO_LOCATION (no coordinates), PENDING
    (located, but the window is not over plus 2 hours) or NOT_FETCHED (ready,
    no row yet). weather is null unless status is OK. ready is true when the
    meetup has coordinates and its window ended at least 2 hours ago, whether
    or not a row exists.
    """

    status: WeatherStatus
    weather: MeetupWeatherPublic | None
    ready: bool
