"""
Auth router

POST /auth/register              → create a new staff account (unapproved by default)
POST /auth/token                 → OAuth2 password flow - returns a JWT
GET  /auth/userinfo              → returns the currently logged-in user's profile
POST /auth/verify-recovery-code  → check a 6-digit recovery code (no auth required)
POST /auth/reset-password        → reset password using a valid recovery code (no auth required)
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_approved_user
from app.core.security import (
    JWTPayload,
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.models.models import PossibleRoles, Role, User, UserOrganization
from app.openapi_examples.auth_examples import (
    REGISTER_REQUEST_EXAMPLES,
    REGISTER_RESPONSES,
    TOKEN_RESPONSES,
    USERINFO_RESPONSES,
)
from app.schemas import (
    ResetPasswordRequest,
    ResetPasswordResponse,
    StaffRegisterRequest,
    TokenResponse,
    UserPublic,
    VerifyRecoveryCodeRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Register ──────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new staff account (pending admin approval)",
    responses=REGISTER_RESPONSES,
)
async def register(
    body: Annotated[StaffRegisterRequest, Body(openapi_examples=REGISTER_REQUEST_EXAMPLES)],
    session: Session = Depends(get_session),
) -> User:
    existing = session.exec(select(User).where(User.username == body.username)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken.",
        )

    # Look up the USER role row — must exist (seeded at startup)
    staff_role = session.exec(select(Role).where(Role.name == PossibleRoles.USER)).first()
    if not staff_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Role table not seeded. Contact an administrator.",
        )

    if staff_role.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Role row has no ID. This is a data integrity error. Contact an administrator",
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
    summary="Login - returns a JWT bearer token",
    responses=TOKEN_RESPONSES,
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

    if user.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been disabled.",
        )

    token = create_access_token(
        JWTPayload(
            sub=user.username,
            role=user.role.name if user.role else PossibleRoles.USER,
            exp=None,  # type: ignore[typeddict-item]  # set inside create_access_token
        )
    )
    return TokenResponse(access_token=token)


# ── Current user ──────────────────────────────────────────────────────────────


@router.get(
    "/userinfo",
    response_model=UserPublic,
    summary="Return the currently authenticated user's profile",
    responses=USERINFO_RESPONSES,
)
async def get_me(
    current_user: User = Depends(get_approved_user),
    session: Session = Depends(get_session),
) -> UserPublic:
    user = session.exec(
        select(User)
        .where(User.id == current_user.id)
        .options(selectinload(User.org_memberships).selectinload(UserOrganization.org))  # type: ignore[arg-type]
    ).one()
    result = UserPublic.model_validate(user)
    if user.role and user.role.name == PossibleRoles.SITE_ADMIN:
        result.org_memberships = []
    return result


# ── Recovery code helpers ─────────────────────────────────────────────────────

_RECOVERY_CODE_TTL = timedelta(hours=72)
_INVALID_CODE_DETAIL = "Código inválido o expirado."


def _validate_code(user: User, code: str) -> bool:
    if not user.recovery_code or user.recovery_code_used:
        return False
    if user.recovery_code_created_at is None:
        return False
    if datetime.now(UTC) - user.recovery_code_created_at > _RECOVERY_CODE_TTL:
        return False
    return user.recovery_code == code


def _get_user_for_recovery(username: str, session: Session) -> User:
    """Return user by username, raising a generic 400 on miss (no user enumeration)."""
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_CODE_DETAIL,
        )
    return user


# ── Verify recovery code ──────────────────────────────────────────────────────


@router.post(
    "/verify-recovery-code",
    summary="Verify a 6-digit recovery code without consuming it",
    status_code=status.HTTP_200_OK,
)
async def verify_recovery_code(
    body: VerifyRecoveryCodeRequest,
    session: Session = Depends(get_session),
) -> dict:
    """
    Check that a recovery code is valid and not yet expired or used.

    Does NOT mark the code as used, so closing the tab and retrying still works.
    Returns a generic error on invalid username to avoid user enumeration.
    """
    user = _get_user_for_recovery(body.username, session)
    if not _validate_code(user, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_CODE_DETAIL,
        )
    return {"ok": True}


# ── Reset password ────────────────────────────────────────────────────────────


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Reset a user's password using a valid recovery code",
    status_code=status.HTTP_200_OK,
)
async def reset_password(
    body: ResetPasswordRequest,
    session: Session = Depends(get_session),
) -> ResetPasswordResponse:
    """
    Reset the user's password if the provided recovery code is valid.

    Marks the code as used after a successful reset so it cannot be reused.
    Returns a generic error on invalid username to avoid user enumeration.
    """
    user = _get_user_for_recovery(body.username, session)
    if not _validate_code(user, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_CODE_DETAIL,
        )

    user.hashed_password = get_password_hash(body.new_password)
    user.recovery_code_used = True
    session.add(user)
    session.commit()

    return ResetPasswordResponse(username=user.username)
