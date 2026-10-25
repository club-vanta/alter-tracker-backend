"""
Organizations router

POST   /organizations/                                   -> create org (SITE_ADMIN)
GET    /organizations/                                   -> list orgs (SITE_ADMIN)
GET    /organizations/{org_id}                           -> get org (site_admin or member)
PATCH  /organizations/{org_id}                           -> update org name/slug (SITE_ADMIN)
POST   /organizations/{org_id}/members/{user_id}         -> add member (ORG_ADMIN or SITE_ADMIN)
PATCH  /organizations/{org_id}/members/{user_id}         -> change member role (ORG_ADMIN or SITE_ADMIN)
DELETE /organizations/{org_id}/members/{user_id}         -> remove member (ORG_ADMIN or SITE_ADMIN)
GET    /organizations/{org_id}/members                   -> list members (SITE_ADMIN)
GET    /organizations/{org_id}/guests/banned             -> list banned guests in org (org member)
GET    /organizations/{org_id}/guests/{id}/meetups       -> a guest's meetups in org with attendance (org member)
PATCH  /organizations/{org_id}/guests/{id}/ban           -> ban a guest in org (org admin)
PATCH  /organizations/{org_id}/guests/{id}/unban         -> unban a guest in org (org admin)
GET    /organizations/{org_id}/geocode                   -> address search over Photon (org admin)
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import StringConstraints
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_org_admin, get_org_member, get_site_admin
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestType,
    Organization,
    OrganizationBan,
    OrgRole,
    User,
    UserOrganization,
)
from app.openapi_examples._error_responses import GEOCODING_UNAVAILABLE_DETAIL
from app.openapi_examples.organizations_examples import (
    ADD_MEMBER_REQUEST_EXAMPLES,
    ADD_MEMBER_RESPONSES,
    BAN_GUEST_REQUEST_EXAMPLES,
    BAN_GUEST_RESPONSES,
    CREATE_ORG_REQUEST_EXAMPLES,
    CREATE_ORG_RESPONSES,
    GEOCODE_RESPONSES,
    GET_GUEST_MEETUPS_IN_ORG_RESPONSES,
    GET_ORG_RESPONSES,
    LIST_BANNED_IN_ORG_RESPONSES,
    LIST_ORG_MEMBERS_RESPONSES,
    LIST_ORGS_RESPONSES,
    REMOVE_MEMBER_RESPONSES,
    UNBAN_GUEST_RESPONSES,
    UPDATE_MEMBER_ROLE_REQUEST_EXAMPLES,
    UPDATE_MEMBER_ROLE_RESPONSES,
    UPDATE_ORG_REQUEST_EXAMPLES,
    UPDATE_ORG_RESPONSES,
)
from app.schemas import (
    AddOrgMemberRequest,
    BanGuestRequest,
    BannedGuestListResponse,
    BannedGuestPublic,
    GeocodeResult,
    GuestAttendanceSummary,
    GuestMazmoProfilePublic,
    GuestMeetupAttendanceListResponse,
    GuestMeetupAttendancePublic,
    GuestPublic,
    OrgCreate,
    OrgListResponse,
    OrgMemberListResponse,
    OrgMemberPublic,
    OrgPublic,
    OrgUpdate,
)
from app.services.attendance import list_guest_attendance, summarize_attendance
from app.services.geocoding import GeocodingUnavailableError, PhotonClient

log = structlog.get_logger(__name__)
router = APIRouter(tags=["organizations"])


# ── Create org ────────────────────────────────────────────────────────────────


@router.post(
    "/organizations/",
    response_model=OrgPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization",
    responses=CREATE_ORG_RESPONSES,
)
async def create_organization(
    payload: Annotated[OrgCreate, Body(openapi_examples=CREATE_ORG_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_site_admin),
) -> Organization:
    """Create a new organization. Only SITE_ADMIN can do this."""
    existing_name = session.exec(select(Organization).where(Organization.name == payload.name)).first()
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot create organization: name '{payload.name}' is already taken.",
        )

    existing_slug = session.exec(select(Organization).where(Organization.slug == payload.slug)).first()
    if existing_slug:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot create organization: slug '{payload.slug}' is already taken.",
        )

    org = Organization(name=payload.name, slug=payload.slug, created_by_id=admin.id)
    session.add(org)
    session.commit()
    session.refresh(org)

    log.info("Organization created", org_id=str(org.id), slug=org.slug, admin=admin.username)
    return org


# ── List orgs ─────────────────────────────────────────────────────────────────


@router.get(
    "/organizations/",
    response_model=OrgListResponse,
    summary="List all organizations",
    responses=LIST_ORGS_RESPONSES,
)
async def list_organizations(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_site_admin),
) -> OrgListResponse:
    """List all organizations. Only SITE_ADMIN can see all orgs."""
    orgs = session.exec(select(Organization).order_by(Organization.name)).all()  # type: ignore[attr-defined]
    return OrgListResponse(total=len(orgs), organizations=[OrgPublic.model_validate(o) for o in orgs])


# ── Get single org ────────────────────────────────────────────────────────────


@router.get(
    "/organizations/{org_id}",
    response_model=OrgPublic,
    summary="Get a single organization",
    responses=GET_ORG_RESPONSES,
)
async def get_organization(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _member: User = Depends(get_org_member),
) -> Organization:
    """Get an organization. Available to members and SITE_ADMIN."""
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {org_id} not found.",
        )
    return org


# ── Update org ───────────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}",
    response_model=OrgPublic,
    summary="Update an organization's name and/or slug",
    responses=UPDATE_ORG_RESPONSES,
)
async def update_organization(
    org_id: uuid.UUID,
    payload: Annotated[OrgUpdate, Body(openapi_examples=UPDATE_ORG_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_site_admin),
) -> Organization:
    """Update name and/or slug of an organization. Only SITE_ADMIN can do this."""
    org = _get_org_or_404(session, org_id)

    if payload.name is not None and payload.name != org.name:
        existing = session.exec(select(Organization).where(Organization.name == payload.name)).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot update organization: name '{payload.name}' is already taken.",
            )
        org.name = payload.name

    if payload.slug is not None and payload.slug != org.slug:
        existing = session.exec(select(Organization).where(Organization.slug == payload.slug)).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot update organization: slug '{payload.slug}' is already taken.",
            )
        org.slug = payload.slug

    session.add(org)
    session.commit()
    session.refresh(org)

    log.info("Organization updated", org_id=str(org_id), name=org.name, slug=org.slug, admin=admin.username)
    return org


# ── List members ──────────────────────────────────────────────────────────────


@router.get(
    "/organizations/{org_id}/members",
    response_model=OrgMemberListResponse,
    summary="List members of an organization",
    responses=LIST_ORG_MEMBERS_RESPONSES,
)
async def list_org_members(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> OrgMemberListResponse:
    """List all members of an organization with their org roles."""
    _get_org_or_404(session, org_id)
    memberships = session.exec(
        select(UserOrganization).where(UserOrganization.org_id == org_id).options(selectinload(UserOrganization.user))  # type: ignore[arg-type]
    ).all()
    return OrgMemberListResponse(
        total=len(memberships),
        members=[
            OrgMemberPublic(user_id=m.user_id, org_id=m.org_id, role=m.role, username=m.user.username)
            for m in memberships
        ],
    )


# ── Add member ────────────────────────────────────────────────────────────────


@router.post(
    "/organizations/{org_id}/members/{user_id}",
    response_model=OrgMemberPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Add a user to an organization",
    responses=ADD_MEMBER_RESPONSES,
)
async def add_org_member(
    org_id: uuid.UUID,
    user_id: int,
    payload: Annotated[AddOrgMemberRequest, Body(openapi_examples=ADD_MEMBER_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> OrgMemberPublic:
    """Add a user to an organization with a specified role (STAFF or ADMIN)."""
    _get_org_or_404(session, org_id)

    target = session.get(User, user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found.",
        )

    existing = session.exec(
        select(UserOrganization).where(UserOrganization.user_id == user_id).where(UserOrganization.org_id == org_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"User '{target.username}' is already a member of this organization "
                f"with role {existing.role}. Use PATCH to change their role."
            ),
        )

    membership = UserOrganization(user_id=user_id, org_id=org_id, role=payload.role)
    session.add(membership)
    session.commit()
    session.refresh(membership)

    log.info(
        "Org member added",
        org_id=str(org_id),
        user=target.username,
        role=payload.role,
        admin=admin.username,
    )
    return OrgMemberPublic(
        user_id=membership.user_id, org_id=membership.org_id, role=membership.role, username=target.username
    )


# ── Change member role ────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}/members/{user_id}",
    response_model=OrgMemberPublic,
    summary="Change a member's org role",
    responses=UPDATE_MEMBER_ROLE_RESPONSES,
)
async def update_org_member_role(
    org_id: uuid.UUID,
    user_id: int,
    payload: Annotated[AddOrgMemberRequest, Body(openapi_examples=UPDATE_MEMBER_ROLE_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> OrgMemberPublic:
    """Change a member's role within an organization (STAFF <-> ADMIN)."""
    membership = session.exec(
        select(UserOrganization).where(UserOrganization.user_id == user_id).where(UserOrganization.org_id == org_id)
    ).first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} is not a member of organization {org_id}.",
        )

    membership.role = payload.role
    session.add(membership)
    session.commit()
    session.refresh(membership)

    target = session.get(User, user_id)
    username = target.username if target else str(user_id)

    log.info(
        "Org member role updated",
        org_id=str(org_id),
        user_id=user_id,
        new_role=payload.role,
        admin=admin.username,
    )
    return OrgMemberPublic(
        user_id=membership.user_id, org_id=membership.org_id, role=membership.role, username=username
    )


