"""
Schemas for the org statistics endpoints.

Every field is always present in the response, so none has a default; a rate
or average with nothing to divide by is null.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.models import StatsComparisonKind, StatsLeadTimeBucket

# -- Attendance --


class MeetupAttendanceRowPublic(BaseModel):
    """Audience attendance of one finalized meetup. attended includes walkin."""

    meetup_id: uuid.UUID
    name: str
    date: datetime
    signed_up: int
    cancelled: int
    attended: int
    walkin: int
    no_show: int

    model_config = ConfigDict(from_attributes=True)


class AttendanceTotalsPublic(BaseModel):
    """Sums over several meetups. A rate or average with nothing to divide by is null."""

    events: int
    signed_up: int
    cancelled: int
    attended: int
    walkin: int
    no_show: int
    cancellation_rate: float | None
    no_show_rate: float | None
    avg_signed_up_per_event: float | None
    avg_attended_per_event: float | None

    model_config = ConfigDict(from_attributes=True)


class AttendanceStatsPublic(BaseModel):
    """Attendance per meetup and in total, with the figures to compare against."""

    meetups: list[MeetupAttendanceRowPublic]
    totals: AttendanceTotalsPublic
    comparison_kind: StatsComparisonKind | None
    comparison: AttendanceTotalsPublic | None

    model_config = ConfigDict(from_attributes=True)


# -- Arrivals --


class ArrivalBucketPublic(BaseModel):
    """
    Arrivals in one 15 minute bucket starting start_minute minutes from the
    meetup start (negative = before). local_time is "HH:MM" for one meetup, null otherwise.
    """

    start_minute: int
    local_time: str | None
    arrivals: int

    model_config = ConfigDict(from_attributes=True)


class ArrivalSummaryPublic(BaseModel):
    """
    Offsets are minutes from the meetup start. The *_local_time fields are
    "HH:MM" only for one meetup's own summary. Everything but arrivals is null
    when nobody arrived.
    """

    arrivals: int
    peak: ArrivalBucketPublic | None
    median_minutes: int | None
    p90_minutes: int | None
    median_local_time: str | None
    p90_local_time: str | None
    first_hour_share: float | None

    model_config = ConfigDict(from_attributes=True)


class LeadTimeRowPublic(BaseModel):
    """Audience sign-ups made in one lead-time bucket. no_show_rate is null with nobody to divide by."""

    bucket: StatsLeadTimeBucket
    signed_up: int
    cancelled: int
    no_show: int
    no_show_rate: float | None

    model_config = ConfigDict(from_attributes=True)


class ArrivalStatsPublic(BaseModel):
    """When people arrive and how far ahead they signed up, with the figures to compare against."""

    curve: list[ArrivalBucketPublic]
    summary: ArrivalSummaryPublic
    lead_time: list[LeadTimeRowPublic]
    comparison_kind: StatsComparisonKind | None
    comparison: ArrivalSummaryPublic | None

    model_config = ConfigDict(from_attributes=True)
