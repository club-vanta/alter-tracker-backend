"""
Events router - org-scoped audit trail API.

GET /organizations/{org_id}/events/                -> all events in org (org admin)
GET /organizations/{org_id}/events/meetups/{id}    -> events at a meetup (org member)
GET /organizations/{org_id}/events/guests/{id}     -> events for a guest (org member; staff: bans only)
GET /organizations/{org_id}/events/staff/{id}      -> events by a staff member (org member; staff: own only)

All endpoints support filtering by:
  - type: Event type(s) comma-separated (see EventType in app/models/models.py for the full list)
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
from app.core.deps import get_org_admin, get_org_member
from app.models.models import EventLog, EventType, Guest, Meetup, OrgRole, PossibleRoles, User, UserOrganization
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
router = APIRouter(tags=["events"])

# Built from the EventType enum so this never goes stale as new event types are added.
_EVENT_TYPE_FILTER_DESCRIPTION = f"Filter by event type(s), comma-separated: {', '.join(e.value for e in EventType)}"


# -- Helpers ------------------------------------------------------------------


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
    org_id: uuid.UUID,
    type_param: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    guest_id: uuid.UUID | None = None,
    meetup_id: str | None = None,
    actor_id: int | None = None,
):
    """
    Build a SQLModel select query for events in an org with the given filters.

    All filters are optional and combine with AND logic.
    Returns the query (not executed) for further modification.
    """
    query = select(EventLog).where(EventLog.org_id == org_id)

    event_types = _parse_event_types(type_param)
    if event_types:
        query = query.where(EventLog.event_type.in_(event_types))  # type: ignore[union-attr]

    if since:
        query = query.where(EventLog.timestamp >= since)
    if until:
        query = query.where(EventLog.timestamp <= until)

    if guest_id is not None:
        query = query.where(EventLog.guest_id == guest_id)

    if meetup_id:
        try:
            meetup_uuid = uuid.UUID(meetup_id)
            query = query.where(EventLog.meetup_id == meetup_uuid)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid meetup_id format: '{meetup_id}' is not a valid UUID. "
                    f"Meetup IDs look like 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'."
                ),
            ) from exc

    if actor_id is not None:
        query = query.where(EventLog.actor_id == actor_id)

    return query


def _execute_paginated_query(
    session: Session,
    query,
    limit: int,
    offset: int,
) -> EventLogListResponse:
    """Execute a query with pagination and return formatted response."""
    from sqlmodel import func

    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

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


def _is_org_admin(user: User, org_id: uuid.UUID, session: Session) -> bool:
    """Return True if user is SITE_ADMIN or has ADMIN role in org_id."""
    if user.role and user.role.name == PossibleRoles.SITE_ADMIN:
        return True
    membership = session.exec(
        select(UserOrganization).where(UserOrganization.user_id == user.id).where(UserOrganization.org_id == org_id)
    ).first()
    return membership is not None and membership.role == OrgRole.ADMIN


# -- GET /organizations/{org_id}/events/ - All events (org admin) -------------


@router.get(
    "/organizations/{org_id}/events/",
    response_model=EventLogListResponse,
    summary="List all events in this organization (org admin only)",
    responses=LIST_ALL_EVENTS_RESPONSES,
)
async def list_all_events(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
    type: str | None = Query(
        default=None,
        description=_EVENT_TYPE_FILTER_DESCRIPTION,
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    guest_id: uuid.UUID | None = Query(default=None, description="Filter by guest id"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    actor_id: int | None = Query(default=None, description="Filter by staff member ID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List all events in the organization with optional filtering.

    Org admin-only endpoint for full audit trail access. Supports filtering
    by event type, timestamp range, guest, meetup, and actor.

    Returns paginated results ordered by timestamp descending (newest first).
    """
    query = _build_event_query(
        org_id=org_id,
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
        actor_id=actor_id,
    )

    return _execute_paginated_query(session, query, limit, offset)


# -- GET /organizations/{org_id}/events/meetups/{id} - Events at a meetup ----


