"""
Meetups router - org-scoped meetup management.

POST  /organizations/{org_id}/meetups/                           -> create meetup (org member)
GET   /organizations/{org_id}/meetups/                           -> list meetups (org member)
GET   /organizations/{org_id}/meetups/{meetup_id}                -> get meetup (org member)
POST  /organizations/{org_id}/meetups/{meetup_id}/sync           -> sync from Mazmo (org member)
GET   /organizations/{org_id}/meetups/{meetup_id}/guests         -> list RSVP guests (org member)
POST  /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/add-walkin  -> add walk-in (org member)
POST  /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/checkin     -> check in (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{id}/undo-checkin -> undo check-in (org member)
PATCH /organizations/{org_id}/meetups/{meetup_id}/finalize       -> finalize (org admin)
PATCH /organizations/{org_id}/meetups/{meetup_id}/unfinalize     -> unfinalize (org admin)
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_org_admin, get_org_member
from app.domain_types import MazmoUserId
from app.models.models import EventLog, EventType, Guest, Meetup, MeetupRsvp, OrganizationBan, User
from app.openapi_examples.meetups_examples import (
    ADD_WALKIN_RESPONSES,
    CHECKIN_RESPONSES,
    CREATE_MEETUP_REQUEST_EXAMPLES,
    CREATE_MEETUP_RESPONSES,
    FINALIZE_MEETUP_RESPONSES,
    GET_MEETUP_RESPONSES,
    LIST_MEETUP_GUESTS_RESPONSES,
    LIST_MEETUPS_RESPONSES,
    SYNC_MEETUP_RESPONSES,
    UNDO_CHECKIN_REQUEST_EXAMPLES,
    UNDO_CHECKIN_RESPONSES,
    UNFINALIZE_MEETUP_RESPONSES,
)
from app.schemas import (
    CheckedInByPublic,
    CheckInResponse,
    GuestPublic,
    GuestWithBanPublic,
    MeetupCreate,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    MeetupListResponse,
    MeetupPublic,
    RsvpPublic,
    SyncResponse,
)
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError
from app.services.sync import sync_guests

log = structlog.get_logger(__name__)
router = APIRouter(tags=["meetups"])


# -- Helpers ------------------------------------------------------------------


def _get_meetup_or_404_in_org(session: Session, meetup_id: uuid.UUID, org_id: uuid.UUID) -> Meetup:
    """Fetch a meetup by ID, verify it belongs to org_id, or raise 404."""
    meetup = session.get(Meetup, meetup_id)
    if not meetup or meetup.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Meetup with id={meetup_id} does not exist in this organization. "
                f"List this org's meetups via GET /organizations/{org_id}/meetups/."
            ),
        )
    return meetup


def _raise_if_finalized(meetup: Meetup) -> None:
    """Raise 409 if the meetup has been finalized."""
    if meetup.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot perform this action: meetup '{meetup.name}' "
                f"was finalized on {meetup.finalized_at}. "
                f"Finalized meetups no longer accept check-ins or syncs."
            ),
        )


# -- Create meetup ------------------------------------------------------------


@router.post(
    "/organizations/{org_id}/meetups/",
    response_model=MeetupPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new meetup",
    responses=CREATE_MEETUP_RESPONSES,
)
async def create_meetup(
    org_id: uuid.UUID,
    payload: Annotated[MeetupCreate, Body(openapi_examples=CREATE_MEETUP_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
    settings: Settings = Depends(get_settings),
) -> Meetup:
    """
    Create a new meetup linked to a Mazmo event thread.

    The Mazmo URL is validated and the event date is fetched from Mazmo.
    Returns 422 if URL format is invalid, 502 if Mazmo returns an error,
    504 if Mazmo is unreachable.
    """
    mazmo_url = str(payload.mazmo_meetup_url)

    existing = session.exec(select(Meetup).where(Meetup.mazmo_meetup_url == mazmo_url)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot create meetup: a meetup with this Mazmo URL already exists. "
                f"Existing meetup: id={existing.id}, name='{existing.name}', date={existing.date}. "
                f"Each Mazmo event can only be tracked once."
            ),
        )

    try:
        async with MazmoClient(settings) as client:
            event_date = await client.fetch_meetup_date(mazmo_url)
    except MazmoNetworkError as exc:
        log.error("Mazmo network error", error=str(exc), mazmo_url=mazmo_url)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"Cannot create meetup: failed to connect to Mazmo API. "
                f"This is likely a temporary network issue. Error: {exc}. "
                f"Try again in a few moments, or check if Mazmo is experiencing an outage."
            ),
        ) from exc
    except MazmoAPIError as exc:
        log.error("Mazmo API error", error=str(exc), mazmo_url=mazmo_url)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Cannot create meetup: Mazmo API returned an error. "
                f"Frontend URL: {mazmo_url}. {exc}. "
                f"This could mean the Mazmo URL is invalid or the event doesn't exist. "
                f"Verify the URL is correct and points to a valid Mazmo event page."
            ),
        ) from exc

    meetup = Meetup(
        org_id=org_id,
        name=payload.name,
        mazmo_meetup_url=mazmo_url,
        date=event_date,
    )
    session.add(meetup)
    session.commit()
    session.refresh(meetup)
    return meetup


# -- List meetups -------------------------------------------------------------


@router.get(
    "/organizations/{org_id}/meetups/",
    response_model=MeetupListResponse,
    summary="List all meetups in this organization",
    responses=LIST_MEETUPS_RESPONSES,
)
async def list_meetups(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
) -> MeetupListResponse:
    """List all meetups for this org ordered by date descending."""
    meetups = session.exec(
        select(Meetup).where(Meetup.org_id == org_id).order_by(Meetup.date.desc())  # type: ignore[attr-defined]
    ).all()
    return MeetupListResponse(
        total=len(meetups),
        meetups=[MeetupPublic.model_validate(m) for m in meetups],
    )


# -- Get single meetup --------------------------------------------------------


@router.get(
    "/organizations/{org_id}/meetups/{meetup_id}",
    response_model=MeetupPublic,
    summary="Get a single meetup",
    responses=GET_MEETUP_RESPONSES,
)
async def get_meetup(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
) -> Meetup:
    """Get a single meetup by ID."""
    return _get_meetup_or_404_in_org(session, meetup_id, org_id)


# -- Sync guests --------------------------------------------------------------


@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/sync",
    response_model=SyncResponse,
    summary="Sync guest list from Mazmo for this meetup",
    responses=SYNC_MEETUP_RESPONSES,
)
async def sync_meetup_guests(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
    settings: Settings = Depends(get_settings),
) -> SyncResponse:
    """
    Fetches the latest RSVP list from Mazmo and upserts it into the local DB.

    Safe to call at any time:
      - New guests are inserted.
      - Existing RSVPs have their rsvp_time updated.
      - `has_arrived`, `arrival_time`, and `arrival_order` are NEVER modified.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)
    _raise_if_finalized(meetup)

    try:
        return await sync_guests(session, settings, meetup)
    except httpx.HTTPStatusError as exc:
        log.error(
            "Mazmo HTTP error during sync",
            status_code=exc.response.status_code,
            meetup_id=str(meetup_id),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Sync failed: Mazmo API returned HTTP error {exc.response.status_code}. "
                f"The event may have been deleted on Mazmo, or their API is having issues. "
                f"Check if the meetup URL is still valid on Mazmo's website."
            ),
        ) from exc
    except httpx.RequestError as exc:
        log.error(
            "Mazmo network error during sync",
            error=str(exc),
            meetup_id=str(meetup_id),
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"Sync failed: could not connect to Mazmo API. Error: {exc}. "
                f"This is likely a temporary network issue. Try again in a few moments."
            ),
        ) from exc
    except ValueError as exc:
        log.error(
            "Mazmo response parse error",
            error=str(exc),
            meetup_id=str(meetup_id),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Sync failed: Mazmo returned data in an unexpected format. Error: {exc}. "
                f"This could indicate Mazmo changed their API. Please report this issue."
            ),
        ) from exc


