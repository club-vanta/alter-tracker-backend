"""
Events router - audit trail API for tracking actions.

GET /api/events                    → All events (admin only)
GET /api/events/meetups/{id}       → Events at a specific meetup (staff+)
GET /api/events/guests/{id}        → Events for a specific guest (staff: bans only, admin: all)
GET /api/events/staff/{id}         → Events by a staff member (staff: own only, admin: any)

Authorization:
  - Staff can view:
    - All events at any meetup (they work meetups)
    - Ban/unban events for any guest (need to know who's banned)
    - Their own actions only
  - Admins can view:
    - Everything, with full filtering

All endpoints support filtering by:
  - type: Event type(s) comma-separated (CHECK_IN, UNDO_CHECK_IN, BAN, UNBAN)
  - since/until: Timestamp range
  - limit/offset: Pagination
"""

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_admin_user, get_approved_user
from app.models.models import EventLog, EventType, Guest, Meetup, PossibleRoles, User
from app.openapi_examples.events_examples import (
    LIST_ALL_EVENTS_RESPONSES,
    LIST_GUEST_EVENTS_RESPONSES,
    LIST_MEETUP_EVENTS_RESPONSES,
    LIST_STAFF_EVENTS_RESPONSES,
)
from app.schemas.events import (
    EventActorPublic,
    EventGuestPublic,
    EventLogListResponse,
    EventLogPublic,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_event_types(type_param: str | None) -> list[str] | None:
    """
    Parse comma-separated event types into a list.

    Returns None if no filter specified (matches all types).
    Validates that each type is a known EventType.
    """
    if not type_param:
        return None

    types = [t.strip().upper() for t in type_param.split(",")]
    valid_types = {e.value for e in EventType}

    for t in types:
        if t not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid event type '{t}' in filter. "
                    f"Valid types are: {', '.join(sorted(valid_types))}. "
                    f"You can combine multiple types with commas, e.g. ?type=CHECK_IN,UNDO_CHECK_IN"
                ),
            )

    return types


def _build_event_query(
    *,
    type_param: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    guest_id: int | None = None,
    meetup_id: str | None = None,
    actor_id: int | None = None,
):
    """
    Build a SQLModel select query with the given filters.

    All filters are optional and combine with AND logic.
    Returns the query (not executed) for further modification.
    """
    query = select(EventLog)

    # Filter by event type(s)
    event_types = _parse_event_types(type_param)
    if event_types:
        query = query.where(EventLog.event_type.in_(event_types))  # type: ignore[union-attr]

    # Filter by timestamp range
    if since:
        query = query.where(EventLog.timestamp >= since)
    if until:
        query = query.where(EventLog.timestamp <= until)

    # Filter by guest
    if guest_id is not None:
        query = query.where(EventLog.guest_id == guest_id)

    # Filter by meetup
    if meetup_id:
        try:
            meetup_uuid = uuid.UUID(meetup_id)
            query = query.where(EventLog.meetup_id == meetup_uuid)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid meetup_id format: '{meetup_id}' is not a valid UUID. "
                    f"Meetup IDs look like 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'. "
                    f"Get valid meetup IDs from GET /meetups/."
                ),
            ) from exc

    # Filter by actor
    if actor_id is not None:
        query = query.where(EventLog.actor_id == actor_id)

    return query


def _execute_paginated_query(
    session: Session,
    query,
    limit: int,
    offset: int,
) -> EventLogListResponse:
    """
    Execute a query with pagination and return formatted response.

    Counts total matching records for pagination UI, then fetches
    the requested page with eager-loaded relationships.
    """
    # Count total (without pagination)
    from sqlmodel import func

    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Fetch page with relationships
    paginated = (
        query.options(
            selectinload(EventLog.actor),  # type: ignore[arg-type]
            selectinload(EventLog.guest),  # type: ignore[arg-type]
        )
        .order_by(EventLog.timestamp.desc())  # type: ignore[union-attr]
        .offset(offset)
        .limit(limit)
    )
    events = session.exec(paginated).all()

    # Convert to response schema
    event_list = []
    for event in events:
        event_public = EventLogPublic(
            id=event.id,  # type: ignore[arg-type]
            event_type=event.event_type,
            timestamp=event.timestamp,
            actor=EventActorPublic.model_validate(event.actor) if event.actor else None,
            guest=EventGuestPublic.model_validate(event.guest) if event.guest else None,
            meetup_id=str(event.meetup_id) if event.meetup_id else None,
            reason=event.reason,
        )
        event_list.append(event_public)

    return EventLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        events=event_list,
    )


def _is_admin(user: User) -> bool:
    """Check if user has admin role."""
    return user.role is not None and user.role.name == PossibleRoles.ADMIN


# ── GET /events - All events (admin only) ────────────────────────────────────


