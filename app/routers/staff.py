"""
Staff / Admin management router

GET    /staff/pending          → list unapproved accounts (admin only)
GET    /staff/                 → list all staff accounts (admin only)
PATCH  /staff/{id}/approve     → approve or revoke a staff account (admin only)
DELETE /staff/{id}             → delete a staff account (admin only)
PATCH  /staff/{id}/role        → promote / demote role (admin only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_admin_user
from app.models.models import PossibleRoles, Role, User
from app.schemas.schemas import ApproveUserRequest, RoleRequest, UserPublic

router = APIRouter(prefix="/staff", tags=["staff"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_staff_or_404(user_id: int, session: Session) -> User:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staff user not found."
        )
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
    return list(session.exec(select(User).order_by(User.created_at)).all())


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
            .order_by(User.created_at)
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
    user.role_id = role_row.id
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Delete staff user ─────────────────────────────────────────────────────────


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a staff account (admin only)",
)
async def delete_staff(
    user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
) -> None:
    user = _get_staff_or_404(user_id, session)

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot delete their own account.",
        )

    session.delete(user)
    session.commit()
