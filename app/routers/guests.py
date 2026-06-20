"""
Guests router - global guest identity management.

POST  /guests/                         -> register a guest by Mazmo username (approved user)
GET   /guests/                         -> list all known guests (identity only, approved user)
GET   /guests/{mazmo_user_id}          -> get a single guest by numeric ID (approved user)
GET   /guests/by-username/{username}   -> get a single guest by Mazmo username (approved user)

Ban management is org-scoped and lives under /organizations/{org_id}/guests/...
"""

from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import EventLog, EventType, Guest, User
from app.openapi_examples.guests_examples import (
    CREATE_GUEST_REQUEST_EXAMPLES,
    CREATE_GUEST_RESPONSES,
    GET_GUEST_BY_USERNAME_RESPONSES,
    GET_GUEST_RESPONSES,
    LIST_GUESTS_RESPONSES,
)
from app.schemas import (
    CreateGuestRequest,
    GuestListResponse,
    GuestPublic,
)
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/guests", tags=["guests"])


# -- Create guest by Mazmo username -------------------------------------------


@router.post(
    "/",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest by Mazmo username",
    responses=CREATE_GUEST_RESPONSES,
)
async def create_guest(
    request: Annotated[CreateGuestRequest, Body(openapi_examples=CREATE_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Register a guest using their Mazmo username handle.

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
                f"POST /organizations/{{org_id}}/meetups/{{meetup_id}}/guests/{user.mazmo_user_id}/add-walkin."
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


# -- List guests --------------------------------------------------------------


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


# -- Get single guest ---------------------------------------------------------


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
                f"Guests are added when they RSVP to a meetup and we sync from Mazmo, "
                f"or when registered manually via POST /guests/."
            ),
        )
    return guest


# -- Get guest by username ----------------------------------------------------


@router.get(
    "/by-username/{username}",
    response_model=GuestPublic,
    summary="Get a single guest by Mazmo username",
    responses=GET_GUEST_BY_USERNAME_RESPONSES,
)
async def get_guest_by_username(
    username: str,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their Mazmo username handle."""
    guest = session.exec(select(Guest).where(Guest.username == username)).first()
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No guest with username '{username}' found in the system. "
                f"They may not have RSVPed to any meetup yet. "
                f"Use POST /guests/ to register them if they're at the door."
            ),
        )
    return guest