@router.get(
    "/",
    response_model=EventLogListResponse,
    summary="List all events (admin only)",
    responses=LIST_ALL_EVENTS_RESPONSES,
)
async def list_all_events(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_admin_user),
    type: str | None = Query(
        default=None,
        description="Filter by event type(s), comma-separated: CHECK_IN, UNDO_CHECK_IN, BAN, UNBAN",
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    guest_id: int | None = Query(default=None, description="Filter by guest mazmo_user_id"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    actor_id: int | None = Query(default=None, description="Filter by staff member ID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List all events in the system with optional filtering.

    Admin-only endpoint for full audit trail access. Supports filtering
    by event type, timestamp range, guest, meetup, and actor.

    Returns paginated results ordered by timestamp descending (newest first).
    """
    query = _build_event_query(
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
        actor_id=actor_id,
    )

    return _execute_paginated_query(session, query, limit, offset)


# ── GET /events/meetups/{id} - Events at a meetup ────────────────────────────


@router.get(
    "/meetups/{meetup_id}",
    response_model=EventLogListResponse,
    summary="List events at a specific meetup",
    responses=LIST_MEETUP_EVENTS_RESPONSES,
)
async def list_meetup_events(
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
    type: str | None = Query(
        default=None,
        description="Filter by event type(s), comma-separated: CHECK_IN, UNDO_CHECK_IN, BAN, UNBAN",
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events at a specific meetup.

    All staff can view meetup events to see check-in activity,
    who checked in guests, and any undone check-ins.

    Commonly used filters:
      - ?type=CHECK_IN,UNDO_CHECK_IN → check-in activity only
    """
    # Verify meetup exists
    meetup = session.get(Meetup, meetup_id)
    if not meetup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Meetup {meetup_id} not found. "
                f"It may have been deleted, or the UUID is incorrect. "
                f"List all meetups via GET /meetups/ to find valid IDs."
            ),
        )

    query = _build_event_query(
        type_param=type,
        since=since,
        until=until,
        meetup_id=str(meetup_id),
    )

    return _execute_paginated_query(session, query, limit, offset)


# ── GET /events/guests/{id} - Events for a guest ─────────────────────────────


@router.get(
    "/guests/{guest_id}",
    response_model=EventLogListResponse,
    summary="List events for a specific guest",
    responses=LIST_GUEST_EVENTS_RESPONSES,
)
async def list_guest_events(
    guest_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_approved_user),
    type: str | None = Query(
        default=None,
        description="Filter by event type(s), comma-separated: CHECK_IN, UNDO_CHECK_IN, BAN, UNBAN",
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events for a specific guest.

    Authorization:
      - Staff: Can only see BAN/UNBAN events (need to know who's banned)
      - Admin: Can see all events for this guest

    Common use case: Staff checking why a guest is on the banned list.
    """
    # Verify guest exists
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Guest with mazmo_user_id={guest_id} not found. "
                f"They may not have RSVPed to any meetup yet. "
                f"List all guests via GET /guests/ to find valid IDs."
            ),
        )

    # Staff can only see ban-related events
    if not _is_admin(current_user):
        # Force filter to ban events only for non-admins
        if type:
            # Parse requested types and filter to only ban types
            requested = _parse_event_types(type) or []
            ban_types = {EventType.BAN.value, EventType.UNBAN.value}
            allowed = [t for t in requested if t in ban_types]
            if not allowed:
                # Requested non-ban types as staff → return empty
                return EventLogListResponse(total=0, limit=limit, offset=offset, events=[])
            type = ",".join(allowed)
        else:
            # No type filter → default to ban events for staff
            type = f"{EventType.BAN.value},{EventType.UNBAN.value}"

    query = _build_event_query(
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
    )

    return _execute_paginated_query(session, query, limit, offset)


# ── GET /events/staff/{id} - Events by a staff member ────────────────────────


@router.get(
    "/staff/{staff_id}",
    response_model=EventLogListResponse,
    summary="List events by a specific staff member",
    responses=LIST_STAFF_EVENTS_RESPONSES,
)
async def list_staff_events(
    staff_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_approved_user),
    type: str | None = Query(
        default=None,
        description="Filter by event type(s), comma-separated: CHECK_IN, UNDO_CHECK_IN, BAN, UNBAN",
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    guest_id: int | None = Query(default=None, description="Filter by guest mazmo_user_id"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events performed by a specific staff member.

    Authorization:
      - Staff: Can only view their own events (staff_id must match current user)
      - Admin: Can view any staff member's events

    Use case: Staff reviewing their own activity, or admin auditing staff actions.
    """
    # Verify target user exists
    target_user = session.get(User, staff_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Staff member with id={staff_id} not found. "
                f"This user may have been deleted. "
                f"List all staff via GET /staff/ (admin only)."
            ),
        )

    # Staff can only view their own events
    if not _is_admin(current_user) and current_user.id != staff_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You can only view your own events. "
                f"You are user id={current_user.id}, but requested events for user id={staff_id}. "
                f"Admins can view any staff member's events."
            ),
        )

    query = _build_event_query(
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
        actor_id=staff_id,
    )

    return _execute_paginated_query(session, query, limit, offset)