# ── Remove member ─────────────────────────────────────────────────────────────


@router.delete(
    "/organizations/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a user from an organization",
    responses=REMOVE_MEMBER_RESPONSES,
)
async def remove_org_member(
    org_id: uuid.UUID,
    user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> None:
    """Remove a user from an organization."""
    membership = session.exec(
        select(UserOrganization).where(UserOrganization.user_id == user_id).where(UserOrganization.org_id == org_id)
    ).first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} is not a member of organization {org_id}.",
        )

    session.delete(membership)
    session.commit()

    log.info("Org member removed", org_id=str(org_id), user_id=user_id, admin=admin.username)


# ── List banned guests ────────────────────────────────────────────────────────


@router.get(
    "/organizations/{org_id}/guests/banned",
    response_model=BannedGuestListResponse,
    summary="List all banned guests in this organization",
    responses=LIST_BANNED_IN_ORG_RESPONSES,
)
async def list_banned_guests(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _member: User = Depends(get_org_member),
) -> BannedGuestListResponse:
    """List all guests currently banned in this organization."""
    _get_org_or_404(session, org_id)

    bans = session.exec(
        select(OrganizationBan)
        .where(OrganizationBan.org_id == org_id)
        .options(selectinload(OrganizationBan.guest).selectinload(Guest.mazmo_profile))  # type: ignore[arg-type]
    ).all()

    guests = [_to_banned_guest_public(ban.guest, ban) for ban in bans]

    return BannedGuestListResponse(total=len(guests), guests=guests)


