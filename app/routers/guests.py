"""
Guests router - global guest identity management.

POST  /guests/mazmo                          -> create a guest by Mazmo username (approved user)
POST  /guests/manual                         -> create a guest without a Mazmo account (approved user)
GET   /guests/                               -> list all known guests, optional ?q= search (approved user)
GET   /guests/{guest_id}                     -> get a single guest by internal id (approved user)
GET   /guests/by-mazmo-handle/{mazmo_handle} -> get a single guest by Mazmo handle (approved user)
PATCH /guests/{guest_id}/link-mazmo          -> link an existing guest to a Mazmo account (approved user)
PATCH /guests/{guest_id}/unlink-mazmo        -> unlink a guest's Mazmo account (approved user)
PATCH /guests/{guest_id}                     -> edit displayname/instagram_username (approved user)

Ban management is org-scoped and lives under /organizations/{org_id}/guests/...
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    OrganizationBan,
    User,
)
from app.openapi_examples.guests_examples import (
    CREATE_MANUAL_GUEST_REQUEST_EXAMPLES,
    CREATE_MANUAL_GUEST_RESPONSES,
    CREATE_MAZMO_GUEST_REQUEST_EXAMPLES,
    CREATE_MAZMO_GUEST_RESPONSES,
    GET_DISPLAYNAME_HISTORY_RESPONSES,
    GET_GUEST_BY_MAZMO_HANDLE_RESPONSES,
    GET_GUEST_RESPONSES,
    LINK_MAZMO_REQUEST_EXAMPLES,
    LINK_MAZMO_RESPONSES,
    LIST_GUESTS_RESPONSES,
    UNLINK_MAZMO_RESPONSES,
    UPDATE_GUEST_REQUEST_EXAMPLES,
    UPDATE_GUEST_RESPONSES,
)
from app.schemas import (
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestDisplaynameHistoryListResponse,
    GuestDisplaynameHistoryPublic,
    GuestListResponse,
    GuestPublic,
    LinkMazmoRequest,
    UpdateGuestRequest,
)
from app.schemas.events import EventActorPublic
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/guests", tags=["guests"])


def _get_guest_or_404(session: Session, guest_id: uuid.UUID) -> Guest:
    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Guest with id={guest_id} does not exist in our database. "
                f"Guests are added when they RSVP to a meetup and we sync from Mazmo, "
                f"or when registered manually via POST /guests/mazmo or POST /guests/manual."
            ),
        )
    return guest


# -- Create guest by Mazmo username --------------------------------------------


@router.post(
    "/mazmo",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest by Mazmo username",
    responses=CREATE_MAZMO_GUEST_RESPONSES,
)
async def create_guest_from_mazmo(
    request: Annotated[CreateGuestRequest, Body(openapi_examples=CREATE_MAZMO_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Register a guest using their Mazmo username handle.

    Looks up the canonical Mazmo user ID and profile data automatically,
    so staff at the door only need to know the handle (e.g. "cindydark").

    Returns 404 if the username doesn't exist on Mazmo.
    Returns 409 if that mazmo_user_id is already registered.
    Returns 504 if Mazmo is unreachable.
    """
    try:
        async with MazmoClient(settings) as client:
            mazmo_user = await client.fetch_user_by_username(request.username)
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

    existing = session.exec(select(Guest).where(Guest.mazmo_user_id == mazmo_user.mazmo_user_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot create guest: mazmo_user_id={mazmo_user.mazmo_user_id} already exists "
                f"in the system as '{existing.displayname}' (id={existing.id}). "
                f"If you want to add them to a meetup, use "
                f"POST /organizations/{{org_id}}/meetups/{{meetup_id}}/guests/{existing.id}/add-walkin."
            ),
        )

    guest = Guest(
        mazmo_user_id=mazmo_user.mazmo_user_id,
        mazmo_handle=mazmo_user.username,
        displayname=mazmo_user.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )
    history = GuestDisplaynameHistory(
        guest_id=guest.id,
        displayname=guest.displayname,
        source=GuestDisplaynameSource.MANUAL_EDIT,
        actor_id=staff.id,
    )

    session.add(guest)
    session.add(event)
    session.add(history)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest created by Mazmo username lookup",
        staff=staff.username,
        guest_id=str(guest.id),
        mazmo_handle=guest.mazmo_handle,
    )

    return guest


# -- Create guest without a Mazmo account --------------------------------------


