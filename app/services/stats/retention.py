"""
Retention statistics: who comes for the first time, who comes back, and how
often.

An attendance is an audience RSVP (NORMAL, INVITED) with status ATTENDED or
WALKIN, from attendance_status_expr, on a finalized meetup of the org. Every
metric counts unique guests. A guest's first time is their earliest
attendance in the org over all time, so it never depends on the selected
period: the attendances of all time are one CTE, first and last dates come
from window functions over it, and the period or the one meetup is a filter
on top.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import CTE, ColumnElement, func, select
from sqlmodel import Session, col

from app.models.models import (
    Guest,
    GuestAttendanceStatus,
    Meetup,
    MeetupRsvp,
    StatsComparisonKind,
    StatsFrequencyBucket,
)
from app.services.attendance import attendance_status_expr
from app.services.stats.scope import (
    AUDIENCE_GUEST_TYPES,
    STATS_TZ,
    StatsPeriod,
    as_utc,
    finalized_meetup_conditions,
    ratio,
)

COHORT_MONTHS = 12
LAPSED_MIN_ATTENDANCES = 3
LAPSED_LIMIT = 50

# Upper attendance bound (inclusive) of each frequency bucket; the last one is open.
_FREQUENCY_LIMITS: tuple[tuple[int, StatsFrequencyBucket], ...] = (
    (1, StatsFrequencyBucket.ONE),
    (2, StatsFrequencyBucket.TWO),
    (5, StatsFrequencyBucket.THREE_FIVE),
)

# -- Result shapes --


@dataclass(frozen=True)
class MeetupNewReturning:
    """Attendees of one finalized meetup: new ones had their first time there."""

    meetup_id: uuid.UUID
    name: str
    date: datetime
    new: int
    returning: int


@dataclass(frozen=True)
class CohortRow:
    """
    Guests whose first time was in month ("YYYY-MM", local). retention[i] is
    the share of them who attended in month i + 1 after it; months after the
    current one are left out, so the list can be shorter than 12.
    """

    month: str
    size: int
    retention: list[float]


@dataclass(frozen=True)
class FrequencyRow:
    bucket: StatsFrequencyBucket
    guests: int


@dataclass(frozen=True)
class LapsedRegular:
    """A guest with several attendances before the period and none in it."""

    guest_id: uuid.UUID
    displayname: str
    attended_before: int
    last_attended: datetime


@dataclass(frozen=True)
class RetentionSummary:
    """
    Unique guests in scope. first_timer_return_rate is the share of the new
    guests who attended again later, even after the period ends.
    """

    attendees: int
    new_guests: int
    returning: int
    new_share: float | None
    first_timer_return_rate: float | None


@dataclass(frozen=True)
class RetentionStats:
    """In one-event mode the lists are empty and lapsed_total is None."""

    summary: RetentionSummary
    meetups: list[MeetupNewReturning]
    cohorts: list[CohortRow]
    frequency: list[FrequencyRow]
    lapsed: list[LapsedRegular]
    lapsed_total: int | None
    comparison_kind: StatsComparisonKind | None
    comparison: RetentionSummary | None


def frequency_bucket(attendances: int) -> StatsFrequencyBucket:
    """Bucket of a guest's attendance count in the period (at least 1)."""
    for limit, bucket in _FREQUENCY_LIMITS:
        if attendances <= limit:
            return bucket
    return StatsFrequencyBucket.SIX_PLUS


# -- Attendances --


def _attendances(org_id: uuid.UUID) -> CTE:
    """
    Every audience attendance of the org's finalized meetups of all time, with
    the guest's first and last attendance date. One RSVP per guest per meetup,
    so date == first_date marks exactly the guest's first time.
    """
    return (
        select(
            col(MeetupRsvp.guest_id).label("guest_id"),
            col(Meetup.id).label("meetup_id"),
            col(Meetup.date).label("date"),
            func.min(Meetup.date).over(partition_by=col(MeetupRsvp.guest_id)).label("first_date"),
            func.max(Meetup.date).over(partition_by=col(MeetupRsvp.guest_id)).label("last_date"),
        )
        .select_from(Meetup)
        .join(MeetupRsvp, MeetupRsvp.meetup_id == Meetup.id)  # type: ignore[arg-type]
        .where(
            *finalized_meetup_conditions(org_id, StatsPeriod(None, None)),
            MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES),  # type: ignore[attr-defined]
            attendance_status_expr().in_([GuestAttendanceStatus.ATTENDED.value, GuestAttendanceStatus.WALKIN.value]),
        )
        .cte("attendances")
    )


