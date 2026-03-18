"""
Auth router

POST /auth/register  → create a new staff account (unapproved by default)
POST /auth/token     → OAuth2 password flow – returns a JWT
GET  /auth/userinfo        → returns the currently logged-in user's profile
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_approved_user
from app.core.security import (
    JWTPayload,
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.models.models import PossibleRoles, Role, User
from app.schemas.schemas import StaffRegisterRequest, UserPublic, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Register ──────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new staff account (pending admin approval)",
)
async def register(
    body: StaffRegisterRequest,
    session: Session = Depends(get_session),
) -> User:
    existing = session.exec(select(User).where(User.username == body.username)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken.",
        )

    # Look up the STAFF role row — must exist (seeded at startup)
    staff_role = session.exec(
        select(Role).where(Role.name == PossibleRoles.STAFF)
    ).first()
    if not staff_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Role table not seeded. Contact an administrator.",
        )

    user = User(
        username=body.username,
        hashed_password=get_password_hash(body.password),
        is_approved=False,
        role_id=staff_role.id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Login (token) ─────────────────────────────────────────────────────────────


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Login – returns a JWT bearer token",
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = session.exec(select(User).where(User.username == form_data.username)).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval. Please try again later.",
        )

    token = create_access_token(
        JWTPayload(
            sub=user.username,
            role=user.role.name if user.role else PossibleRoles.STAFF,
            exp=None,  # type: ignore[typeddict-item]  # set inside create_access_token
        )
    )
    return TokenResponse(access_token=token)


# ── Current user ──────────────────────────────────────────────────────────────


@router.get(
    "/userinfo",
    response_model=UserPublic,
    summary="Return the currently authenticated user's profile",
)
async def get_me(
    current_user: User = Depends(get_approved_user),
) -> User:
    return current_user
