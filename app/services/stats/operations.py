"""
Door operations statistics: who came in by guest type, how payments went,
who did the check-ins, the busiest quarter hour at the door, and the bans.

Unlike the other tabs this one counts every guest type (NORMAL, INVITED,
STAFF, VENDOR): staff and vendors go through the same door. Attended means
status ATTENDED or WALKIN from attendance_status_expr; cancelled means status
CANCELLED. The paid, pending and exempt split comes only from
payment_standing, the same rule the door card uses.

Bans are event_log rows of type BAN for the org. The ban flow
(PATCH /organizations/{org_id}/guests/{guest_id}/ban) does not record a
meetup_id, so one meetup's bans are the ones logged from its start until
BAN_WINDOW later. For a period they are the ones logged inside the period's
Buenos Aires local dates (StatsPeriod.utc_bounds).
"""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col

from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestAttendanceStatus,
    GuestType,
    Meetup,
    MeetupRsvp,
    StatsComparisonKind,
    User,
)
from app.services.attendance import attendance_status_expr
from app.services.payment import PaymentStanding, payment_standing
from app.services.stats.scope import (
    STATS_TZ,
    StatsPeriod,
    finalized_meetup_conditions,
    floor_to_quarter_hour,
    ratio,
    single_meetup_conditions,
    within,
)
from app.services.timeutil import as_utc

BAN_WINDOW = timedelta(hours=12)
BAN_LIST_LIMIT = 20
# Audience first, then the people working the event (not GuestType's declaration order).
GUEST_TYPE_ORDER: tuple[GuestType, ...] = (GuestType.NORMAL, GuestType.INVITED, GuestType.STAFF, GuestType.VENDOR)

# -- Result shapes --


@dataclass(frozen=True)
class GuestTypeRow:
    guest_type: GuestType
    attended: int
    share: float | None


@dataclass(frozen=True)
class OperationsPaymentStats:
    """
    Attended RSVPs of the paying meetups in scope, split by payment_standing.
    cancelled_but_paid counts cancelled RSVPs that had paid. paid_share is
    paid / (paid + pending).

    Same rule as the door card (GET .../meetups/{id}/stats) but three known
    differences for one meetup: the door card splits every non-cancelled RSVP,
    no-shows included, while this counts attended RSVPs only; the door card
    reports cancelled_but_paid for any meetup, while this only covers meetups
    that require payment; and a checked-in cancellation counts as attended
    (and so as paid) here, by status, while the door card goes by the raw
    cancelled flag.
    """

    paying_events: int
    paid: int
    pending: int
    exempt: int
    cancelled_but_paid: int
    paid_share: float | None


@dataclass(frozen=True)
class StaffCheckins:
    """Check-ins by one staff user. user_id None: no checker was recorded."""

    user_id: int | None
    username: str | None
    checkins: int


@dataclass(frozen=True)
class BusiestSlot:
    """The quarter hour with the most check-ins; local_time is its "HH:MM" start in Buenos Aires."""

    meetup_id: uuid.UUID
    meetup_name: str
    local_time: str
    checkins: int


@dataclass(frozen=True)
class BanRow:
    guest_id: uuid.UUID
    displayname: str
    reason: str | None
    timestamp: datetime
    actor_username: str | None


@dataclass(frozen=True)
class OperationsSummary:
    """avg_peak_checkins is the mean of each meetup's busiest quarter hour, over meetups with check-ins."""

    attended: int
    paid_share: float | None
    avg_peak_checkins: float | None
    bans: int


@dataclass(frozen=True)
class OperationsStats:
    summary: OperationsSummary
    guest_types: list[GuestTypeRow]
    payments: OperationsPaymentStats | None
    staff: list[StaffCheckins]
    busiest: BusiestSlot | None
    ban_list: list[BanRow]
    comparison_kind: StatsComparisonKind | None
    comparison: OperationsSummary | None


@dataclass(frozen=True)
class _MeetupPeak:
    """One meetup's busiest quarter hour, as a local datetime."""

    meetup_id: uuid.UUID
    meetup_name: str
    meetup_date: datetime
    bucket: datetime
    checkins: int


# -- Queries --

_ATTENDED = (GuestAttendanceStatus.ATTENDED.value, GuestAttendanceStatus.WALKIN.value)
type _Column = ColumnElement[Any] | Mapped[Any]
# UTC start (inclusive) and end (exclusive); None leaves that side open.
type _Window = tuple[datetime | None, datetime | None]


