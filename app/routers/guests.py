"""
Guests router - manages guest identity and ban status.

GET   /api/guests/                    → list all known guests (identity only)
GET   /api/guests/banned              → list all banned guests (staff can view)
GET   /api/guests/{mazmo_user_id}     → get a single guest's identity
PATCH /api/guests/{mazmo_user_id}/ban   → ban a guest (admin only)
PATCH /api/guests/{mazmo_user_id}/unban → unban a guest (admin only)

Note: Meetup-specific operations (sync, checkin) are in the meetups router.
"""

from datetime import UTC, datetime
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_admin_user, get_approved_user
from app.domain_types import MazmoUserId
from app.models.models import EventLog, EventType, Guest, User
from app.openapi_examples.guests_examples import (
    BAN_REQUEST_EXAMPLES,
    BAN_RESPONSES,
    CREATE_GUEST_BY_USERNAME_REQUEST_EXAMPLES,
    CREATE_GUEST_BY_USERNAME_RESPONSES,
    CREATE_GUEST_REQUEST_EXAMPLES,
    CREATE_GUEST_RESPONSES,
    GET_GUEST_RESPONSES,
    LIST_BANNED_RESPONSES,
    LIST_GUESTS_RESPONSES,
    UNBAN_RESPONSES,
)
from app.schemas import (
    BanGuestRequest,
    BannedGuestListResponse,
    BannedGuestPublic,
    CreateGuestByUsernameRequest,
    CreateGuestRequest,
    GuestListResponse,
    GuestPublic,
)
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/guests", tags=["guests"])


# ── Create guest manually ────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Manually create a guest (no Mazmo sync required)",
    responses=CREATE_GUEST_RESPONSES,
)
async def create_guest(
    request: Annotated[CreateGuestRequest, Body(openapi_examples=CREATE_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Manually register a guest who has no prior Mazmo sync history.

    Use this when someone shows up at the door and has never RSVPed to any
    previous meetup — so they don't exist in our system yet. After creating
    them here, they can be added to a meetup via `POST /meetups/{id}/guests/{mazmo_user_id}/add-walkin`.

    The `mazmo_user_id` must match the guest's actual Mazmo profile ID.
    If the guest already exists in the system (synced from a previous meetup),
    this returns 409 — no need to create them manually.
    """
    existing = session.get(Guest, request.mazmo_user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot create guest: mazmo_user_id={request.mazmo_user_id} already exists "
                f"in the system as '{existing.username}'. "
                f"If you want to add them to a meetup, use "
                f"POST /meetups/{{meetup_id}}/guests/{request.mazmo_user_id}/add-walkin."
            ),
        )

    guest = Guest(
        mazmo_user_id=MazmoUserId(request.mazmo_user_id),
        username=request.username,
        displayname=request.displayname,
    )

    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=MazmoUserId(request.mazmo_user_id),
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest created manually",
        staff=staff.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
    )

    return guest


# ── Create guest by Mazmo username ───────────────────────────────────────────


@router.post(
    "/by-username",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest by Mazmo username (no numeric ID needed)",
    responses=CREATE_GUEST_BY_USERNAME_RESPONSES,
)
async def create_guest_by_username(
    request: Annotated[CreateGuestByUsernameRequest, Body(openapi_examples=CREATE_GUEST_BY_USERNAME_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Register a guest using only their Mazmo username handle.

    Looks up the canonical Mazmo user ID and profile data automatically,
    so staff at the door only need to know the handle (e.g. "cindydark").

    Returns 404 if the username doesn't exist on Mazmo.
    Returns 409 if the guest is already registered in the system.
    Returns 504 if Mazmo is unreachable.
    """
    try:
        async with MazmoClient(settings) as client:
            user = await client.fetch_user_by_username(request.username)
    except MazmoNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(f"Cannot create guest: failed to connect to Mazmo API. Error: {exc}. Try again in a few moments."),
        ) from exc
    except MazmoAPIError as exc:
        if isinstance(exc.__cause__, httpx.HTTPStatusError) and exc.__cause__.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"Username '{request.username}' was not found on Mazmo. Check the spelling and try again."),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mazmo API returned an error: {exc}",
        ) from exc

    existing = session.get(Guest, user.mazmo_user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot create guest: mazmo_user_id={user.mazmo_user_id} already exists "
                f"in the system as '{existing.username}'. "
                f"If you want to add them to a meetup, use "
                f"POST /meetups/{{meetup_id}}/guests/{user.mazmo_user_id}/add-walkin."
            ),
        )

    guest = Guest(
        mazmo_user_id=user.mazmo_user_id,
        username=user.username,
        displayname=user.displayname,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=user.mazmo_user_id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest created by username lookup",
        staff=staff.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
    )

    return guest


