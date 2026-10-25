"""
Shared period and scope filters for org statistics.

Every stats tab counts the same meetups: finalized ones of one organization,
whose date falls inside a period measured in Buenos Aires local time. The
conditions live here so a tab that joins Meetup with RSVPs reuses them
instead of rewriting them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col, select

from app.models.models import GuestType, Meetup

STATS_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
QUARTER_HOUR_MINUTES = 15

# Staff and vendors work the event; only these guest types are the audience.
AUDIENCE_GUEST_TYPES: tuple[str, ...] = (GuestType.NORMAL.value, GuestType.INVITED.value)


def ratio(numerator: int, denominator: int, digits: int) -> float | None:
    """numerator / denominator rounded to digits decimals; None when the denominator is zero."""
    return round(numerator / denominator, digits) if denominator > 0 else None


def floor_to_quarter_hour(moment: datetime) -> datetime:
    """The start of the clock quarter hour that moment falls in, in moment's own time zone."""
    return moment.replace(minute=moment.minute // QUARTER_HOUR_MINUTES * QUARTER_HOUR_MINUTES, second=0, microsecond=0)


def as_utc(value: datetime) -> datetime:
    """The test database returns naive datetimes, production timestamptz; both mean UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InvalidStatsPeriodError(ValueError):
    """Raised when a period's start is not strictly before its end."""


def _local_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=STATS_TZ).astimezone(UTC)


@dataclass(frozen=True)
class StatsPeriod:
    """
    A range of local dates, start inclusive and end exclusive. None leaves
    that side open, so StatsPeriod(None, None) means all time.
    """

    start: date | None
    end: date | None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise InvalidStatsPeriodError(
                f"Invalid period: start {self.start} must be before end {self.end}. "
                "Use a start date earlier than the end date."
            )

    def utc_bounds(self) -> tuple[datetime | None, datetime | None]:
        """Local midnight of each date, converted to UTC."""
        return (
            _local_midnight_utc(self.start) if self.start is not None else None,
            _local_midnight_utc(self.end) if self.end is not None else None,
        )

    def previous(self) -> "StatsPeriod | None":
        """The period of the same length that ends where this one starts."""
        if self.start is None or self.end is None:
            return None
        return StatsPeriod(self.start - (self.end - self.start), self.start)


def within(
    column: ColumnElement[datetime] | Mapped[datetime], bounds: tuple[datetime | None, datetime | None]
) -> list[ColumnElement[bool]]:
    """WHERE conditions keeping column in [start, end); a None side is left open."""
    start, end = bounds
    conditions: list[ColumnElement[bool]] = []
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column < end)
    return conditions


def finalized_meetup_conditions(org_id: uuid.UUID, period: StatsPeriod) -> list[ColumnElement[bool]]:
    """WHERE conditions selecting the org's finalized meetups inside the period."""
    return [
        Meetup.org_id == org_id,  # type: ignore[arg-type]
        Meetup.is_finalized.is_(True),  # type: ignore[attr-defined]
        *within(col(Meetup.date), period.utc_bounds()),
    ]


def single_meetup_conditions(org_id: uuid.UUID, meetup_id: uuid.UUID) -> list[ColumnElement[bool]]:
    """WHERE conditions selecting one finalized meetup of the org, whatever its date."""
    return [
        *finalized_meetup_conditions(org_id, StatsPeriod(None, None)),
        Meetup.id == meetup_id,  # type: ignore[arg-type]
    ]


def is_finalized_meetup_of_org(session: Session, org_id: uuid.UUID, meetup_id: uuid.UUID) -> bool:
    """Whether meetup_id is a finalized meetup of the org (any date)."""
    return session.exec(select(Meetup.id).where(*single_meetup_conditions(org_id, meetup_id))).first() is not None
