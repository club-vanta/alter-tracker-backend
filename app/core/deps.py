"""
FastAPI dependency functions for authentication and authorization.

Global dependency chain:
  get_current_user    -> validates JWT, returns User
  get_approved_user   -> calls get_current_user + checks is_approved and not is_disabled
  get_site_admin      -> calls get_approved_user + checks role == SITE_ADMIN

Per-org dependency chain (org_id comes from the path parameter):
  get_org_member(org_id) -> approved user who is a member of the org (any role), or SITE_ADMIN
  get_org_admin(org_id)  -> approved user who is ADMIN in the org, or SITE_ADMIN

Usage in a route:
  @router.post("/organizations/{org_id}/meetups/")
  async def create_meetup(
      org_id: uuid.UUID,
      staff: User = Depends(get_org_member),
      ...
  ): ...
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.models import OrgRole, PossibleRoles, User, UserOrganization

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Decode the JWT and return the matching User. Raises 401 on any failure."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user = session.exec(select(User).where(User.username == payload["sub"])).first()
    if user is None:
        raise credentials_exception

    return user


def get_approved_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensures the authenticated user has been approved and is not disabled."""
    if not current_user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval.",
        )
    if current_user.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been disabled.",
        )
    return current_user


def get_site_admin(
    current_user: User = Depends(get_approved_user),
) -> User:
    """Ensures the authenticated user has the SITE_ADMIN global role."""
    if current_user.role is None or current_user.role.name != PossibleRoles.SITE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Site admin privileges required.",
        )
    return current_user


def get_org_member(
    org_id: uuid.UUID,
    current_user: User = Depends(get_approved_user),
    session: Session = Depends(get_session),
) -> User:
    """
    Returns the current user if they are a member of org_id (any org role),
    or a SITE_ADMIN (who bypasses all org checks). Raises 403 otherwise.
    """
    if current_user.role and current_user.role.name == PossibleRoles.SITE_ADMIN:
        return current_user

    membership = session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id)
        .where(UserOrganization.org_id == org_id)
    ).first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You do not have access to this organization (org_id={org_id}). Contact a site admin to be added."
            ),
        )

    return current_user


def get_org_admin(
    org_id: uuid.UUID,
    current_user: User = Depends(get_approved_user),
    session: Session = Depends(get_session),
) -> User:
    """
    Returns the current user if they are ADMIN in org_id, or a SITE_ADMIN.
    Raises 403 if they are not a member or are only STAFF in this org.
    """
    if current_user.role and current_user.role.name == PossibleRoles.SITE_ADMIN:
        return current_user

    membership = session.exec(
        select(UserOrganization)
        .where(UserOrganization.user_id == current_user.id)
        .where(UserOrganization.org_id == org_id)
    ).first()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"You do not have access to this organization (org_id={org_id}). Contact a site admin to be added."
            ),
        )

    if membership.role != OrgRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin privileges required.",
        )

    return current_user
