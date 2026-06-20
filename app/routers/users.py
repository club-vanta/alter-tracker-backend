"""
Users router

GET /users/ -> search active users by username (any approved user)
"""

import structlog
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import User
from app.schemas import UserSearchResult

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/",
    response_model=list[UserSearchResult],
    summary="Search active users by username",
)
async def search_users(
    q: str = Query(default="", description="Filter by username (case-insensitive, partial match)"),
    session: Session = Depends(get_session),
    _user: User = Depends(get_approved_user),
) -> list[UserSearchResult]:
    """Return up to 20 active users matching the username query. Returns id and username only."""
    stmt = select(User).where(User.is_approved == True).where(User.is_disabled == False)  # noqa: E712
    if q:
        stmt = stmt.where(User.username.ilike(f"%{q}%"))  # type: ignore[union-attr]
    stmt = stmt.limit(20)
    users = session.exec(stmt).all()
    return [UserSearchResult(id=u.id, username=u.username) for u in users if u.id is not None]
