"""Time helpers shared by services."""

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """A UTC datetime; a naive value (users.created_at is a plain timestamp column) already means UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