# -- List guests for meetup ---------------------------------------------------


@router.get(
    "/organizations/{org_id}/meetups/{meetup_id}/guests",
    response_model=MeetupGuestListResponse,
    summary="List guests RSVPed to this meetup",
    responses=LIST_MEETUP_GUESTS_RESPONSES,
)
async def list_meetup_guests(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_org_member),
) -> MeetupGuestListResponse:
    """List all guests who have RSVPed to this meetup with their RSVP state and org ban status."""
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvps = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
        .order_by(MeetupRsvp.rsvp_time)  # type: ignore[attr-defined]
    ).all()

    # Batch-load bans to avoid N+1 queries
    guest_ids = [rsvp.guest_id for rsvp in rsvps]
    banned_ids: set[int] = set()
    if guest_ids:
        bans = session.exec(
            select(OrganizationBan)
            .where(OrganizationBan.org_id == org_id)
            .where(OrganizationBan.guest_id.in_(guest_ids))  # type: ignore[union-attr]
        ).all()
        banned_ids = {int(ban.guest_id) for ban in bans}

    guests = [
        MeetupGuestPublic(
            guest=GuestWithBanPublic(
                mazmo_user_id=rsvp.guest.mazmo_user_id,
                username=rsvp.guest.username,
                displayname=rsvp.guest.displayname,
                is_banned=int(rsvp.guest_id) in banned_ids,
            ),
            rsvp=RsvpPublic.model_validate(rsvp),
        )
        for rsvp in rsvps
    ]

    return MeetupGuestListResponse(total=len(guests), guests=guests)