def _in_period(column: ColumnElement[datetime], period: StatsPeriod) -> list[ColumnElement[bool]]:
    """Conditions keeping column inside the period's local dates."""
    start, end = period.utc_bounds()
    conditions: list[ColumnElement[bool]] = []
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column < end)
    return conditions


def _scope(att: CTE, period: StatsPeriod, meetup_id: uuid.UUID | None) -> list[ColumnElement[bool]]:
    """Attendances of the one meetup, or else of the period."""
    if meetup_id is not None:
        return [att.c.meetup_id == meetup_id]
    return _in_period(att.c.date, period)


# -- Summary --


def _summary(session: Session, att: CTE, scope: list[ColumnElement[bool]]) -> RetentionSummary:
    first_time = att.c.date == att.c.first_date
    attendees, new_guests, came_back = session.execute(
        select(
            func.count(att.c.guest_id.distinct()),
            func.count(att.c.guest_id.distinct()).filter(first_time),
            func.count(att.c.guest_id.distinct()).filter(first_time, att.c.last_date > att.c.first_date),
        ).where(*scope)
    ).one()
    return RetentionSummary(
        attendees=attendees,
        new_guests=new_guests,
        returning=attendees - new_guests,
        new_share=ratio(new_guests, attendees, 4),
        first_timer_return_rate=ratio(came_back, new_guests, 4),
    )


# -- Aggregate breakdowns --


def _meetups(session: Session, att: CTE, org_id: uuid.UUID, period: StatsPeriod) -> list[MeetupNewReturning]:
    """Every finalized meetup in the period, oldest first, including those nobody attended."""
    query = (
        select(
            col(Meetup.id),
            col(Meetup.name),
            col(Meetup.date),
            func.count(att.c.guest_id).filter(att.c.date == att.c.first_date),
            func.count(att.c.guest_id).filter(att.c.date > att.c.first_date),
        )
        .select_from(Meetup)
        .outerjoin(att, att.c.meetup_id == Meetup.id)
        .where(*finalized_meetup_conditions(org_id, period))
        .group_by(col(Meetup.id), col(Meetup.name), col(Meetup.date))
        .order_by(Meetup.date, Meetup.id)  # type: ignore[arg-type]
    )
    return [
        MeetupNewReturning(meetup_id=meetup_id, name=name, date=as_utc(when), new=new, returning=returning)
        for meetup_id, name, when, new, returning in session.execute(query).all()
    ]


def _month_index(day: datetime | date) -> int:
    return day.year * 12 + day.month - 1


def _cohorts(session: Session, att: CTE, period: StatsPeriod, today: date) -> list[CohortRow]:
    """
    Local months are worked out in Python: the test database stores naive
    UTC timestamps and production timestamptz, so SQL time zone conversion
    would differ between them. The rows are the attendances of the guests
    whose first time is in the period.
    """
    rows = session.execute(
        select(att.c.guest_id, att.c.first_date, att.c.date).where(*_in_period(att.c.first_date, period))
    ).all()
    members: defaultdict[int, set[uuid.UUID]] = defaultdict(set)
    returned: defaultdict[tuple[int, int], set[uuid.UUID]] = defaultdict(set)
    for guest_id, first_date, when in rows:
        cohort = _month_index(as_utc(first_date).astimezone(STATS_TZ))
        offset = _month_index(as_utc(when).astimezone(STATS_TZ)) - cohort
        members[cohort].add(guest_id)
        if 1 <= offset <= COHORT_MONTHS:
            returned[cohort, offset].add(guest_id)

    current = _month_index(today)
    return [
        CohortRow(
            month=f"{cohort // 12:04d}-{cohort % 12 + 1:02d}",
            size=len(guests),
            retention=[
                round(len(returned[cohort, offset]) / len(guests), 4)
                for offset in range(1, min(COHORT_MONTHS, current - cohort) + 1)
            ],
        )
        for cohort, guests in sorted(members.items())
    ]