# ── List guests ──────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=GuestListResponse,
    summary="List all known guests (identity only)",
    responses=LIST_GUESTS_RESPONSES,
)
async def list_guests(
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> GuestListResponse:
    """List all guests in the system (identity only, no RSVP state)."""
    guests = session.exec(select(Guest).order_by(Guest.username)).all()
    return GuestListResponse(
        total=len(guests),
        guests=[GuestPublic.model_validate(g) for g in guests],
    )


# ── List banned guests ────────────────────────────────────────────────────────


@router.get(
    "/banned",
    response_model=BannedGuestListResponse,
    summary="List all banned guests",
    responses=LIST_BANNED_RESPONSES,
)
async def list_banned_guests(
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> BannedGuestListResponse:
    """List all banned guests with their ban details."""
    guests = session.exec(
        select(Guest).where(Guest.is_banned == True).order_by(Guest.username)  # noqa: E712
    ).all()
    return BannedGuestListResponse(
        total=len(guests),
        guests=[BannedGuestPublic.model_validate(g) for g in guests],
    )


# ── Get single guest ─────────────────────────────────────────────────────────


@router.get(
    "/{mazmo_user_id}",
    response_model=GuestPublic,
    summary="Get a single guest's identity",
    responses=GET_GUEST_RESPONSES,
)
async def get_guest(
    mazmo_user_id: int,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their Mazmo user ID."""
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Guest with mazmo_user_id={mazmo_user_id} does not exist in our database. "
                f"This guest may not have RSVPed to any meetup yet, or the ID might be incorrect. "
                f"Guests are only added when they RSVP to a meetup and we sync from Mazmo. "
                f"Try POST /meetups/{{meetup_id}}/sync first, or verify the mazmo_user_id."
            ),
        )
    return guest


# ── Ban guest ─────────────────────────────────────────────────────────────────


@router.patch(
    "/{mazmo_user_id}/ban",
    response_model=BannedGuestPublic,
    summary="Ban a guest (admin only)",
    responses=BAN_RESPONSES,
)
async def ban_guest(
    mazmo_user_id: int,
    request: Annotated[BanGuestRequest, Body(openapi_examples=BAN_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> Guest:
    """
    Ban a guest. Records the admin who performed the ban and the reason.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already banned.
    """
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot ban guest: mazmo_user_id={mazmo_user_id} does not exist in our database. "
                f"Guests are only added when they RSVP to a meetup and we sync from Mazmo. "
                f"Sync a meetup they've RSVPed to first, then try banning them again."
            ),
        )
    if guest.is_banned:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot ban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is already banned. They were banned on {guest.banned_at} "
                f"for reason: '{guest.banned_reason}'. To update the ban reason, "
                f"unban first via PATCH /guests/{mazmo_user_id}/unban, then re-ban."
            ),
        )

    guest.is_banned = True
    guest.banned_at = datetime.now(UTC)
    guest.banned_by_id = admin.id
    guest.banned_reason = request.reason

    # Create audit log entry
    event = EventLog(
        event_type=EventType.BAN,
        actor_id=admin.id,
        guest_id=guest.mazmo_user_id,
        reason=request.reason,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest banned",
        admin=admin.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
        reason=request.reason,
    )
    return guest


# ── Unban guest ───────────────────────────────────────────────────────────────


@router.patch(
    "/{mazmo_user_id}/unban",
    response_model=GuestPublic,
    summary="Unban a guest (admin only)",
    responses=UNBAN_RESPONSES,
)
async def unban_guest(
    mazmo_user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> Guest:
    """
    Unban a guest. Clears all ban-related fields.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently banned.
    """
    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot unban guest: mazmo_user_id={mazmo_user_id} does not exist. "
                f"Double-check the ID via GET /guests/ or GET /guests/banned."
            ),
        )
    if not guest.is_banned:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is not currently banned. They may have been unbanned by another admin. "
                f"Check audit trail at GET /events/guests/{mazmo_user_id}."
            ),
        )

    guest.is_banned = False
    guest.banned_at = None
    guest.banned_by_id = None
    guest.banned_reason = None

    # Create audit log entry
    event = EventLog(
        event_type=EventType.UNBAN,
        actor_id=admin.id,
        guest_id=guest.mazmo_user_id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest unbanned",
        admin=admin.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
    )
    return guest