# -- Add walk-in guest --------------------------------------------------------


@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/add-walkin",
    response_model=MeetupGuestPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Add a walk-in guest to this meetup",
    responses=ADD_WALKIN_RESPONSES,
)
async def add_walkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> MeetupGuestPublic:
    """
    Manually add a guest who has a Mazmo profile but didn't RSVP to this event.

    Creates a MeetupRsvp row with the current time as rsvp_time.
    The guest must already exist in the system (fetched via a previous sync
    for any meetup, or visible in GET /guests/).

    Returns 404 if the guest doesn't exist in the system.
    Returns 409 if the guest already has an RSVP for this meetup.
    Returns 409 if the meetup is finalized.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)
    _raise_if_finalized(meetup)

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot add walk-in: guest mazmo_user_id={mazmo_user_id} does not exist in the system. "
                f"Register them first via POST /guests/ (username lookup) or sync a meetup they've RSVPed to."
            ),
        )

    existing = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup_id).where(MeetupRsvp.guest_id == mazmo_user_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"already has an RSVP for this meetup. "
                f"They may have RSVPed on Mazmo or been added as a walk-in previously."
            ),
        )

    rsvp = MeetupRsvp(
        meetup_id=meetup_id,
        guest_id=MazmoUserId(mazmo_user_id),
        rsvp_time=datetime.now(UTC),
        cancelled_rsvp=False,
        is_walkin=True,
    )

    event = EventLog(
        event_type=EventType.WALKIN,
        actor_id=staff.id,
        guest_id=MazmoUserId(mazmo_user_id),
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add walk-in: guest '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"already has an RSVP for this meetup (concurrent request)."
            ),
        ) from None
    session.refresh(rsvp)

    log.info(
        "Walk-in guest added",
        staff=staff.username,
        guest=guest.username,
        guest_id=mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
    )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()

    return MeetupGuestPublic(
        guest=GuestWithBanPublic(
            mazmo_user_id=guest.mazmo_user_id,
            username=guest.username,
            displayname=guest.displayname,
            is_banned=ban is not None,
        ),
        rsvp=RsvpPublic.model_validate(rsvp),
    )


# -- Check in a guest ---------------------------------------------------------


@router.post(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/checkin",
    response_model=CheckInResponse,
    summary="Check in a guest at this meetup",
    responses=CHECKIN_RESPONSES,
)
async def checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> CheckInResponse:
    """
    Mark a guest as arrived at this meetup.

    The arrival_order and arrival_time are set automatically by a database trigger.
    The staff member performing the check-in is recorded for audit purposes.
    Returns 404 if guest not RSVPed, 409 if already checked in or meetup finalized.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)
    _raise_if_finalized(meetup)

    # SELECT FOR UPDATE locks the row for the duration of this transaction.
    # Without it, two concurrent check-in requests could both read has_arrived=False,
    # both pass the check below, and both commit -- producing two CHECK_IN event log
    # entries and an ambiguous audit trail. The lock ensures the second request
    # reads the already-committed state (has_arrived=True) and correctly gets a 409.
    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .with_for_update()
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot check in: guest mazmo_user_id={mazmo_user_id} is not RSVPed. "
                f"Either: (1) they haven't RSVPed on Mazmo yet, "
                f"(2) RSVP list needs syncing - try POST /organizations/{org_id}/meetups/{meetup_id}/sync, "
                f"or (3) wrong ID - check GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot check in: guest '{rsvp.guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is already checked in. They arrived at {rsvp.arrival_time} "
                f"(arrival #{rsvp.arrival_order}). "
                f"To undo this, use PATCH /organizations/{org_id}/meetups/{meetup_id}"
                f"/guests/{mazmo_user_id}/undo-checkin."
            ),
        )

    rsvp.has_arrived = True
    rsvp.checked_in_by_id = staff.id

    event = EventLog(
        event_type=EventType.CHECK_IN,
        actor_id=staff.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()
    session.refresh(rsvp)

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Internal error: check-in succeeded but guest record "
                f"(mazmo_user_id={mazmo_user_id}) could not be fetched. "
                f"This should never happen - please report this bug. "
                f"The check-in WAS recorded in the database."
            ),
        )

    return CheckInResponse(
        guest=GuestPublic.model_validate(guest),
        arrival_order=rsvp.arrival_order,  # type: ignore[arg-type]
        arrival_time=rsvp.arrival_time,  # type: ignore[arg-type]
        checked_in_by=CheckedInByPublic.model_validate(staff),
    )


