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