def _rsvps(*columns: _Column) -> Select:
    """RSVPs joined to their meetup. Uses SQLAlchemy's select because sqlmodel's overloads stop at four columns."""
    return select(*columns).select_from(MeetupRsvp).join(Meetup, Meetup.id == MeetupRsvp.meetup_id)  # type: ignore[arg-type]


def _guest_types(session: Session, conditions: list[ColumnElement[bool]]) -> list[GuestTypeRow]:
    """Attended per guest type, always all four in GUEST_TYPE_ORDER."""
    guest_type = col(MeetupRsvp.guest_type)
    query = _rsvps(guest_type, func.count()).where(*conditions, attendance_status_expr().in_(_ATTENDED))
    counts: dict[str, int] = dict(session.execute(query.group_by(guest_type)).tuples().all())
    total = sum(counts.values())
    return [GuestTypeRow(t, counts.get(t, 0), ratio(counts.get(t, 0), total, 4)) for t in GUEST_TYPE_ORDER]


def _payments(session: Session, conditions: list[ColumnElement[bool]]) -> OperationsPaymentStats | None:
    """
    Payment split over the paying meetups in scope; None when there are none.
    Counts come grouped by (guest_type, has_paid) so payment_standing, not
    SQL, decides which group is paid, pending or exempt.
    """
    paying = [*conditions, Meetup.requires_payment.is_(True)]  # type: ignore[attr-defined]
    paying_events = session.execute(select(func.count()).select_from(Meetup).where(*paying)).scalar_one()
    if paying_events == 0:
        return None

    status = attendance_status_expr()
    guest_type, has_paid = col(MeetupRsvp.guest_type), col(MeetupRsvp.has_paid)
    query = _rsvps(
        guest_type,
        has_paid,
        func.count().filter(status.in_(_ATTENDED)),
        func.count().filter(status == GuestAttendanceStatus.CANCELLED.value),
    ).where(*paying)
    attended: Counter[PaymentStanding] = Counter()
    cancelled_but_paid = 0
    for row_type, row_paid, row_attended, row_cancelled in session.execute(query.group_by(guest_type, has_paid)):
        attended[payment_standing(row_type, row_paid)] += row_attended
        if row_paid:
            cancelled_but_paid += row_cancelled

    paid, pending = attended[PaymentStanding.PAID], attended[PaymentStanding.PENDING]
    return OperationsPaymentStats(
        paying_events=paying_events,
        paid=paid,
        pending=pending,
        exempt=attended[PaymentStanding.EXEMPT],
        cancelled_but_paid=cancelled_but_paid,
        paid_share=ratio(paid, paid + pending, 4),
    )


def _staff(session: Session, conditions: list[ColumnElement[bool]]) -> list[StaffCheckins]:
    """
    Attended RSVPs per checker, most first, ties by username. The user is
    outer-joined so a missing account still counts (username None), and RSVPs
    with no recorded checker form one row, always last.
    """
    checker, username = col(MeetupRsvp.checked_in_by_id), col(User.username)
    query = (
        _rsvps(checker, username, func.count())
        .outerjoin(User, User.id == MeetupRsvp.checked_in_by_id)  # type: ignore[arg-type]
        .where(*conditions, attendance_status_expr().in_(_ATTENDED))
        .group_by(checker, username)
    )
    rows = [StaffCheckins(user_id, name, checkins) for user_id, name, checkins in session.execute(query)]
    return sorted(rows, key=lambda row: (row.user_id is None, -row.checkins, row.username or ""))


def _meetup_peaks(session: Session, conditions: list[ColumnElement[bool]]) -> list[_MeetupPeak]:
    """
    Each meetup's busiest local quarter hour of check-ins (all guest types),
    the earliest one on ties. Meetups without check-in times are left out.
    """
    meetup_id, name, meetup_date = col(Meetup.id), col(Meetup.name), col(Meetup.date)
    arrival = col(MeetupRsvp.arrival_time)
    query = _rsvps(meetup_id, name, meetup_date, arrival).where(
        *conditions, attendance_status_expr().in_(_ATTENDED), arrival.is_not(None)
    )
    buckets: defaultdict[tuple[uuid.UUID, str, datetime], Counter[datetime]] = defaultdict(Counter)
    for row_id, row_name, row_date, row_arrival in session.execute(query):
        local_bucket = floor_to_quarter_hour(as_utc(row_arrival).astimezone(STATS_TZ))
        buckets[(row_id, row_name, as_utc(row_date))][local_bucket] += 1

    peaks = []
    for (row_id, row_name, row_date), counts in buckets.items():
        bucket = min(counts, key=lambda b: (-counts[b], b))
        peaks.append(_MeetupPeak(row_id, row_name, row_date, bucket, counts[bucket]))
    return peaks