@router.post(
    "/manual",
    response_model=GuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest without a Mazmo account",
    responses=CREATE_MANUAL_GUEST_RESPONSES,
)
async def create_manual_guest(
    request: Annotated[CreateManualGuestRequest, Body(openapi_examples=CREATE_MANUAL_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Register a guest who does not have a Mazmo account.

    No dedup check is performed: there is no external identifier to
    deduplicate against, so two guests with the same displayname can
    coexist. Use PATCH /guests/{guest_id}/link-mazmo later if this guest
    turns out to have (or creates) a Mazmo account.
    """
    guest = Guest(
        displayname=request.displayname,
        instagram_username=request.instagram_username,
    )
    event = EventLog(
        event_type=EventType.GUEST_CREATED,
        actor_id=staff.id,
        guest_id=guest.id,
    )
    history = GuestDisplaynameHistory(
        guest_id=guest.id,
        displayname=guest.displayname,
        source=GuestDisplaynameSource.MANUAL_EDIT,
        actor_id=staff.id,
    )

    session.add(guest)
    session.add(event)
    session.add(history)
    session.commit()
    session.refresh(guest)

    log.info(
        "Manual guest created",
        staff=staff.username,
        guest_id=str(guest.id),
        displayname=guest.displayname,
    )

    return guest


# -- List guests ----------------------------------------------------------------


@router.get(
    "/",
    response_model=GuestListResponse,
    summary="List all known guests (identity only)",
    responses=LIST_GUESTS_RESPONSES,
)
async def list_guests(
    q: str | None = Query(
        default=None,
        description="Filter by displayname or mazmo_handle (case-insensitive substring)",
    ),
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> GuestListResponse:
    """List all guests in the system (identity only, no RSVP state)."""
    query = select(Guest)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Guest.displayname.ilike(pattern), Guest.mazmo_handle.ilike(pattern)))  # type: ignore[union-attr]

    guests = session.exec(query.order_by(Guest.displayname)).all()  # type: ignore[attr-defined]
    return GuestListResponse(
        total=len(guests),
        guests=[GuestPublic.model_validate(g) for g in guests],
    )


# -- Get single guest -------------------------------------------------------------


@router.get(
    "/{guest_id}",
    response_model=GuestPublic,
    summary="Get a single guest's identity",
    responses=GET_GUEST_RESPONSES,
)
async def get_guest(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their internal id."""
    return _get_guest_or_404(session, guest_id)


# -- Get guest by Mazmo handle ----------------------------------------------------


@router.get(
    "/by-mazmo-handle/{mazmo_handle}",
    response_model=GuestPublic,
    summary="Get a single guest by Mazmo handle",
    responses=GET_GUEST_BY_MAZMO_HANDLE_RESPONSES,
)
async def get_guest_by_mazmo_handle(
    mazmo_handle: str,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> Guest:
    """Get a single guest by their Mazmo handle. Guests without Mazmo never match."""
    guest = session.exec(select(Guest).where(Guest.mazmo_handle == mazmo_handle)).first()
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No guest with Mazmo handle '{mazmo_handle}' found in the system. "
                f"They may not have RSVPed to any meetup yet, or may not have a Mazmo "
                f"account at all. Use POST /guests/mazmo to register them by handle, "
                f"or POST /guests/manual if they don't use Mazmo."
            ),
        )
    return guest


# -- Link an existing guest to a Mazmo account -------------------------------------


@router.patch(
    "/{guest_id}/link-mazmo",
    response_model=GuestPublic,
    summary="Link an existing guest to a Mazmo account",
    responses=LINK_MAZMO_RESPONSES,
)
async def link_guest_to_mazmo(
    guest_id: uuid.UUID,
    request: Annotated[LinkMazmoRequest, Body(openapi_examples=LINK_MAZMO_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> Guest:
    """
    Attach a Mazmo account to a guest created without one.

    Overwrites mazmo_user_id, mazmo_handle, and displayname with the
    Mazmo profile data. instagram_username is left untouched.

    If the incoming Mazmo displayname differs from the guest's previous
    value, writes a GuestDisplaynameHistory row (source=MAZMO_LINK) and
    an EventLog(GUEST_DISPLAYNAME_CHANGED) entry, alongside the existing
    EventLog(GUEST_MAZMO_LINKED) - same commit, same timestamp.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already linked, or if the Mazmo account
    is already linked to a different guest (no automatic merge).
    """
    guest = _get_guest_or_404(session, guest_id)

    if guest.mazmo_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: guest '{guest.displayname}' (id={guest_id}) is already "
                f"linked to Mazmo handle '@{guest.mazmo_handle}'. Unlink first via "
                f"PATCH /guests/{guest_id}/unlink-mazmo if you need to change it."
            ),
        )

    try:
        async with MazmoClient(settings) as client:
            mazmo_user = await client.fetch_user_by_username(request.username)
    except MazmoNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(f"Cannot link: failed to connect to Mazmo API. Error: {exc}. Try again in a few moments."),
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

    existing = session.exec(select(Guest).where(Guest.mazmo_user_id == mazmo_user.mazmo_user_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: mazmo_user_id={mazmo_user.mazmo_user_id} already belongs to "
                f"guest '{existing.displayname}' (id={existing.id}). Merging two guests is not "
                f"supported; pick the correct guest or fix the Mazmo handle."
            ),
        )

    old_displayname = guest.displayname
    guest.mazmo_user_id = mazmo_user.mazmo_user_id
    guest.mazmo_handle = mazmo_user.username
    guest.displayname = mazmo_user.displayname

    now = datetime.now(UTC)
    event = EventLog(
        event_type=EventType.GUEST_MAZMO_LINKED,
        actor_id=staff.id,
        guest_id=guest.id,
        timestamp=now,
    )

    session.add(guest)
    session.add(event)

    if guest.displayname != old_displayname:
        session.add(
            GuestDisplaynameHistory(
                guest_id=guest.id,
                displayname=guest.displayname,
                source=GuestDisplaynameSource.MAZMO_LINK,
                actor_id=staff.id,
                recorded_at=now,
            )
        )
        session.add(
            EventLog(
                event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                org_id=None,
                actor_id=staff.id,
                guest_id=guest.id,
                timestamp=now,
                reason=f"Displayname changed from '{old_displayname[:200]}' to '{guest.displayname[:200]}'",
            )
        )

    try:
        session.commit()
    except IntegrityError:
        # Race: another request linked the same mazmo_user_id first,
        # between our pre-check SELECT above and this commit.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot link: mazmo_user_id={mazmo_user.mazmo_user_id} was linked to "
                f"another guest by a concurrent request. Look it up via "
                f"GET /guests/by-mazmo-handle/{mazmo_user.username} to see who has it now."
            ),
        ) from None
    session.refresh(guest)

    log.info(
        "Guest linked to Mazmo",
        staff=staff.username,
        guest_id=str(guest.id),
        mazmo_handle=guest.mazmo_handle,
    )

    return guest


# -- Unlink a guest's Mazmo account -----------------------------------------------


@router.patch(
    "/{guest_id}/unlink-mazmo",
    response_model=GuestPublic,
    summary="Unlink a guest's Mazmo account",
    responses=UNLINK_MAZMO_RESPONSES,
)
async def unlink_guest_mazmo(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Detach a guest's Mazmo account, e.g. to undo a link made by mistake.

    displayname is NOT reverted to any prior value (no name history is
    kept) - it stays as whatever it was, and can be corrected afterward
    via PATCH /guests/{guest_id}. The freed mazmo_user_id can be linked
    to this guest again, or to a different one.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently linked.
    Returns 409 if the guest has an active ban in any organization - see
    the ban-evasion guard below for why this is blocked.
    """
    guest = _get_guest_or_404(session, guest_id)

    if guest.mazmo_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Cannot unlink: guest '{guest.displayname}' (id={guest_id}) is not linked to a Mazmo account."),
        )

    # Ban-evasion guard.
    #
    # Bans live on OrganizationBan, keyed to this guest's internal id, so an
    # active ban would survive the unlink itself - the OrganizationBan row
    # is untouched by this endpoint. The actual danger is what unlinking
    # enables next: once mazmo_user_id/mazmo_handle are cleared here, that
    # Mazmo handle is no longer claimed by anyone in our database, so
    # POST /guests/mazmo with the SAME Mazmo handle creates a brand-new
    # Guest row - fresh UUID, zero OrganizationBan rows, no dedup check
    # against this (banned) guest. That new guest can then check in at the
    # door as if nothing happened, even though it is controlled by the same
    # banned person's Mazmo account. Banning requires org ADMIN (see
    # PATCH /organizations/{org_id}/guests/{guest_id}/ban), but this
    # unlink-mazmo endpoint only requires an approved user - so without
    # this check, any approved staff member could silently undo an admin's
    # ban decision without ever touching the ban record. We deliberately
    # query across ALL organizations (no org_id filter): bans are per-org,
    # but re-registering as a fresh identity would let the guest walk back
    # into EVERY org they were banned from, not just the one an admin
    # happened to ban them in.
    active_ban = session.exec(select(OrganizationBan).where(OrganizationBan.guest_id == guest_id)).first()
    if active_ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unlink: guest '{guest.displayname}' (id={guest_id}) has an active ban "
                f"in organization id={active_ban.org_id} (reason: '{active_ban.reason}'). "
                f"Unlinking is blocked specifically because it would free Mazmo handle "
                f"'@{guest.mazmo_handle}' to be re-registered via POST /guests/mazmo as a brand-new, "
                f"unbanned guest - letting a banned person re-enter as if they were never banned. "
                f"If you genuinely need to unlink, first unban them via "
                f"PATCH /organizations/{active_ban.org_id}/guests/{guest_id}/unban, then retry."
            ),
        )

    previous_mazmo_user_id = guest.mazmo_user_id
    guest.mazmo_user_id = None
    guest.mazmo_handle = None

    event = EventLog(
        event_type=EventType.GUEST_MAZMO_UNLINKED,
        actor_id=staff.id,
        guest_id=guest.id,
    )

    session.add(guest)
    session.add(event)
    session.commit()
    session.refresh(guest)

    log.info(
        "Guest unlinked from Mazmo",
        staff=staff.username,
        guest_id=str(guest.id),
        previous_mazmo_user_id=previous_mazmo_user_id,
    )

    return guest


# -- Edit guest -------------------------------------------------------------------


@router.patch(
    "/{guest_id}",
    response_model=GuestPublic,
    summary="Edit a guest's displayname and/or Instagram handle",
    responses=UPDATE_GUEST_RESPONSES,
)
async def update_guest(
    guest_id: uuid.UUID,
    payload: Annotated[UpdateGuestRequest, Body(openapi_examples=UPDATE_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_approved_user),
) -> Guest:
    """
    Edit a guest's displayname and/or instagram_username.

    A key omitted from the request body is left untouched. A key sent
    explicitly as null clears it - but only for instagram_username.
    displayname is typed str | None on the request schema, so the schema
    itself accepts {"displayname": null} without a 422; it is this
    function's own guard (payload.displayname is not None) that silently
    ignores an explicit null for displayname instead of clearing it,
    because Guest.displayname is non-nullable and cannot actually be
    cleared. This distinction uses payload.model_fields_set, since
    payload.instagram_username is None in both the "omitted" and
    "explicitly cleared" cases.

    mazmo_user_id and mazmo_handle cannot be changed here - use
    link-mazmo/unlink-mazmo for that.

    A real displayname change (new value differs from the current one)
    writes a GuestDisplaynameHistory row (source=MANUAL_EDIT) and an
    EventLog(GUEST_DISPLAYNAME_CHANGED) entry, in the same commit as the
    guest update. Omitting displayname, or "changing" it to its current
    value, writes neither - only a real change is audited.
    """
    guest = _get_guest_or_404(session, guest_id)

    if "displayname" in payload.model_fields_set and payload.displayname is not None:
        if payload.displayname != guest.displayname:
            old_displayname = guest.displayname
            guest.displayname = payload.displayname
            now = datetime.now(UTC)
            session.add(
                GuestDisplaynameHistory(
                    guest_id=guest.id,
                    displayname=guest.displayname,
                    source=GuestDisplaynameSource.MANUAL_EDIT,
                    actor_id=staff.id,
                    recorded_at=now,
                )
            )
            session.add(
                EventLog(
                    event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                    org_id=None,
                    actor_id=staff.id,
                    guest_id=guest.id,
                    timestamp=now,
                    reason=f"Displayname changed from '{old_displayname[:200]}' to '{guest.displayname[:200]}'",
                )
            )
    if "instagram_username" in payload.model_fields_set:
        guest.instagram_username = payload.instagram_username

    session.add(guest)
    session.commit()
    session.refresh(guest)

    return guest


# -- Get a guest's displayname history ---------------------------------------------


@router.get(
    "/{guest_id}/displayname-history",
    response_model=GuestDisplaynameHistoryListResponse,
    summary="Get a guest's full displayname history",
    responses=GET_DISPLAYNAME_HISTORY_RESPONSES,
)
async def get_guest_displayname_history(
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
) -> GuestDisplaynameHistoryListResponse:
    """
    Get the full displayname history for a guest, newest first.

    Not paginated: a displayname changes rarely over a guest's lifetime,
    so the complete list is always returned. A guest with no changes
    since creation still has exactly one row (its initial value) - never
    an empty list, since every guest creation path writes one.

    Global guest endpoint, same as GET /guests/{guest_id} - not scoped
    to an organization.
    """
    _get_guest_or_404(session, guest_id)

    rows = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest_id)
        .options(selectinload(GuestDisplaynameHistory.actor))  # type: ignore[arg-type]
        .order_by(GuestDisplaynameHistory.recorded_at.desc())  # type: ignore[union-attr]
    ).all()

    return GuestDisplaynameHistoryListResponse(
        total=len(rows),
        history=[
            GuestDisplaynameHistoryPublic(
                displayname=row.displayname,
                source=row.source,
                recorded_at=row.recorded_at,
                actor=EventActorPublic.model_validate(row.actor) if row.actor else None,
            )
            for row in rows
        ],
    )