# -- A guest's meetups and attendance -----------------------------------------


@router.get(
    "/organizations/{org_id}/guests/{guest_id}/meetups",
    response_model=GuestMeetupAttendanceListResponse,
    summary="List the meetups of this organization a guest signed up for, with attendance",
    responses=GET_GUEST_MEETUPS_IN_ORG_RESPONSES,
)
async def list_guest_meetups(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    _member: User = Depends(get_org_member),
) -> GuestMeetupAttendanceListResponse:
    """
    Every meetup of this organization the guest signed up for on Mazmo or
    was checked in to as a walk-in, newest first, each with its attendance
    status, plus totals. Meetups of other organizations never appear.

    A meetup only counts as a NO_SHOW once it is finalized; before that a
    guest who has not arrived is UPCOMING. Cancelling is not a no-show.
    The rule lives in app.services.attendance.

    Returns 404 if the organization or the guest doesn't exist. A guest
    with no RSVPs in this organization gets an empty list.
    """
    _get_org_or_404(session, org_id)
    if not session.get(Guest, guest_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot list meetups: guest id={guest_id} does not exist in our database. "
                f"Find the guest via GET /guests/."
            ),
        )

    attendance = list_guest_attendance(session, org_id, guest_id)
    counts = summarize_attendance(a.status for a in attendance)

    return GuestMeetupAttendanceListResponse(
        total=len(attendance),
        summary=GuestAttendanceSummary.model_validate(counts),
        meetups=[
            GuestMeetupAttendancePublic(
                meetup_id=a.meetup.id,
                name=a.meetup.name,
                date=a.meetup.date,
                status=a.status,
                rsvp_time=a.rsvp.rsvp_time,
                arrival_time=a.rsvp.arrival_time,
                guest_type=GuestType(a.rsvp.guest_type),
            )
            for a in attendance
        ],
    )


# ── Ban guest ─────────────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}/guests/{guest_id}/ban",
    response_model=BannedGuestPublic,
    summary="Ban a guest in this organization (org admin only)",
    responses=BAN_GUEST_RESPONSES,
)
async def ban_guest(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    payload: Annotated[BanGuestRequest, Body(openapi_examples=BAN_GUEST_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> BannedGuestPublic:
    """
    Ban a guest in this organization. Records the admin who performed the ban and the reason.

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is already banned in this organization.
    """
    _get_org_or_404(session, org_id)

    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot ban guest: id={guest_id} does not exist in our database. "
                f"Guests are added via Mazmo sync or manually via POST /guests/mazmo or POST /guests/manual."
            ),
        )

    existing_ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()
    if existing_ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot ban guest: '{guest.displayname}' (id={guest_id}) "
                f"is already banned in this organization. They were banned on {existing_ban.banned_at} "
                f"for reason: '{existing_ban.reason}'. "
                f"To update the ban reason, unban first via "
                f"PATCH /organizations/{org_id}/guests/{guest_id}/unban, then re-ban."
            ),
        )

    ban = OrganizationBan(
        org_id=org_id,
        guest_id=guest_id,
        banned_by_id=admin.id,
        banned_at=datetime.now(UTC),
        reason=payload.reason,
    )
    event = EventLog(
        event_type=EventType.BAN,
        actor_id=admin.id,
        guest_id=guest_id,
        org_id=org_id,
        reason=payload.reason,
    )

    session.add(ban)
    session.add(event)
    session.commit()
    session.refresh(ban)

    log.info(
        "Guest banned",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        org_id=str(org_id),
        reason=payload.reason,
    )

    return _to_banned_guest_public(guest, ban)


