"""
Schemas for the org statistics endpoints.

Every field is always present in the response, so none has a default; a rate
or average with nothing to divide by is null.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.models import (
    GuestType,
    StatsAgeBucket,
    StatsComparisonKind,
    StatsFrequencyBucket,
    StatsLeadTimeBucket,
    StatsTemperatureBand,
)
from app.schemas.meetups import MeetupWeatherStatusPublic

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
    "HH:MM" only for one meetup's own summary. arrivals counts the arrivals
    from 120 minutes before to 720 minutes after the start; excluded_arrivals
    counts those outside that window, which no other field includes. The
    optional fields are null when nobody arrived inside the window.
    """

    arrivals: int
    excluded_arrivals: int
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


# -- Demographics --


class GenderGroupPublic(BaseModel):
    """
    One gender group of audience RSVPs. key is a Mazmo gender code (null:
    unknown). Rates and shares are null with nothing to divide by.
    """

    key: str | None
    attended: int
    expected: int
    no_show: int
    no_show_rate: float | None
    attended_share: float | None
    expected_share: float | None

    model_config = ConfigDict(from_attributes=True)


class AgeGroupPublic(BaseModel):
    """
    One age group of audience RSVPs, keyed by a StatsAgeBucket. Rates and
    shares are null with nothing to divide by.
    """

    key: StatsAgeBucket
    attended: int
    expected: int
    no_show: int
    no_show_rate: float | None
    attended_share: float | None
    expected_share: float | None

    model_config = ConfigDict(from_attributes=True)


class DemographicSummaryPublic(BaseModel):
    """The figures compared between two scopes. median_age and known_age_share are null when nobody attended."""

    attended: int
    median_age: float | None
    known_age_share: float | None

    model_config = ConfigDict(from_attributes=True)


class DemographicStatsPublic(BaseModel):
    """Gender and age of the people who attend, with the figures to compare against. One RSVP counts once."""

    gender: list[GenderGroupPublic]
    age: list[AgeGroupPublic]
    summary: DemographicSummaryPublic
    comparison_kind: StatsComparisonKind | None
    comparison: DemographicSummaryPublic | None

    model_config = ConfigDict(from_attributes=True)


# -- Retention --


class MeetupNewReturningPublic(BaseModel):
    """Unique attendees of one finalized meetup: new ones had their first time there."""

    meetup_id: uuid.UUID
    name: str
    date: datetime
    new: int
    returning: int

    model_config = ConfigDict(from_attributes=True)


class CohortRowPublic(BaseModel):
    """
    Guests whose first time was in month ("YYYY-MM", Buenos Aires). retention[i] is the
    share of them who attended in month i + 1 after it. Months that have not happened
    yet are left out, so the list can be shorter than 12.
    """

    month: str
    size: int
    retention: list[float]

    model_config = ConfigDict(from_attributes=True)


class FrequencyRowPublic(BaseModel):
    """Unique guests with a number of attendances in the period, in one StatsFrequencyBucket."""

    bucket: StatsFrequencyBucket
    guests: int

    model_config = ConfigDict(from_attributes=True)


class LapsedRegularPublic(BaseModel):
    """A guest with several attendances before the period and none in it."""

    guest_id: uuid.UUID
    displayname: str
    attended_before: int
    last_attended: datetime

    model_config = ConfigDict(from_attributes=True)


class RetentionSummaryPublic(BaseModel):
    """
    Unique guests in scope. new_share and first_timer_return_rate are null with nothing to
    divide by; the return rate counts later attendances even after the period ends.
    """

    attendees: int
    new_guests: int
    returning: int
    new_share: float | None
    first_timer_return_rate: float | None

    model_config = ConfigDict(from_attributes=True)


class RetentionStatsPublic(BaseModel):
    """
    New and returning guests, cohorts, frequency and lapsed regulars, with the figures to
    compare against. For one meetup the lists are empty and lapsed_total is null; for a
    period without a start, lapsed is empty and lapsed_total is null.
    """

    summary: RetentionSummaryPublic
    meetups: list[MeetupNewReturningPublic]
    cohorts: list[CohortRowPublic]
    frequency: list[FrequencyRowPublic]
    lapsed: list[LapsedRegularPublic]
    lapsed_total: int | None
    comparison_kind: StatsComparisonKind | None
    comparison: RetentionSummaryPublic | None

    model_config = ConfigDict(from_attributes=True)


# -- Operations --


class GuestTypeRowPublic(BaseModel):
    """Attended RSVPs of one guest type; share is of all attended, null when nobody attended."""

    guest_type: GuestType
    attended: int
    share: float | None

    model_config = ConfigDict(from_attributes=True)