# -- Undo check-in ------------------------------------------------------------


class UndoCheckInRequest(BaseModel):
    """Request body for undoing a check-in."""

    reason: str = Field(min_length=5, max_length=500)


@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/guests/{mazmo_user_id}/undo-checkin",
    response_model=GuestPublic,
    summary="Undo a guest check-in at this meetup",
    responses=UNDO_CHECKIN_RESPONSES,
)
async def undo_checkin_guest(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    mazmo_user_id: Annotated[int, Field(gt=0, le=2_147_483_647)],
    request: Annotated[UndoCheckInRequest, Body(openapi_examples=UNDO_CHECKIN_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_member),
) -> Guest:
    """
    Undo a check-in for a guest at this meetup.

    Use this when a guest was checked in by mistake. Requires a reason
    for the audit trail. Clears has_arrived, arrival_time, arrival_order,
    and checked_in_by_id.

    Returns 404 if guest not RSVPed to this meetup.
    Returns 409 if guest is not currently checked in.
    """
    _get_meetup_or_404_in_org(session, meetup_id, org_id)

    rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup_id)
        .where(MeetupRsvp.guest_id == mazmo_user_id)
        .options(selectinload(MeetupRsvp.guest))  # type: ignore[arg-type]
    ).first()

    if not rsvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot undo check-in: guest mazmo_user_id={mazmo_user_id} is not RSVPed "
                f"to this meetup. Verify the guest ID via GET /organizations/{org_id}/meetups/{meetup_id}/guests."
            ),
        )

    if not rsvp.has_arrived:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot undo check-in: guest '{rsvp.guest.username}' "
                f"(mazmo_user_id={mazmo_user_id}) is not currently checked in. "
                f"They may have been un-checked by someone else. "
                f"See event log: GET /organizations/{org_id}/events/meetups/{meetup_id}?type=CHECK_IN,UNDO_CHECK_IN"
            ),
        )

    rsvp.has_arrived = False
    rsvp.arrival_time = None
    rsvp.arrival_order = None
    rsvp.checked_in_by_id = None

    event = EventLog(
        event_type=EventType.UNDO_CHECK_IN,
        actor_id=staff.id,
        guest_id=mazmo_user_id,
        meetup_id=meetup_id,
        org_id=org_id,
        reason=request.reason,
    )

    session.add(rsvp)
    session.add(event)
    session.commit()

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Internal error: undo succeeded but guest record "
                f"(mazmo_user_id={mazmo_user_id}) could not be fetched. "
                f"This should never happen - please report this bug. "
                f"The undo WAS recorded in the database."
            ),
        )

    log.info(
        "Check-in undone",
        staff=staff.username,
        guest=guest.username,
        guest_id=guest.mazmo_user_id,
        meetup_id=str(meetup_id),
        org_id=str(org_id),
        reason=request.reason,
    )
    return guest


