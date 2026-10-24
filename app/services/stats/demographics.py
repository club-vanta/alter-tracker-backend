"""
Demographic statistics: gender and age of the people who attend, from their
Mazmo profile.

The unit is one audience RSVP (an attendance), not a unique guest: someone who
came to five meetups counts five times. A guest with no profile, or a profile
without the value, falls in the "unknown" group (gender None, age bucket
UNKNOWN) so group totals always equal the attended total. Statuses come only
from attendance_status_expr.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlmodel import Session, col

from app.models.models import (
    GuestAttendanceStatus,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
    StatsAgeBucket,
    StatsComparisonKind,
)
from app.services.attendance import attendance_status_expr
from app.services.stats.scope import AUDIENCE_GUEST_TYPES, StatsPeriod, finalized_meetup_conditions, ratio

# Mazmo allows fake ages (3, 126); outside this range an age is treated as unknown.
MIN_PLAUSIBLE_AGE = 18
MAX_PLAUSIBLE_AGE = 99

# Upper bound (exclusive) of each bucket, in enum order. The last known bucket is open-ended.
_AGE_BUCKET_LIMITS: tuple[tuple[int, StatsAgeBucket], ...] = (
    (25, StatsAgeBucket.AGE_18_24),
    (30, StatsAgeBucket.AGE_25_29),
    (35, StatsAgeBucket.AGE_30_34),
    (40, StatsAgeBucket.AGE_35_39),
    (50, StatsAgeBucket.AGE_40_49),
)

# -- Result shapes --


@dataclass(frozen=True)
class DemographicGroup:
    """One gender or age group. key is a Mazmo gender code (None: unknown) or a StatsAgeBucket value."""

    key: str | None
    attended: int
    expected: int
    no_show: int
    no_show_rate: float | None
    attended_share: float | None
    expected_share: float | None


@dataclass(frozen=True)
class GroupShare:
    key: str | None
    attended_share: float | None


@dataclass(frozen=True)
class DemographicSummary:
    attended: int
    median_age: float | None
    known_age_share: float | None
    gender_shares: list[GroupShare]
    age_shares: list[GroupShare]


@dataclass(frozen=True)
class DemographicStats:
    gender: list[DemographicGroup]
    age: list[DemographicGroup]
    summary: DemographicSummary
    comparison_kind: StatsComparisonKind | None
    comparison: DemographicSummary | None


@dataclass(frozen=True)
class _Counts:
    attended: int = 0
    expected: int = 0
    no_show: int = 0


# -- Age buckets --


def age_bucket(age: int | None) -> StatsAgeBucket:
    """Bucket of an age; None and implausible ages are UNKNOWN. Twin of age_bucket_expr."""
    if age is None or not MIN_PLAUSIBLE_AGE <= age <= MAX_PLAUSIBLE_AGE:
        return StatsAgeBucket.UNKNOWN
    for limit, bucket in _AGE_BUCKET_LIMITS:
        if age < limit:
            return bucket
    return StatsAgeBucket.AGE_50_PLUS


def age_bucket_expr(age: Any) -> ColumnElement[str]:
    """
    SQL twin of age_bucket(): a CASE returning StatsAgeBucket values.

    WHY: grouping happens in the database so thousands of RSVPs are never
    loaded. test_age_bucket_sql_matches_python keeps the two forms in step.
    """
    return case(
        (age.is_(None), StatsAgeBucket.UNKNOWN.value),
        (~age.between(MIN_PLAUSIBLE_AGE, MAX_PLAUSIBLE_AGE), StatsAgeBucket.UNKNOWN.value),
        *((age < limit, bucket.value) for limit, bucket in _AGE_BUCKET_LIMITS),
        else_=StatsAgeBucket.AGE_50_PLUS.value,
    )


# -- Queries --


def _gender_expr() -> ColumnElement[str | None]:
    """Stored gender, with blank and missing values both NULL."""
    return func.nullif(func.trim(GuestMazmoProfile.gender), "")


def _counts_query(*conditions: ColumnElement[bool]) -> Select:
    """
    Audience RSVP counts per (gender, age bucket). Uses SQLAlchemy's select
    because sqlmodel's overloads stop at four columns. The profile is
    outer-joined so guests without one are still counted.
    """
    status = attendance_status_expr()
    walkin = GuestAttendanceStatus.WALKIN.value
    gender = _gender_expr()
    bucket = age_bucket_expr(GuestMazmoProfile.age)
    return (
        select(
            gender,
            bucket,
            func.count().filter(status.in_([GuestAttendanceStatus.ATTENDED.value, walkin])),
            func.count().filter(status.is_not(None), status != walkin, status != GuestAttendanceStatus.CANCELLED.value),
            func.count().filter(status == GuestAttendanceStatus.NO_SHOW.value),
        )
        .select_from(Meetup)
        .join(
            MeetupRsvp,
            and_(
                MeetupRsvp.meetup_id == Meetup.id,  # type: ignore[arg-type]
                MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES),  # type: ignore[attr-defined]
            ),
        )
        .outerjoin(GuestMazmoProfile, GuestMazmoProfile.guest_id == MeetupRsvp.guest_id)  # type: ignore[arg-type]
        .where(*conditions)
        .group_by(gender, bucket)
    )


def _median_age_query(*conditions: ColumnElement[bool]) -> Select:
    """Median age of attended RSVPs with a plausible age (NULL when there are none)."""
    status = attendance_status_expr()
    return (
        select(func.percentile_cont(0.5).within_group(col(GuestMazmoProfile.age)))
        .select_from(Meetup)
        .join(
            MeetupRsvp,
            and_(
                MeetupRsvp.meetup_id == Meetup.id,  # type: ignore[arg-type]
                MeetupRsvp.guest_type.in_(AUDIENCE_GUEST_TYPES),  # type: ignore[attr-defined]
            ),
        )
        .join(GuestMazmoProfile, GuestMazmoProfile.guest_id == MeetupRsvp.guest_id)  # type: ignore[arg-type]
        .where(
            *conditions,
            status.in_([GuestAttendanceStatus.ATTENDED.value, GuestAttendanceStatus.WALKIN.value]),
            GuestMazmoProfile.age.between(MIN_PLAUSIBLE_AGE, MAX_PLAUSIBLE_AGE),  # type: ignore[union-attr]
        )
    )


# -- Grouping --


@dataclass(frozen=True)
class _Scope:
    """Raw grouped counts and median for one set of meetups."""

    by_gender: dict[str | None, _Counts]
    by_age: dict[str, _Counts]
    median_age: float | None


def _load_scope(session: Session, *conditions: ColumnElement[bool]) -> _Scope:
    gender_totals: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    age_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for gender, bucket, attended, expected, no_show in session.execute(_counts_query(*conditions)).all():
        for totals in (gender_totals[gender], age_totals[bucket]):
            totals[0] += attended
            totals[1] += expected
            totals[2] += no_show
    median = session.execute(_median_age_query(*conditions)).scalar_one()
    return _Scope(
        by_gender={key: _Counts(*totals) for key, totals in gender_totals.items()},
        by_age={key: _Counts(*totals) for key, totals in age_totals.items()},
        median_age=round(float(median), 1) if median is not None else None,
    )


def _groups(counts: dict[str | None, _Counts]) -> list[DemographicGroup]:
    total_attended = sum(c.attended for c in counts.values())
    total_expected = sum(c.expected for c in counts.values())
    return [
        DemographicGroup(
            key=key,
            attended=c.attended,
            expected=c.expected,
            no_show=c.no_show,
            no_show_rate=ratio(c.no_show, c.expected, 4),
            attended_share=ratio(c.attended, total_attended, 4),
            expected_share=ratio(c.expected, total_expected, 4),
        )
        for key, c in counts.items()
    ]


def _gender_groups(scope: _Scope) -> list[DemographicGroup]:
    """Largest first, ties by code, unknown (None) last."""
    groups = _groups(scope.by_gender)
    return sorted(groups, key=lambda g: (g.key is None, -g.attended, g.key or ""))


def _age_groups(scope: _Scope) -> list[DemographicGroup]:
    """All buckets in enum order, empty ones as zeros."""
    return _groups({b.value: scope.by_age.get(b.value, _Counts()) for b in StatsAgeBucket})  # type: ignore[arg-type]


def _summarize(scope: _Scope) -> DemographicSummary:
    age = _age_groups(scope)
    attended = sum(g.attended for g in age)
    known = sum(g.attended for g in age if g.key != StatsAgeBucket.UNKNOWN.value)
    return DemographicSummary(
        attended=attended,
        median_age=scope.median_age,
        known_age_share=ratio(known, attended, 4),
        gender_shares=[GroupShare(g.key, g.attended_share) for g in _gender_groups(scope)],
        age_shares=[GroupShare(g.key, g.attended_share) for g in age],
    )


# -- Entry point --


def demographic_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> DemographicStats:
    """
    Demographics for the whole period (compared with the previous period), or
    for one meetup (compared with the whole period). The caller guarantees
    meetup_id is a finalized meetup of the org.
    """
    period_conditions = finalized_meetup_conditions(org_id, period)

    if meetup_id is not None:
        scope = _load_scope(
            session, *finalized_meetup_conditions(org_id, StatsPeriod(None, None)), col(Meetup.id) == meetup_id
        )  # type: ignore[arg-type]
        comparison_kind = StatsComparisonKind.PERIOD_AVERAGE
        comparison = _summarize(_load_scope(session, *period_conditions))
    else:
        scope = _load_scope(session, *period_conditions)
        previous = period.previous()
        comparison_kind = StatsComparisonKind.PREVIOUS_PERIOD if previous else None
        comparison = (
            _summarize(_load_scope(session, *finalized_meetup_conditions(org_id, previous))) if previous else None
        )

    return DemographicStats(
        gender=_gender_groups(scope),
        age=_age_groups(scope),
        summary=_summarize(scope),
        comparison_kind=comparison_kind,
        comparison=comparison,
    )
