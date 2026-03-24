"""
FastAPI dependency functions for authentication and authorization.

Dependency chain:
  get_current_user        → validates JWT, returns User
  get_approved_user       → calls get_current_user + checks is_approved
  get_admin_user          → calls get_approved_user + checks role == ADMIN

Usage in a route:
  @router.post("/something")
  async def my_route(current_user: User = Depends(get_approved_user)):
      ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.models import PossibleRoles, User

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


def get_admin_user(
    current_user: User = Depends(get_approved_user),
) -> User:
    """Ensures the authenticated user has the 'admin' role."""
    if current_user.role is None or current_user.role.name != PossibleRoles.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user
