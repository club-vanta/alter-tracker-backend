"""
Event log schemas for audit trail API.

These schemas handle event log queries with filtering, pagination, and
proper authorization context. Events track actions like check-ins, bans,
and their reversals.

Authorization summary:
  - Staff: Can view events at meetups, ban events for guests, own actions
  - Admin: Can view all events with full filtering capabilities
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import EventType

# Built from the EventType enum so this never goes stale as new event types are added.
_EVENT_TYPE_FILTER_DESCRIPTION = f"Comma-separated event types to filter ({', '.join(e.value for e in EventType)})"


class EventTypeFilter(StrEnum):
    """
    Event types that can be filtered in queries.

    Maps directly to EventType in models but defined separately for
    schema validation to avoid circular imports.
    """

    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"
    BAN = "BAN"
    UNBAN = "UNBAN"


# ── Nested objects for event responses ────────────────────────────────────────


class EventActorPublic(BaseModel):
    """
    Minimal staff representation in event logs.

    Shows who performed an action without exposing sensitive account details.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class EventGuestPublic(BaseModel):
    """
    Minimal guest representation in event logs.

    Provides enough info to identify the guest without full profile data.
    """

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str


# ── Event log response ────────────────────────────────────────────────────────


class EventLogPublic(BaseModel):
    """
    Single event log entry for API responses.

    Contains the full context of what happened: who did what to whom,
    when, and why. Relationships are expanded for convenience.

    Fields:
      - id: Unique event identifier
      - event_type: What happened (see EventType in app/models/models.py for the full list)
      - timestamp: When it happened (UTC)
      - actor: Staff member who performed the action
      - guest: Guest who was affected (if applicable)
      - meetup_id: Related meetup UUID (for check-in events)
      - reason: Why the action was taken (required for bans, optional for undos)
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    timestamp: datetime
    actor: EventActorPublic | None = None
    guest: EventGuestPublic | None = None
    meetup_id: str | None = None  # UUID as string for JSON serialization
    reason: str | None = None


class EventLogListResponse(BaseModel):
    """
    Paginated list of event log entries.

    Pagination fields:
      - total: Total number of events matching the filter (for UI pagination)
      - limit: Maximum events per page (default 50, max 100)
      - offset: Number of events skipped (for page calculation)

    Example: Page 2 with 50 per page → offset=50, limit=50
    """

    total: int
    limit: int
    offset: int
    events: list[EventLogPublic]


# ── Query parameters ──────────────────────────────────────────────────────────


class EventLogQuery(BaseModel):
    """
    Query parameters for filtering event logs.

    All filters are optional and combine with AND logic.
    Multiple event types can be specified as comma-separated values.

    Examples:
      - ?type=CHECK_IN,UNDO_CHECK_IN → check-in activity
      - ?type=BAN,UNBAN → ban audit trail
      - ?since=2024-03-23T00:00:00Z → events after date
      - ?guest_id=123&meetup_id=abc → specific guest at specific meetup

    Pagination:
      - limit: Events per page (default 50, max 100)
      - offset: Skip N events (default 0)
    """

    type: str | None = Field(
        default=None,
        description=_EVENT_TYPE_FILTER_DESCRIPTION,
    )
    since: datetime | None = Field(
        default=None,
        description="Only events after this timestamp (ISO 8601 format)",
    )
    until: datetime | None = Field(
        default=None,
        description="Only events before this timestamp (ISO 8601 format)",
    )
    guest_id: int | None = Field(
        default=None,
        description="Filter by guest's mazmo_user_id",
    )
    meetup_id: str | None = Field(
        default=None,
        description="Filter by meetup UUID",
    )
    actor_id: int | None = Field(
        default=None,
        description="Filter by staff member who performed the action",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum events to return (1-100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of events to skip for pagination",
    )