@router.get(
    "/organizations/{org_id}/events/meetups/{meetup_id}",
    response_model=EventLogListResponse,
    summary="List events at a specific meetup",
    responses=LIST_MEETUP_EVENTS_RESPONSES,
)
async def list_meetup_events(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
    type: str | None = Query(
        default=None,
        description=_EVENT_TYPE_FILTER_DESCRIPTION,
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events at a specific meetup.

    All org members can view meetup events to see check-in activity,
    who checked in guests, and any undone check-ins.
    """
    meetup = session.get(Meetup, meetup_id)
    if not meetup or meetup.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Meetup {meetup_id} not found in this organization. "
                f"List this org's meetups via GET /organizations/{org_id}/meetups/."
            ),
        )

    query = _build_event_query(
        org_id=org_id,
        type_param=type,
        since=since,
        until=until,
        meetup_id=str(meetup_id),
    )

    return _execute_paginated_query(session, query, limit, offset)


# -- GET /organizations/{org_id}/events/guests/{id} - Events for a guest -----


@router.get(
    "/organizations/{org_id}/events/guests/{guest_id}",
    response_model=EventLogListResponse,
    summary="List events for a specific guest in this organization",
    responses=LIST_GUEST_EVENTS_RESPONSES,
)
async def list_guest_events(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_org_member),
    type: str | None = Query(
        default=None,
        description=_EVENT_TYPE_FILTER_DESCRIPTION,
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events for a specific guest in this organization.

    Authorization:
      - Staff (org member): Can only see BAN/UNBAN events (need to know who's banned)
      - Org admin / SITE_ADMIN: Can see all events for this guest in this org
    """
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Guest with id={guest_id} not found. "
                f"They may not have RSVPed to any meetup yet. "
                f"List all guests via GET /guests/ to find valid IDs."
            ),
        )

    if not _is_org_admin(current_user, org_id, session):
        if type:
            requested = _parse_event_types(type) or []
            ban_types = {EventType.BAN.value, EventType.UNBAN.value}
            allowed = [t for t in requested if t in ban_types]
            if not allowed:
                return EventLogListResponse(total=0, limit=limit, offset=offset, events=[])
            type = ",".join(allowed)
        else:
            type = f"{EventType.BAN.value},{EventType.UNBAN.value}"

    query = _build_event_query(
        org_id=org_id,
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
    )

    return _execute_paginated_query(session, query, limit, offset)


# -- GET /organizations/{org_id}/events/staff/{id} - Events by a staff member -


@router.get(
    "/organizations/{org_id}/events/staff/{staff_id}",
    response_model=EventLogListResponse,
    summary="List events by a specific staff member in this organization",
    responses=LIST_STAFF_EVENTS_RESPONSES,
)
async def list_staff_events(
    org_id: uuid.UUID,
    staff_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_org_member),
    type: str | None = Query(
        default=None,
        description=_EVENT_TYPE_FILTER_DESCRIPTION,
    ),
    since: datetime | None = Query(default=None, description="Events after this timestamp"),
    until: datetime | None = Query(default=None, description="Events before this timestamp"),
    guest_id: uuid.UUID | None = Query(default=None, description="Filter by guest id"),
    meetup_id: str | None = Query(default=None, description="Filter by meetup UUID"),
    limit: int = Query(default=50, ge=1, le=100, description="Events per page (max 100)"),
    offset: int = Query(default=0, ge=0, description="Skip N events"),
) -> EventLogListResponse:
    """
    List events performed by a specific staff member in this organization.

    Authorization:
      - Staff: Can only view their own events (staff_id must match current user)
      - Org admin / SITE_ADMIN: Can view any staff member's events in this org
    """
    target_user = session.get(User, staff_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Staff member with id={staff_id} not found. "
                f"This user may have been deleted. "
                f"List all staff via GET /staff/ (site admin only)."
            ),
        )

    if not _is_org_admin(current_user, org_id, session) and current_user.id != staff_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You can only view your own events. "
                f"You are user id={current_user.id}, but requested events for user id={staff_id}. "
                f"Org admins can view any staff member's events."
            ),
        )

    query = _build_event_query(
        org_id=org_id,
        type_param=type,
        since=since,
        until=until,
        guest_id=guest_id,
        meetup_id=meetup_id,
        actor_id=staff_id,
    )

    return _execute_paginated_query(session, query, limit, offset)
