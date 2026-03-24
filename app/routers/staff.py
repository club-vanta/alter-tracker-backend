"""
Staff / Admin management router

GET    /staff/pending          → list unapproved accounts (admin only)
GET    /staff/                 → list all staff accounts (admin only)
PATCH  /staff/{id}/approve     → approve or revoke a staff account (admin only)
PATCH  /staff/{id}/disable     → disable a staff account (admin only)
PATCH  /staff/{id}/enable      → re-enable a disabled staff account (admin only)
PATCH  /staff/{id}/role        → promote / demote role (admin only)
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from app.core.database import get_session
from app.core.deps import get_admin_user
from app.models.models import PossibleRoles, Role, User
from app.schemas import ApproveUserRequest, DisableUserRequest, RoleRequest, UserPublic

router = APIRouter(prefix="/staff", tags=["staff"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_staff_or_404(user_id: int, session: Session) -> User:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff user not found.")
    return user


def _get_role_or_404(role: PossibleRoles, session: Session) -> Role:
    row = session.exec(select(Role).where(Role.name == role.value)).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role '{role.value}' not found in database. Run seed.",
        )
    return row


# ── List all staff ────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=list[UserPublic],
    summary="List all staff accounts (admin only)",
)
async def list_staff(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_admin_user),
) -> list[User]:
    return list(session.exec(select(User).order_by(col(User.created_at))).all())


# ── List pending approvals ────────────────────────────────────────────────────


@router.get(
    "/pending",
    response_model=list[UserPublic],
    summary="List unapproved staff accounts (admin only)",
)
async def list_pending(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_admin_user),
) -> list[User]:
    return list(
        session.exec(
            select(User)
            .where(User.is_approved == False)  # noqa: E712
            .order_by(col(User.created_at))
        ).all()
    )


# ── Approve / revoke ──────────────────────────────────────────────────────────


@router.patch(
    "/{user_id}/approve",
    response_model=UserPublic,
    summary="Approve or revoke a staff account (admin only)",
)
async def set_approval(
    user_id: int,
    body: ApproveUserRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> User:
    user = _get_staff_or_404(user_id, session)

    if user.id == admin.id and not body.is_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot revoke their own approval.",
        )

    user.is_approved = body.is_approved
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Change role ───────────────────────────────────────────────────────────────


@router.patch(
    "/{user_id}/role",
    response_model=UserPublic,
    summary="Promote or demote a staff member's role (admin only)",
)
async def set_role(
    user_id: int,
    body: RoleRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> User:
    user = _get_staff_or_404(user_id, session)

    if user.id == admin.id and body.role != PossibleRoles.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot demote themselves.",
        )

    role_row = _get_role_or_404(body.role, session)
    if role_row.id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Role row has no ID. This is a data integrity error.",
        )
    user.role_id = role_row.id
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Disable staff user ────────────────────────────────────────────────────────


@router.patch(
    "/{user_id}/disable",
    response_model=UserPublic,
    summary="Disable a staff account (admin only)",
)
async def disable_staff(
    user_id: int,
    body: DisableUserRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> User:
    """
    Disable a staff account (soft-delete).

    Disabled accounts cannot log in but their data is preserved for
    audit trails. Records who disabled the account, when, and why.
    """
    user = _get_staff_or_404(user_id, session)

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot disable their own account.",
        )

    if user.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is already disabled.",
        )

    user.is_disabled = True
    user.disabled_at = datetime.now(UTC)
    user.disabled_by_id = admin.id
    user.disabled_reason = body.reason
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Enable staff user ─────────────────────────────────────────────────────────


@router.patch(
    "/{user_id}/enable",
    response_model=UserPublic,
    summary="Re-enable a disabled staff account (admin only)",
)
async def enable_staff(
    user_id: int,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_admin_user),
) -> User:
    """
    Re-enable a previously disabled staff account.

    Clears the disable fields, allowing the user to log in again.
    """
    user = _get_staff_or_404(user_id, session)

    if not user.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not disabled.",
        )

    user.is_disabled = False
    user.disabled_at = None
    user.disabled_by_id = None
    user.disabled_reason = None
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
