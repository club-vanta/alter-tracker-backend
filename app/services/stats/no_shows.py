"""
No-show statistics: who signs up for a meetup and does not come, and what
those RSVPs have in common.

A no-show is an audience RSVP (NORMAL, INVITED) of a finalized meetup whose
status, from attendance_status_expr, is NO_SHOW: signed up, not cancelled,
never checked in. The expected RSVPs are the ones that could have been a
no-show: ATTENDED plus NO_SHOW (walk-ins never signed up; cancellations
withdrew). Every rate is pooled: sum(no_show) / sum(expected), never an
average of rates.

The breakdowns reuse the other stats modules so each rule lives in one
place: attendance rows for the per-meetup figures, demographic_counts for
gender and age, lead_time_rows for how far ahead people signed up, and the
retention attendances for whether a guest had come before.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, Subquery, case, func, select
from sqlmodel import Session, col

from app.models.models import (
    Guest,
    GuestAttendanceStatus,
    Meetup,
    MeetupRsvp,
    StatsAgeBucket,
    StatsComparisonKind,
    StatsVisitHistory,
)
from app.services.attendance import EXPECTED_STATUSES, attendance_status_expr
from app.services.stats.arrivals import LeadTimeRow, lead_time_rows
from app.services.stats.attendance import (
    MeetupAttendanceRow,
    meetup_attendance_rows,
    single_meetup_attendance_rows,
    summarize_rows,
)
from app.services.stats.demographics import GroupCounts, demographic_counts
from app.services.stats.retention import attendances_cte
from app.services.stats.scope import (
    AUDIENCE_GUEST_TYPES,
    StatsPeriod,
    finalized_meetup_conditions,
    ratio,
    single_meetup_conditions,
)
from app.services.timeutil import as_utc

# A guest who missed at least this many meetups in the period counts as a repeat no-show.
REPEAT_MIN_NO_SHOWS = 2
GUEST_LIMIT = 50

# -- Result shapes --


@dataclass(frozen=True)
class NoShowSummary:
    """
    Expected RSVPs and no-shows in scope. repeat_guests counts the guests with
    REPEAT_MIN_NO_SHOWS or more no-shows: in the period, or for one meetup,
    those of its no-shows who reach it over all time.
    """

    expected: int
    no_show: int
    no_show_rate: float | None
    repeat_guests: int


@dataclass(frozen=True)
class NoShowMeetupRow:
    meetup_id: uuid.UUID
    name: str
    date: datetime
    expected: int
    no_show: int
    no_show_rate: float | None


@dataclass(frozen=True)
class NoShowGenderGroup:
    """key is a Mazmo gender code (None: unknown)."""

    key: str | None
    expected: int
    no_show: int
    no_show_rate: float | None


@dataclass(frozen=True)
class NoShowAgeGroup:
    key: StatsAgeBucket
    expected: int
    no_show: int
    no_show_rate: float | None


@dataclass(frozen=True)
class NoShowHistoryGroup:
    key: StatsVisitHistory
    expected: int
    no_show: int
    no_show_rate: float | None


@dataclass(frozen=True)
class NoShowGuest:
    """A guest's expected RSVPs, attendances and no-shows, and the date of their last no-show."""

    guest_id: uuid.UUID
    displayname: str
    expected: int
    attended: int
    no_show: int
    last_no_show: datetime


@dataclass(frozen=True)
class NoShowStats:
    """
    Period mode: meetups lists every finalized meetup of the period and guests
    the repeat no-shows of the period. One-meetup mode: meetups is empty and
    guests lists everyone who missed that meetup, counted over all time.
    guests holds the first GUEST_LIMIT; guests_total counts them all.
    """

    summary: NoShowSummary
    meetups: list[NoShowMeetupRow]
    gender: list[NoShowGenderGroup]
    age: list[NoShowAgeGroup]
    history: list[NoShowHistoryGroup]
    lead_time: list[LeadTimeRow]
    guests: list[NoShowGuest]
    guests_total: int
    comparison_kind: StatsComparisonKind | None
    comparison: NoShowSummary | None


