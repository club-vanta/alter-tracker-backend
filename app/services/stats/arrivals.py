"""
Arrival statistics: when people show up relative to the meetup start, and how
far ahead of the meetup they signed up.

Arrivals are audience RSVPs (NORMAL, INVITED) of finalized meetups that were
checked in with an arrival time, walk-ins included. Lead time covers audience
RSVPs that signed up (walk-ins excluded). Statuses come only from
attendance_status_expr. The curve and summary functions are pure: they take
plain values (offset minutes, and for one event the meetup's local start) so
they can be tested without the database.

Each arrival has two positions: its offset (minutes from its meetup's start)
and its clock minute (minutes from local midnight starting its meetup's night,
so an arrival after midnight is past 1440 and averages correctly). A meetup's
night is the local date of its start, except that a start before noon belongs
to the night before: a meetup at 23:00 and one at 00:30 share one clock. The
offset figures and the clock figures are computed separately: over several
meetups that start at different hours they need not line up.
"""

import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from sqlalchemy import ColumnElement, Select, select
from sqlmodel import Session, col

from app.models.models import (
    GuestAttendanceStatus,
    Meetup,
    MeetupRsvp,
    StatsComparisonKind,
    StatsLeadTimeBucket,
)
from app.services.attendance import attendance_status_expr
from app.services.stats.scope import (
    AUDIENCE_GUEST_TYPES,
    QUARTER_HOUR_MINUTES,
    STATS_TZ,
    StatsPeriod,
    finalized_meetup_conditions,
    floor_to_quarter_hour,
    ratio,
    single_meetup_conditions,
)
from app.services.timeutil import as_utc

BUCKET_MINUTES = QUARTER_HOUR_MINUTES
FIRST_HOUR_MINUTES = 60
# Arrivals outside this window of minutes from the start (late catch-up check-ins, clock errors) are excluded.
MIN_OFFSET_MINUTES = -120
MAX_OFFSET_MINUTES = 720  # exclusive
# A meetup starting before noon belongs to the night before, for clock times.
NIGHT_STARTS_AFTER = timedelta(hours=12)

# Upper day bound (inclusive) of each lead-time bucket; the last one is open.
_LEAD_TIME_LIMITS: tuple[tuple[int, StatsLeadTimeBucket], ...] = (
    (0, StatsLeadTimeBucket.SAME_DAY),
    (1, StatsLeadTimeBucket.ONE_DAY),
    (3, StatsLeadTimeBucket.TWO_THREE),
    (7, StatsLeadTimeBucket.FOUR_SEVEN),
    (14, StatsLeadTimeBucket.EIGHT_FOURTEEN),
)

# -- Result shapes --


@dataclass(frozen=True)
class ArrivalBucket:
    """
    Arrivals in one 15 minute bucket starting start_minute minutes from the
    meetup start (negative = before). local_time is "HH:MM" in one-event mode.
    """

    start_minute: int
    local_time: str | None
    arrivals: int


@dataclass(frozen=True)
class Arrival:
    """One arrival: minutes from its meetup's start, and minutes from local midnight of the meetup's date."""

    offset: int
    clock: int


@dataclass(frozen=True)
class ArrivalSummary:
    """
    *_minutes are offsets from the meetup start; *_local_time are "HH:MM" of
    the local clock. peak is the busiest bucket of the curve and
    peak_local_time the start of the busiest local quarter hour. Means are
    rounded to the minute. first_hour_share counts arrivals before minute 60,
    early ones included. arrivals counts only the arrivals inside the offset
    window; excluded_arrivals counts those outside it.
    """

    arrivals: int
    excluded_arrivals: int
    peak: ArrivalBucket | None
    peak_local_time: str | None
    mean_minutes: int | None
    p90_minutes: int | None
    mean_local_time: str | None
    p90_local_time: str | None
    first_hour_share: float | None


@dataclass(frozen=True)
class LeadTimeRow:
    bucket: StatsLeadTimeBucket
    signed_up: int
    cancelled: int
    no_show: int
    no_show_rate: float | None


@dataclass(frozen=True)
class ArrivalStats:
    curve: list[ArrivalBucket]
    summary: ArrivalSummary
    lead_time: list[LeadTimeRow]
    comparison_kind: StatsComparisonKind | None
    comparison: ArrivalSummary | None


# -- Pure functions --


def _clock(local_start: datetime, minutes: int) -> str:
    return (local_start + timedelta(minutes=minutes)).strftime("%H:%M")


def _clock_bucket_start(local_start: datetime, offset: int) -> int:
    """Offset of the quarter hour of the local clock that the arrival falls in."""
    floored = floor_to_quarter_hour(local_start + timedelta(minutes=offset))
    return (floored - local_start) // timedelta(minutes=1)


