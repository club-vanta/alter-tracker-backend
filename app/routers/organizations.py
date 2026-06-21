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
PATCH  /organizations/{org_id}/guests/{id}/ban           -> ban a guest in org (org admin)
PATCH  /organizations/{org_id}/guests/{id}/unban         -> unban a guest in org (org admin)
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_org_admin, get_org_member, get_site_admin
from app.domain_types import MazmoUserId
from app.models.models import EventLog, EventType, Guest, Organization, OrganizationBan, OrgRole, User, UserOrganization
from app.openapi_examples.organizations_examples import (
    ADD_MEMBER_REQUEST_EXAMPLES,
    ADD_MEMBER_RESPONSES,
    BAN_GUEST_REQUEST_EXAMPLES,
    BAN_GUEST_RESPONSES,
    CREATE_ORG_REQUEST_EXAMPLES,
    CREATE_ORG_RESPONSES,
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
    GuestPublic,
    OrgCreate,
    OrgListResponse,
    OrgMemberListResponse,
    OrgMemberPublic,
    OrgPublic,
    OrgUpdate,
)

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

    bans = session.exec(select(OrganizationBan).where(OrganizationBan.org_id == org_id)).all()

    guests = []
    for ban in bans:
        guest = session.get(Guest, ban.guest_id)
        if guest:
            guests.append(
                BannedGuestPublic(
                    mazmo_user_id=guest.mazmo_user_id,
                    username=guest.username,
                    displayname=guest.displayname,
                    banned_at=ban.banned_at,
                    banned_reason=ban.reason,
                    banned_by_id=ban.banned_by_id,
                )
            )

    return BannedGuestListResponse(total=len(guests), guests=guests)


# ── Ban guest ─────────────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}/guests/{mazmo_user_id}/ban",
    response_model=BannedGuestPublic,
    summary="Ban a guest in this organization (org admin only)",
    responses=BAN_GUEST_RESPONSES,
)
async def ban_guest(
    org_id: uuid.UUID,
    mazmo_user_id: int,
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

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot ban guest: mazmo_user_id={mazmo_user_id} does not exist in our database. "
                f"Guests are added via Mazmo sync or manually via POST /guests/."
            ),
        )

    existing_ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()
    if existing_ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot ban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is already banned in this organization. They were banned on {existing_ban.banned_at} "
                f"for reason: '{existing_ban.reason}'. "
                f"To update the ban reason, unban first via "
                f"PATCH /organizations/{org_id}/guests/{mazmo_user_id}/unban, then re-ban."
            ),
        )

    ban = OrganizationBan(
        org_id=org_id,
        guest_id=MazmoUserId(mazmo_user_id),
        banned_by_id=admin.id,
        banned_at=datetime.now(UTC),
        reason=payload.reason,
    )
    event = EventLog(
        event_type=EventType.BAN,
        actor_id=admin.id,
        guest_id=MazmoUserId(mazmo_user_id),
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
        guest=guest.username,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=str(org_id),
        reason=payload.reason,
    )

    return BannedGuestPublic(
        mazmo_user_id=guest.mazmo_user_id,
        username=guest.username,
        displayname=guest.displayname,
        banned_at=ban.banned_at,
        banned_reason=ban.reason,
        banned_by_id=ban.banned_by_id,
    )


# ── Unban guest ───────────────────────────────────────────────────────────────


@router.patch(
    "/organizations/{org_id}/guests/{mazmo_user_id}/unban",
    response_model=GuestPublic,
    summary="Unban a guest in this organization (org admin only)",
    responses=UNBAN_GUEST_RESPONSES,
)
async def unban_guest(
    org_id: uuid.UUID,
    mazmo_user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(get_org_admin),
) -> GuestPublic:
    """
    Unban a guest in this organization. Removes the ban record (history stays in event log).

    Returns 404 if the guest doesn't exist.
    Returns 409 if the guest is not currently banned in this organization.
    """
    _get_org_or_404(session, org_id)

    guest = session.get(Guest, mazmo_user_id)
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot unban guest: mazmo_user_id={mazmo_user_id} does not exist. "
                f"Double-check the ID via GET /guests/ or GET /organizations/{org_id}/guests/banned."
            ),
        )

    ban = session.exec(
        select(OrganizationBan).where(OrganizationBan.org_id == org_id).where(OrganizationBan.guest_id == mazmo_user_id)
    ).first()
    if not ban:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot unban guest: '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
                f"is not currently banned in this organization. "
                f"They may have been unbanned by another admin. "
                f"Check audit trail at GET /organizations/{org_id}/events/guests/{mazmo_user_id}."
            ),
        )

    event = EventLog(
        event_type=EventType.UNBAN,
        actor_id=admin.id,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=org_id,
    )

    session.delete(ban)
    session.add(event)
    session.commit()

    log.info(
        "Guest unbanned",
        admin=admin.username,
        guest=guest.username,
        guest_id=MazmoUserId(mazmo_user_id),
        org_id=str(org_id),
    )

    return GuestPublic.model_validate(guest)


# ── Helper ────────────────────────────────────────────────────────────────────


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