# -- Grouping --


def _rate(counts: GroupCounts) -> float | None:
    return ratio(counts.no_show, counts.expected, 4)


def _meetup_rows(rows: list[MeetupAttendanceRow]) -> list[NoShowMeetupRow]:
    return [
        NoShowMeetupRow(
            meetup_id=row.meetup_id,
            name=row.name,
            date=as_utc(row.date),
            expected=row.signed_up - row.cancelled,
            no_show=row.no_show,
            no_show_rate=summarize_rows([row]).no_show_rate,
        )
        for row in rows
    ]


def _gender_groups(by_gender: dict[str | None, GroupCounts]) -> list[NoShowGenderGroup]:
    """Most expected first, ties by code, unknown (None) last. Groups nobody was expected in are dropped."""
    groups = [
        NoShowGenderGroup(key=key, expected=c.expected, no_show=c.no_show, no_show_rate=_rate(c))
        for key, c in by_gender.items()
        if c.expected
    ]
    return sorted(groups, key=lambda g: (g.key is None, -g.expected, g.key or ""))


def _age_groups(by_age: dict[StatsAgeBucket, GroupCounts]) -> list[NoShowAgeGroup]:
    """Every bucket in enum order, empty ones as zeros."""
    return [
        NoShowAgeGroup(key=bucket, expected=c.expected, no_show=c.no_show, no_show_rate=_rate(c))
        for bucket, c in by_age.items()
    ]


# -- Queries --


def _audience_rsvps(query: Select, *conditions: ColumnElement[bool]) -> Select:
    """The query over the expected audience RSVPs of the meetups matching the conditions."""
    return (
        query.select_from(MeetupRsvp)
        .join(Meetup, Meetup.id == MeetupRsvp.meetup_id)  # type: ignore[arg-type]
        .where(
            *conditions,
            MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES),  # type: ignore[attr-defined]
            attendance_status_expr().in_(EXPECTED_STATUSES),
        )
    )


def _history_groups(session: Session, org_id: uuid.UUID, *conditions: ColumnElement[bool]) -> list[NoShowHistoryGroup]:
    """
    Expected RSVPs split by whether the guest had attended an earlier
    finalized meetup of the org (strictly earlier start), any date.
    """
    att = attendances_cte(org_id)
    first = select(att.c.guest_id, att.c.first_date).where(att.c.is_first).subquery()
    key = case(
        (first.c.first_date < Meetup.date, StatsVisitHistory.RETURNING.value),
        else_=StatsVisitHistory.FIRST_TIME.value,
    )
    no_show = attendance_status_expr() == GuestAttendanceStatus.NO_SHOW.value
    query = _audience_rsvps(select(key, func.count(), func.count().filter(no_show)), *conditions).outerjoin(
        first,
        first.c.guest_id == MeetupRsvp.guest_id,  # type: ignore[arg-type]
    )
    counts = {k: GroupCounts(expected=e, no_show=n) for k, e, n in session.execute(query.group_by(key)).all()}
    return [
        NoShowHistoryGroup(key=k, expected=c.expected, no_show=c.no_show, no_show_rate=_rate(c))
        for k in StatsVisitHistory
        for c in [counts.get(k.value, GroupCounts())]
    ]


def _per_guest(org_id: uuid.UUID, period: StatsPeriod, missed: uuid.UUID | None = None) -> Subquery:
    """
    One row per guest with expected RSVPs in the period: expected, attended,
    no_show and last_no_show. With missed, only the guests who were a
    no-show at that meetup.
    """
    status = attendance_status_expr()
    is_no_show = status == GuestAttendanceStatus.NO_SHOW.value
    query = _audience_rsvps(
        select(
            col(MeetupRsvp.guest_id).label("guest_id"),
            func.count().label("expected"),
            func.count().filter(status == GuestAttendanceStatus.ATTENDED.value).label("attended"),
            func.count().filter(is_no_show).label("no_show"),
            func.max(Meetup.date).filter(is_no_show).label("last_no_show"),
        ),
        *finalized_meetup_conditions(org_id, period),
    ).group_by(col(MeetupRsvp.guest_id))
    if missed is not None:
        no_shows_there = _audience_rsvps(select(col(MeetupRsvp.guest_id)), Meetup.id == missed).where(is_no_show)  # type: ignore[arg-type]
        query = query.where(col(MeetupRsvp.guest_id).in_(no_shows_there))
    return query.subquery()