def _frequency(session: Session, att: CTE, period: StatsPeriod) -> list[FrequencyRow]:
    per_guest = (
        select(func.count().label("times"))
        .select_from(att)
        .where(*_in_period(att.c.date, period))
        .group_by(att.c.guest_id)
        .subquery()
    )
    guests: defaultdict[StatsFrequencyBucket, int] = defaultdict(int)
    for times, count in session.execute(select(per_guest.c.times, func.count()).group_by(per_guest.c.times)).all():
        guests[frequency_bucket(times)] += count
    return [FrequencyRow(bucket=bucket, guests=guests[bucket]) for bucket in StatsFrequencyBucket]


def _lapsed(session: Session, att: CTE, period: StatsPeriod) -> tuple[list[LapsedRegular], int]:
    """
    Guests with LAPSED_MIN_ATTENDANCES or more attendances before the period
    start and none in the period: the first LAPSED_LIMIT and the full count.
    The caller guarantees the period has a start.
    """
    start, _ = period.utc_bounds()
    before = att.c.date < start
    attended_before = func.count().filter(before)
    last_attended = func.max(att.c.date).filter(before)
    query = (
        select(att.c.guest_id, col(Guest.displayname), attended_before, last_attended, func.count().over())
        .select_from(att)
        .join(Guest, Guest.id == att.c.guest_id)  # type: ignore[arg-type]
        .group_by(att.c.guest_id, col(Guest.displayname))
        .having(attended_before >= LAPSED_MIN_ATTENDANCES, func.count().filter(*_in_period(att.c.date, period)) == 0)
        .order_by(attended_before.desc(), last_attended.desc(), att.c.guest_id)
        .limit(LAPSED_LIMIT)
    )
    rows = session.execute(query).all()
    lapsed = [
        LapsedRegular(guest_id=guest_id, displayname=name, attended_before=times, last_attended=as_utc(last))
        for guest_id, name, times, last, _ in rows
    ]
    return lapsed, rows[0][4] if rows else 0


# -- Entry point --


def retention_stats(
    session: Session,
    org_id: uuid.UUID,
    period: StatsPeriod,
    meetup_id: uuid.UUID | None,
    *,
    today: date | None = None,
) -> RetentionStats:
    """
    Retention for the whole period (compared with the previous period), or for
    one meetup (compared with the whole period; no breakdowns). today bounds
    the cohort months and defaults to the current Buenos Aires date. The
    caller guarantees meetup_id is a finalized meetup of the org.
    """
    att = _attendances(org_id)

    if meetup_id is not None:
        return RetentionStats(
            summary=_summary(session, att, _scope(att, period, meetup_id)),
            meetups=[],
            cohorts=[],
            frequency=[],
            lapsed=[],
            lapsed_total=None,
            comparison_kind=StatsComparisonKind.PERIOD_AVERAGE,
            comparison=_summary(session, att, _scope(att, period, None)),
        )

    previous = period.previous()
    lapsed, lapsed_total = _lapsed(session, att, period) if period.start is not None else ([], None)
    return RetentionStats(
        summary=_summary(session, att, _scope(att, period, None)),
        meetups=_meetups(session, att, org_id, period),
        cohorts=_cohorts(session, att, period, today or datetime.now(STATS_TZ).date()),
        frequency=_frequency(session, att, period),
        lapsed=lapsed,
        lapsed_total=lapsed_total,
        comparison_kind=StatsComparisonKind.PREVIOUS_PERIOD if previous else None,
        comparison=_summary(session, att, _scope(att, previous, None)) if previous else None,
    )