# -- Finalize meetup ----------------------------------------------------------


@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/finalize",
    response_model=MeetupPublic,
    summary="Finalize a meetup (org admin only)",
    responses=FINALIZE_MEETUP_RESPONSES,
)
async def finalize_meetup(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_admin),
) -> Meetup:
    """
    Mark a meetup as finalized. No further check-ins or syncs will be allowed.

    Returns 409 if the meetup is already finalized.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)

    if meetup.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot finalize: meetup '{meetup.name}' is already finalized (finalized at {meetup.finalized_at})."
            ),
        )

    meetup.is_finalized = True
    meetup.finalized_at = datetime.now(UTC)

    event = EventLog(
        event_type=EventType.MEETUP_FINALIZED,
        actor_id=staff.id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(meetup)
    session.add(event)
    session.commit()
    session.refresh(meetup)

    log.info(
        "Meetup finalized",
        staff=staff.username,
        meetup_id=str(meetup_id),
        meetup_name=meetup.name,
        org_id=str(org_id),
    )
    return meetup


# -- Un-finalize meetup -------------------------------------------------------


@router.patch(
    "/organizations/{org_id}/meetups/{meetup_id}/unfinalize",
    response_model=MeetupPublic,
    summary="Un-finalize a meetup (org admin only)",
    responses=UNFINALIZE_MEETUP_RESPONSES,
)
async def unfinalize_meetup(
    org_id: uuid.UUID,
    meetup_id: uuid.UUID,
    session: Session = Depends(get_session),
    staff: User = Depends(get_org_admin),
) -> Meetup:
    """
    Revert a meetup's finalized status (e.g., finalized by accident).

    Returns 409 if the meetup is not finalized.
    """
    meetup = _get_meetup_or_404_in_org(session, meetup_id, org_id)

    if not meetup.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot un-finalize: meetup '{meetup.name}' is not currently finalized.",
        )

    meetup.is_finalized = False
    meetup.finalized_at = None

    event = EventLog(
        event_type=EventType.MEETUP_UNFINALIZED,
        actor_id=staff.id,
        meetup_id=meetup_id,
        org_id=org_id,
    )

    session.add(meetup)
    session.add(event)
    session.commit()
    session.refresh(meetup)

    log.info(
        "Meetup un-finalized",
        staff=staff.username,
        meetup_id=str(meetup_id),
        meetup_name=meetup.name,
        org_id=str(org_id),
    )
    return meetup