def arrival_curve(offsets: list[int], *, local_start: datetime | None = None) -> list[ArrivalBucket]:
    """
    Arrivals per 15 minute bucket, with the empty buckets between the first
    and last arrival so the curve is continuous.

    Without local_start the buckets are floor(offset / 15) * 15 minutes from
    the start. With the meetup's local start (one-event mode) they are
    quarter hours of the local clock; start_minute is the offset of the bucket
    from the start and local_time its "HH:MM". Order is by start_minute, so a
    meetup crossing midnight stays in order.
    """
    if not offsets:
        return []
    if local_start is None:
        counts = Counter(offset // BUCKET_MINUTES * BUCKET_MINUTES for offset in offsets)
    else:
        counts = Counter(_clock_bucket_start(local_start, offset) for offset in offsets)
    return [
        ArrivalBucket(
            start_minute=minute,
            local_time=_clock(local_start, minute) if local_start is not None else None,
            arrivals=counts.get(minute, 0),
        )
        for minute in range(min(counts), max(counts) + 1, BUCKET_MINUTES)
    ]


def _percentile(sorted_values: list[int], p: float) -> int:
    """Nearest rank: the ceil(p * n)-th smallest value."""
    return sorted_values[math.ceil(p * len(sorted_values)) - 1]


def _mean(values: list[int]) -> int:
    """Mean rounded half up to a whole minute."""
    return math.floor(sum(values) / len(values) + 0.5)


def format_clock(minutes: int) -> str:
    """The "HH:MM" of minutes from local midnight; values past a day or before it wrap around."""
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def _busiest_quarter(clocks: list[int]) -> int:
    """Start of the local quarter hour with the most arrivals, the earliest on ties."""
    counts = Counter(clock // BUCKET_MINUTES * BUCKET_MINUTES for clock in clocks)
    return min(counts, key=lambda start: (-counts[start], start))


def summarize_arrivals(arrivals: list[Arrival], curve: list[ArrivalBucket], *, excluded: int = 0) -> ArrivalSummary:
    """Peak bucket (earliest on ties), busiest clock quarter, mean, p90 and first-hour share."""
    if not arrivals:
        return ArrivalSummary(0, excluded, None, None, None, None, None, None, None)
    offsets = sorted(a.offset for a in arrivals)
    clocks = sorted(a.clock for a in arrivals)
    return ArrivalSummary(
        arrivals=len(arrivals),
        excluded_arrivals=excluded,
        peak=max(curve, key=lambda bucket: bucket.arrivals),
        peak_local_time=format_clock(_busiest_quarter(clocks)),
        mean_minutes=_mean(offsets),
        p90_minutes=_percentile(offsets, 0.9),
        mean_local_time=format_clock(_mean(clocks)),
        p90_local_time=format_clock(_percentile(clocks, 0.9)),
        first_hour_share=ratio(sum(1 for o in offsets if o < FIRST_HOUR_MINUTES), len(offsets), 4),
    )


def lead_time_bucket(days: int) -> StatsLeadTimeBucket:
    """Bucket for whole local days between the RSVP and the meetup; negative counts as same day."""
    for limit, bucket in _LEAD_TIME_LIMITS:
        if days <= limit:
            return bucket
    return StatsLeadTimeBucket.FIFTEEN_PLUS


# -- Queries --


@dataclass(frozen=True)
class _RsvpRow:
    """One audience RSVP of a finalized meetup, datetimes as aware UTC."""

    meetup_date: datetime
    rsvp_time: datetime
    arrival_time: datetime | None
    status: GuestAttendanceStatus | None


def _rsvp_query(*conditions: ColumnElement[bool]) -> Select[tuple[datetime, datetime, datetime | None, str | None]]:
    """Uses SQLAlchemy's select because sqlmodel's overloads stop at four columns."""
    return (
        select(
            col(Meetup.date),
            col(MeetupRsvp.rsvp_time),
            col(MeetupRsvp.arrival_time),
            attendance_status_expr(),
        )
        .select_from(MeetupRsvp)
        .join(Meetup, Meetup.id == MeetupRsvp.meetup_id)  # type: ignore[arg-type]
        .where(MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES), *conditions)  # type: ignore[attr-defined]
    )


def _load_rows(session: Session, *conditions: ColumnElement[bool]) -> list[_RsvpRow]:
    """Meetup starts are cut to the minute so offsets, clock buckets and local times agree."""
    return [
        _RsvpRow(
            meetup_date=as_utc(meetup_date).replace(second=0, microsecond=0),
            rsvp_time=as_utc(rsvp_time),
            arrival_time=as_utc(arrival_time) if arrival_time is not None else None,
            status=GuestAttendanceStatus(status) if status is not None else None,
        )
        for meetup_date, rsvp_time, arrival_time, status in session.execute(_rsvp_query(*conditions)).all()
    ]


# -- Analysis --

_ARRIVED = (GuestAttendanceStatus.ATTENDED, GuestAttendanceStatus.WALKIN)


def _clock_minute(meetup_date: datetime, moment: datetime) -> int:
    """Whole minutes from local midnight starting the meetup's night to moment."""
    night = (meetup_date.astimezone(STATS_TZ) - NIGHT_STARTS_AFTER).date()
    return (moment - datetime.combine(night, time.min, tzinfo=STATS_TZ)) // timedelta(minutes=1)


def _arrivals(rows: list[_RsvpRow]) -> tuple[list[Arrival], int]:
    """
    The arrivals inside the offset window (whole minutes, rounded down), and
    the number of arrivals left out for being outside it.
    """
    every = [
        Arrival(
            offset=(row.arrival_time - row.meetup_date) // timedelta(minutes=1),
            clock=_clock_minute(row.meetup_date, row.arrival_time),
        )
        for row in rows
        if row.status in _ARRIVED and row.arrival_time is not None
    ]
    included = [a for a in every if MIN_OFFSET_MINUTES <= a.offset < MAX_OFFSET_MINUTES]
    return included, len(every) - len(included)


def _lead_time_rows(rows: list[_RsvpRow]) -> list[LeadTimeRow]:
    signed_up: Counter[StatsLeadTimeBucket] = Counter()
    cancelled: Counter[StatsLeadTimeBucket] = Counter()
    no_show: Counter[StatsLeadTimeBucket] = Counter()
    for row in rows:
        if row.status is None or row.status == GuestAttendanceStatus.WALKIN:
            continue
        days = (row.meetup_date.astimezone(STATS_TZ).date() - row.rsvp_time.astimezone(STATS_TZ).date()).days
        bucket = lead_time_bucket(days)
        signed_up[bucket] += 1
        if row.status == GuestAttendanceStatus.CANCELLED:
            cancelled[bucket] += 1
        elif row.status == GuestAttendanceStatus.NO_SHOW:
            no_show[bucket] += 1
    return [
        LeadTimeRow(
            bucket=bucket,
            signed_up=signed_up[bucket],
            cancelled=cancelled[bucket],
            no_show=no_show[bucket],
            no_show_rate=ratio(no_show[bucket], signed_up[bucket] - cancelled[bucket], 4),
        )
        for bucket in StatsLeadTimeBucket
    ]


def lead_time_rows(session: Session, *conditions: ColumnElement[bool]) -> list[LeadTimeRow]:
    """Sign-ups, cancellations and no-shows per lead-time bucket over the meetups matching the conditions."""
    return _lead_time_rows(_load_rows(session, *conditions))


def _summary_of(rows: list[_RsvpRow]) -> ArrivalSummary:
    arrivals, excluded = _arrivals(rows)
    return summarize_arrivals(arrivals, arrival_curve([a.offset for a in arrivals]), excluded=excluded)


# -- Entry point --


def arrival_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> ArrivalStats:
    """
    Arrivals for the whole period (compared with the previous period), or for
    one meetup in local clock time (compared with the whole period). The
    caller guarantees meetup_id is a finalized meetup of the org.
    """
    if meetup_id is not None:
        rows = _load_rows(session, *single_meetup_conditions(org_id, meetup_id))
        comparison_rows = _load_rows(session, *finalized_meetup_conditions(org_id, period))
        kind = StatsComparisonKind.PERIOD_AVERAGE
    else:
        rows = _load_rows(session, *finalized_meetup_conditions(org_id, period))
        previous = period.previous()
        comparison_rows = _load_rows(session, *finalized_meetup_conditions(org_id, previous)) if previous else None
        kind = StatsComparisonKind.PREVIOUS_PERIOD if previous else None

    local_start = rows[0].meetup_date.astimezone(STATS_TZ) if meetup_id is not None and rows else None
    arrivals, excluded = _arrivals(rows)
    curve = arrival_curve([a.offset for a in arrivals], local_start=local_start)
    return ArrivalStats(
        curve=curve,
        summary=summarize_arrivals(arrivals, curve, excluded=excluded),
        lead_time=_lead_time_rows(rows),
        comparison_kind=kind,
        comparison=_summary_of(comparison_rows) if comparison_rows is not None else None,
    )
