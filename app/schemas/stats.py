"""
Schemas for the org statistics endpoints.

Every field is always present in the response, so none has a default; a rate
or average with nothing to divide by is null.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.models import StatsComparisonKind

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