class OperationsPaymentStatsPublic(BaseModel):
    """
    Attended RSVPs of the meetups that require payment. Only NORMAL guests pay; every other
    type is exempt. paid_share is paid / (paid + pending), null when nobody owes entry.
    """

    paying_events: int
    paid: int
    pending: int
    exempt: int
    cancelled_but_paid: int
    paid_share: float | None

    model_config = ConfigDict(from_attributes=True)


class StaffCheckinsPublic(BaseModel):
    """Check-ins by one staff user. user_id and username are null for check-ins with no recorded checker."""

    user_id: int | None
    username: str | None
    checkins: int

    model_config = ConfigDict(from_attributes=True)


class BusiestSlotPublic(BaseModel):
    """The quarter hour with the most check-ins; local_time is its "HH:MM" start in Buenos Aires."""

    meetup_id: uuid.UUID
    meetup_name: str
    local_time: str
    checkins: int

    model_config = ConfigDict(from_attributes=True)


class BanRowPublic(BaseModel):
    """A ban logged in scope. reason and actor_username are null when not recorded or the actor was deleted."""

    guest_id: uuid.UUID
    displayname: str
    reason: str | None
    timestamp: datetime
    actor_username: str | None

    model_config = ConfigDict(from_attributes=True)


class OperationsSummaryPublic(BaseModel):
    """The figures compared between two scopes. avg_peak_checkins is the mean of each meetup's busiest quarter hour."""

    attended: int
    paid_share: float | None
    avg_peak_checkins: float | None
    bans: int

    model_config = ConfigDict(from_attributes=True)


class OperationsStatsPublic(BaseModel):
    """
    Door operations over every guest type, with the figures to compare against. payments is
    null when no meetup in scope requires payment; ban_list holds the newest 20 of summary.bans.
    """

    summary: OperationsSummaryPublic
    guest_types: list[GuestTypeRowPublic]
    payments: OperationsPaymentStatsPublic | None
    staff: list[StaffCheckinsPublic]
    busiest: BusiestSlotPublic | None
    ban_list: list[BanRowPublic]
    comparison_kind: StatsComparisonKind | None
    comparison: OperationsSummaryPublic | None

    model_config = ConfigDict(from_attributes=True)


# -- Weather --


class WeatherMeetupRowPublic(BaseModel):
    """
    One finalized meetup with its attendance and weather. The weather fields are
    null without a value: all three when the meetup has no weather row, and
    rained and total_precipitation_mm when the provider had no precipitation data.
    """

    meetup_id: uuid.UUID
    name: str
    date: datetime
    attended: int
    no_show_rate: float | None
    avg_temperature: float | None
    rained: bool | None
    total_precipitation_mm: float | None

    model_config = ConfigDict(from_attributes=True)


class WeatherGroupPublic(BaseModel):
    """
    Meetups that share a weather condition. avg_attended is the mean attended per
    event; no_show_rate is pooled over the group (all no-shows over all expected
    guests, not an average of rates). Both are null for a group with no events.
    """

    events: int
    avg_attended: float | None
    no_show_rate: float | None

    model_config = ConfigDict(from_attributes=True)


class TemperatureBandRowPublic(BaseModel):
    """Meetups whose average temperature falls in one band; figures as in WeatherGroupPublic."""

    band: StatsTemperatureBand
    events: int
    avg_attended: float | None
    no_show_rate: float | None

    model_config = ConfigDict(from_attributes=True)


class WeatherEventPublic(MeetupWeatherStatusPublic):
    """The weather standing of one meetup (as GET .../meetups/{id}/weather) plus its attendance."""

    attended: int
    no_show_rate: float | None

    model_config = ConfigDict(from_attributes=True)


class WeatherStatsPublic(BaseModel):
    """
    How weather relates to attendance, over finalized meetups.

    Aggregate mode (no meetup_id): event is null. meetups lists every meetup in
    the period by date ascending. rainy is rained true and dry is rained false;
    a meetup whose weather row has no precipitation data (rained null) joins
    neither. temperature_bands always has all five bands in order, lower bound
    inclusive, over avg_temperature; a meetup whose row has no temperature joins
    none. without_weather counts only the meetups with no weather row at all;
    a row with a null value is not counted there.

    One-event mode (meetup_id): event is set; meetups, temperature_bands are
    empty, rainy and dry are null and without_weather is 0.

    The tab compares groups of events against each other instead of against
    another period, so comparison_kind and comparison are always null.
    """

    event: WeatherEventPublic | None
    meetups: list[WeatherMeetupRowPublic]
    rainy: WeatherGroupPublic | None
    dry: WeatherGroupPublic | None
    temperature_bands: list[TemperatureBandRowPublic]
    without_weather: int
    comparison_kind: None
    comparison: None

    model_config = ConfigDict(from_attributes=True)
