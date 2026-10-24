"""
Attendance statistics: how many people signed up, cancelled, came and
no-showed, per meetup and in total for a period.

Statuses come only from attendance_status_expr (the SQL twin of the rule in
app.services.attendance); the audience is guest types NORMAL and INVITED.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlmodel import Session, col

from app.models.models import GuestAttendanceStatus, Meetup, MeetupRsvp, StatsComparisonKind
from app.services.attendance import attendance_status_expr
from app.services.stats.scope import AUDIENCE_GUEST_TYPES, StatsPeriod, finalized_meetup_conditions

# -- Result shapes --


@dataclass(frozen=True)
class MeetupAttendanceRow:
    """Audience attendance of one finalized meetup. attended includes walkin."""

    meetup_id: uuid.UUID
    name: str
    date: datetime
    signed_up: int
    cancelled: int
    attended: int
    walkin: int
    no_show: int


@dataclass(frozen=True)
class AttendanceTotals:
    """Sums over several meetups. A rate or average with nothing to divide by is None."""

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


@dataclass(frozen=True)
class AttendanceStats:
    meetups: list[MeetupAttendanceRow]
    totals: AttendanceTotals
    comparison_kind: StatsComparisonKind | None
    comparison: AttendanceTotals | None


# -- Queries --


def _count_where(*conditions: ColumnElement[bool]):
    """Count of the joined RSVPs matching the conditions (outer-join NULLs are never counted)."""
    return func.count(MeetupRsvp.guest_id).filter(*conditions)  # type: ignore[arg-type]


def _attendance_rows_query(*conditions: ColumnElement[bool]) -> Select:
    """
    One row per finalized meetup matching the conditions, with audience counts.
    Uses SQLAlchemy's select because sqlmodel's overloads stop at four columns.
    The audience filter sits in the outer join's ON clause so a meetup with no
    audience RSVPs still yields a row of zeros.
    """
    status = attendance_status_expr()
    walkin = GuestAttendanceStatus.WALKIN.value
    return (
        select(
            col(Meetup.id),
            col(Meetup.name),
            col(Meetup.date),
            _count_where(status.is_not(None), status != walkin),
            _count_where(status == GuestAttendanceStatus.CANCELLED.value),
            _count_where(status.in_([GuestAttendanceStatus.ATTENDED.value, walkin])),
            _count_where(status == walkin),
            _count_where(status == GuestAttendanceStatus.NO_SHOW.value),
        )
        .select_from(Meetup)
        .outerjoin(
            MeetupRsvp,
            and_(
                MeetupRsvp.meetup_id == Meetup.id,  # type: ignore[arg-type]
                MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES),  # type: ignore[attr-defined]
            ),
        )
        .where(*conditions)
        .group_by(col(Meetup.id), col(Meetup.name), col(Meetup.date))
        .order_by(Meetup.date, Meetup.id)  # type: ignore[arg-type]
    )


def _run_rows(session: Session, query: Select) -> list[MeetupAttendanceRow]:
    return [
        MeetupAttendanceRow(
            meetup_id=meetup_id,
            name=name,
            date=date,
            signed_up=signed_up,
            cancelled=cancelled,
            attended=attended,
            walkin=walkin,
            no_show=no_show,
        )
        for meetup_id, name, date, signed_up, cancelled, attended, walkin, no_show in session.execute(query).all()
    ]


def meetup_attendance_rows(session: Session, org_id: uuid.UUID, period: StatsPeriod) -> list[MeetupAttendanceRow]:
    """Audience attendance of each finalized meetup in the period, oldest first."""
    return _run_rows(session, _attendance_rows_query(*finalized_meetup_conditions(org_id, period)))


def _single_meetup_rows(session: Session, org_id: uuid.UUID, meetup_id: uuid.UUID) -> list[MeetupAttendanceRow]:
    conditions = [*finalized_meetup_conditions(org_id, StatsPeriod(None, None)), Meetup.id == meetup_id]
    return _run_rows(session, _attendance_rows_query(*conditions))


# -- Totals --


def _ratio(numerator: int, denominator: int, digits: int) -> float | None:
    return round(numerator / denominator, digits) if denominator > 0 else None


def summarize_rows(rows: list[MeetupAttendanceRow]) -> AttendanceTotals:
    """Sum the rows. Rates are rounded to 4 decimals, averages to 1."""
    events = len(rows)
    signed_up = sum(r.signed_up for r in rows)
    cancelled = sum(r.cancelled for r in rows)
    attended = sum(r.attended for r in rows)
    no_show = sum(r.no_show for r in rows)
    return AttendanceTotals(
        events=events,
        signed_up=signed_up,
        cancelled=cancelled,
        attended=attended,
        walkin=sum(r.walkin for r in rows),
        no_show=no_show,
        cancellation_rate=_ratio(cancelled, signed_up, 4),
        no_show_rate=_ratio(no_show, signed_up - cancelled, 4),
        avg_signed_up_per_event=_ratio(signed_up, events, 1),
        avg_attended_per_event=_ratio(attended, events, 1),
    )


# -- Entry point --


def attendance_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> AttendanceStats:
    """
    Attendance for the whole period (compared with the previous period), or for
    one meetup (compared with the whole period). The caller guarantees meetup_id
    is a finalized meetup of the org.
    """
    if meetup_id is not None:
        rows = _single_meetup_rows(session, org_id, meetup_id)
        period_rows = meetup_attendance_rows(session, org_id, period)
        return AttendanceStats(
            meetups=rows,
            totals=summarize_rows(rows),
            comparison_kind=StatsComparisonKind.PERIOD_AVERAGE,
            comparison=summarize_rows(period_rows),
        )

    rows = meetup_attendance_rows(session, org_id, period)
    previous = period.previous()
    return AttendanceStats(
        meetups=rows,
        totals=summarize_rows(rows),
        comparison_kind=StatsComparisonKind.PREVIOUS_PERIOD if previous else None,
        comparison=summarize_rows(meetup_attendance_rows(session, org_id, previous)) if previous else None,
    )