def _repeat_count(session: Session, per_guest: Subquery) -> int:
    return session.execute(
        select(func.count()).select_from(per_guest).where(per_guest.c.no_show >= REPEAT_MIN_NO_SHOWS)
    ).scalar_one()


def _guests(session: Session, per_guest: Subquery, min_no_shows: int) -> tuple[list[NoShowGuest], int]:
    """The guests with min_no_shows or more, most no-shows first: the first GUEST_LIMIT and the full count."""
    query = (
        select(
            per_guest,
            col(Guest.displayname).label("displayname"),
            func.count().over().label("total"),
        )
        .join(Guest, Guest.id == per_guest.c.guest_id)  # type: ignore[arg-type]
        .where(per_guest.c.no_show >= min_no_shows)
        .order_by(per_guest.c.no_show.desc(), per_guest.c.last_no_show.desc(), per_guest.c.guest_id)
        .limit(GUEST_LIMIT)
    )
    rows = session.execute(query).all()
    guests = [
        NoShowGuest(
            guest_id=row.guest_id,
            displayname=row.displayname,
            expected=row.expected,
            attended=row.attended,
            no_show=row.no_show,
            last_no_show=as_utc(row.last_no_show),
        )
        for row in rows
    ]
    return guests, rows[0].total if rows else 0


def _summary(rows: list[MeetupAttendanceRow], repeat_guests: int) -> NoShowSummary:
    totals = summarize_rows(rows)
    return NoShowSummary(
        expected=totals.signed_up - totals.cancelled,
        no_show=totals.no_show,
        no_show_rate=totals.no_show_rate,
        repeat_guests=repeat_guests,
    )


def _period_summary(session: Session, org_id: uuid.UUID, period: StatsPeriod) -> NoShowSummary:
    return _summary(
        meetup_attendance_rows(session, org_id, period),
        _repeat_count(session, _per_guest(org_id, period)),
    )


# -- Entry point --


def no_show_stats(session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None) -> NoShowStats:
    """
    No-shows for the whole period (compared with the previous period), or for
    one meetup (compared with the whole period). The caller guarantees
    meetup_id is a finalized meetup of the org.
    """
    if meetup_id is not None:
        conditions = single_meetup_conditions(org_id, meetup_id)
        missed = _per_guest(org_id, StatsPeriod(None, None), missed=meetup_id)
        guests, guests_total = _guests(session, missed, 1)
        summary = _summary(single_meetup_attendance_rows(session, org_id, meetup_id), _repeat_count(session, missed))
        meetups: list[NoShowMeetupRow] = []
        comparison_kind: StatsComparisonKind | None = StatsComparisonKind.PERIOD_AVERAGE
        comparison: NoShowSummary | None = _period_summary(session, org_id, period)
    else:
        conditions = finalized_meetup_conditions(org_id, period)
        rows = meetup_attendance_rows(session, org_id, period)
        guests, guests_total = _guests(session, _per_guest(org_id, period), REPEAT_MIN_NO_SHOWS)
        summary = _summary(rows, guests_total)
        meetups = _meetup_rows(rows)
        previous = period.previous()
        comparison_kind = StatsComparisonKind.PREVIOUS_PERIOD if previous else None
        comparison = _period_summary(session, org_id, previous) if previous else None

    counts = demographic_counts(session, *conditions)
    return NoShowStats(
        summary=summary,
        meetups=meetups,
        gender=_gender_groups(counts.by_gender),
        age=_age_groups(counts.by_age),
        history=_history_groups(session, org_id, *conditions),
        lead_time=lead_time_rows(session, *conditions),
        guests=guests,
        guests_total=guests_total,
        comparison_kind=comparison_kind,
        comparison=comparison,
    )