def _busiest(peaks: list[_MeetupPeak]) -> BusiestSlot | None:
    """The busiest quarter hour of all; ties go to the earliest meetup, then the earliest time."""
    if not peaks:
        return None
    top = min(peaks, key=lambda p: (-p.checkins, p.meetup_date, p.bucket))
    return BusiestSlot(top.meetup_id, top.meetup_name, top.bucket.strftime("%H:%M"), top.checkins)


def _ban_select(org_id: uuid.UUID, window: _Window, *columns: _Column) -> Select:
    conditions: list[ColumnElement[bool]] = [
        EventLog.event_type == EventType.BAN.value,  # type: ignore[arg-type]
        EventLog.org_id == org_id,  # type: ignore[arg-type]
        *within(col(EventLog.timestamp), window),
    ]
    return (
        select(*columns)
        .select_from(EventLog)
        .join(Guest, Guest.id == EventLog.guest_id)  # type: ignore[arg-type]
        .where(*conditions)
    )


def _ban_count(session: Session, org_id: uuid.UUID, window: _Window) -> int:
    return session.execute(_ban_select(org_id, window, func.count())).scalar_one()


def _ban_list(session: Session, org_id: uuid.UUID, window: _Window) -> list[BanRow]:
    """The newest BAN_LIST_LIMIT bans. The actor is outer-joined, so a ban without one still shows."""
    query = (
        _ban_select(
            org_id,
            window,
            col(EventLog.guest_id),
            col(Guest.displayname),
            col(EventLog.reason),
            col(EventLog.timestamp),
            col(User.username),
        )
        .outerjoin(User, User.id == EventLog.actor_id)  # type: ignore[arg-type]
        .order_by(col(EventLog.timestamp).desc(), col(EventLog.id).desc())
        .limit(BAN_LIST_LIMIT)
    )
    return [
        BanRow(guest_id, displayname, reason, as_utc(timestamp), actor)
        for guest_id, displayname, reason, timestamp, actor in session.execute(query)
    ]


# -- Summary --


def _summary_of(
    guest_types: list[GuestTypeRow], payments: OperationsPaymentStats | None, peaks: list[_MeetupPeak], bans: int
) -> OperationsSummary:
    return OperationsSummary(
        attended=sum(row.attended for row in guest_types),
        paid_share=payments.paid_share if payments is not None else None,
        avg_peak_checkins=ratio(sum(p.checkins for p in peaks), len(peaks), 1),
        bans=bans,
    )


def _summarize(
    session: Session,
    org_id: uuid.UUID,
    conditions: list[ColumnElement[bool]],
    ban_window: _Window,
) -> OperationsSummary:
    return _summary_of(
        _guest_types(session, conditions),
        _payments(session, conditions),
        _meetup_peaks(session, conditions),
        _ban_count(session, org_id, ban_window),
    )


# -- Entry point --


def operations_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> OperationsStats:
    """
    Door operations for the whole period (compared with the previous period),
    or for one meetup (compared with the whole period). The caller guarantees
    meetup_id is a finalized meetup of the org.
    """
    period_conditions = finalized_meetup_conditions(org_id, period)

    if meetup_id is not None:
        conditions = single_meetup_conditions(org_id, meetup_id)
        start = as_utc(session.execute(select(col(Meetup.date)).where(col(Meetup.id) == meetup_id)).scalar_one())
        ban_window: _Window = (start, start + BAN_WINDOW)
        comparison_kind = StatsComparisonKind.PERIOD_AVERAGE
        comparison = _summarize(session, org_id, period_conditions, period.utc_bounds())
    else:
        conditions = period_conditions
        ban_window = period.utc_bounds()
        previous = period.previous()
        comparison_kind = StatsComparisonKind.PREVIOUS_PERIOD if previous else None
        comparison = (
            _summarize(session, org_id, finalized_meetup_conditions(org_id, previous), previous.utc_bounds())
            if previous
            else None
        )

    guest_types = _guest_types(session, conditions)
    payments = _payments(session, conditions)
    peaks = _meetup_peaks(session, conditions)
    return OperationsStats(
        summary=_summary_of(guest_types, payments, peaks, _ban_count(session, org_id, ban_window)),
        guest_types=guest_types,
        payments=payments,
        staff=_staff(session, conditions),
        busiest=_busiest(peaks),
        ban_list=_ban_list(session, org_id, ban_window),
        comparison_kind=comparison_kind,
        comparison=comparison,
    )
