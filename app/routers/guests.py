"""
Guests router

POST /api/guests/sync          → pull latest RSVP list from Mazmo (approved staff+)
GET  /api/guests/              → list/search guests (approved staff+)      [NOT YET IMPLEMENTED]
POST /api/guests/{id}/checkin  → mark a guest as arrived                   [NOT YET IMPLEMENTED]
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.deps import get_approved_user
from app.models.models import User
from app.schemas.schemas import SyncResponse
from app.services.sync import sync_guests

log = logging.getLogger(__name__)
router = APIRouter(prefix="/guests", tags=["guests"])


# ── Sync ──────────────────────────────────────────────────────────────────────


@router.post(
    "/sync",
    response_model=SyncResponse,
    summary="Refresh guest list from Mazmo (never overwrites check-in data)",
)
async def sync(
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),
    settings: Settings = Depends(get_settings),
) -> SyncResponse:
    """
    Fetches the latest RSVP list from Mazmo and upserts it into the local DB.

    Safe to call at any time:
      - New guests are inserted.
      - Already-known guests are silently skipped (DO NOTHING).
      - `has_arrived`, `arrival_time`, and `arrival_order` are NEVER modified.
    """
    try:
        return await sync_guests(session, settings)
    except httpx.HTTPStatusError as exc:
        log.error("Mazmo HTTP error during sync: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mazmo API returned an error: {exc}",
        ) from exc

    except httpx.RequestError as exc:
        log.error("Mazmo network error during sync: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Could not reach Mazmo API: {exc}",
        ) from exc

    except ValueError as exc:
        log.error("Mazmo response parse error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Received an invalid data format from the Mazmo API:" + str(exc),
        ) from exc


# ── Guest list + check-in (NOT YET IMPLEMENTED placeholders) ─────────────────────────────


# TODO:
@router.get("/", summary="List / search guests")
async def list_guests(
    _staff: User = Depends(get_approved_user),
) -> dict:
    return {"detail": "NOT YET IMPLEMENTED"}


# TODO:
@router.post("/{mazmo_user_id}/checkin", summary="Check in a guest")
async def checkin(
    mazmo_user_id: int,
    _staff: User = Depends(get_approved_user),
) -> dict:
    return {"detail": "NOT YET IMPLEMENTED"}
