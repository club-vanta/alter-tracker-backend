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
from sqlmodel import Session, select

from app.models.models import GuestType, Meetup

STATS_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Staff and vendors work the event; only these guest types are the audience.
AUDIENCE_GUEST_TYPES: tuple[str, ...] = (GuestType.NORMAL.value, GuestType.INVITED.value)


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


def finalized_meetup_conditions(org_id: uuid.UUID, period: StatsPeriod) -> list[ColumnElement[bool]]:
    """WHERE conditions selecting the org's finalized meetups inside the period."""
    start, end = period.utc_bounds()
    conditions: list[ColumnElement[bool]] = [
        Meetup.org_id == org_id,  # type: ignore[arg-type]
        Meetup.is_finalized.is_(True),  # type: ignore[attr-defined]
    ]
    if start is not None:
        conditions.append(Meetup.date >= start)  # type: ignore[arg-type]
    if end is not None:
        conditions.append(Meetup.date < end)  # type: ignore[arg-type]
    return conditions


def is_finalized_meetup_of_org(session: Session, org_id: uuid.UUID, meetup_id: uuid.UUID) -> bool:
    """Whether meetup_id is a finalized meetup of the org (any date)."""
    conditions = [*finalized_meetup_conditions(org_id, StatsPeriod(None, None)), Meetup.id == meetup_id]  # type: ignore[arg-type]
    return session.exec(select(Meetup.id).where(*conditions)).first() is not None