# ── Unban guest ───────────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}/guests/{guest_id}/unban",
    response_model=GuestPublic,
    summary="Unban a guest in this organization (org admin only)",
    responses=UNBAN_GUEST_RESPONSES,
)
async def unban_guest(
    org_id: uuid.UUID,
    guest_id: uuid.UUID,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> GuestPublic:
    """
    Unban a guest in this organization. Removes the ban record (history stays in event log).

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently banned in this organization.
    """
    _get_org_or_404(session, org_id)

    guest = session.get(Guest, guest_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot unban guest: id={guest_id} does not exist. "
                f"Double-check the ID via GET /guests/ or GET /organizations/{org_id}/guests/banned."
            ),
        )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == guest_id)
    ).first()
    if not ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unban guest: '{guest.displayname}' (id={guest_id}) "
                f"is not currently banned in this organization. "
                f"They may have been unbanned by another admin. "
                f"Check audit trail at GET /organizations/{org_id}/events/guests/{guest_id}."
            ),
        )

    event = EventLog(
        event_type=EventType.UNBAN,
        actor_id=admin.id,
        guest_id=guest_id,
        org_id=org_id,
    )

    session.delete(ban)
    session.add(event)
    session.commit()

    log.info(
        "Guest unbanned",
        admin=admin.username,
        guest=guest.displayname,
        guest_id=str(guest_id),
        org_id=str(org_id),
    )

    return GuestPublic.model_validate(guest)


# -- Address search -----------------------------------------------------------


@router.get(
    "/organizations/{org_id}/geocode",
    response_model=list[GeocodeResult],
    summary="Search addresses in Argentina to set a meetup's location",
    responses=GEOCODE_RESPONSES,
)
async def geocode_address(
    org_id: uuid.UUID,
    q: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=3, max_length=200),
        Query(description="Address text to search for"),
    ],
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
    settings: Settings = Depends(get_settings),
) -> list[GeocodeResult]:
    """
    Proxies the query to Photon (OpenStreetMap data), biased toward Buenos
    Aires, and returns at most 5 matches in Argentina, best first. Results
    in other countries are dropped. The query is trimmed before the length
    check, so 3 to 200 characters of real text are required.

    Photon is called at most once per second per process; concurrent
    searches wait their turn. Returns 502 if Photon times out or fails;
    setting a location by hand is unaffected.
    """
    _get_org_or_404(session, org_id)
    # The session (one pooled connection) stays checked out during the limiter
    # wait and the Photon call. Acceptable: org admins only, the UI debounces
    # searches, and the call is bounded by the 5 s timeout. Releasing it early
    # would mean rollback/close on the request session, which the test
    # fixtures' shared transaction does not tolerate cleanly.
    try:
        async with PhotonClient(settings) as client:
            return await client.search(q)
    except GeocodingUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=GEOCODING_UNAVAILABLE_DETAIL) from exc


# ── Helper ────────────────────────────────────────────────────────────────────


def _to_banned_guest_public(guest: Guest, ban: OrganizationBan) -> BannedGuestPublic:
    """Combine a guest's identity (incl. Mazmo profile snapshot) with its ban row."""
    return BannedGuestPublic(
        id=guest.id,
        mazmo_user_id=guest.mazmo_user_id,
        mazmo_handle=guest.mazmo_handle,
        displayname=guest.displayname,
        instagram_username=guest.instagram_username,
        mazmo_profile=GuestMazmoProfilePublic.model_validate(guest.mazmo_profile) if guest.mazmo_profile else None,
        banned_at=ban.banned_at,
        banned_reason=ban.reason,
        banned_by_id=ban.banned_by_id,
    )


def _get_org_or_404(session: Session, org_id: uuid.UUID) -> Organization:
    org = session.get(Organization, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization {org_id} not found.",
        )
    return org


# Re-export for use in other routers
__all__ = ["OrgRole", "_get_org_or_404"]
