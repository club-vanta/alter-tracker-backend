"""Time helpers shared by services."""

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """The test database returns naive datetimes, production timestamptz; both mean UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
