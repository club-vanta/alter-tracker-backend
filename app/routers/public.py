"""
Public router (no auth, read-only, only the org set in PUBLIC_ALTERS_ORG_SLUG)

GET /public/alters  -> upcoming and past alters of the configured org, newest first
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.models.models import Organization
from app.openapi_examples.public_examples import LIST_PUBLIC_ALTERS_RESPONSES
from app.schemas import PublicAltersResponse
from app.services.public_alters import list_public_alters

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/public", tags=["public"])

CACHE_CONTROL = "public, max-age=300"


def _get_public_org_or_404(session: Session, settings: Settings) -> Organization:
    """Resolve the one organization exposed publicly, or raise an actionable 404."""
    slug = settings.public_alters_org_slug
    if not slug:
        log.warning("Public alters requested but PUBLIC_ALTERS_ORG_SLUG is not set", slug=slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Cannot list alters: PUBLIC_ALTERS_ORG_SLUG is not set on the server. "
                "Set it to the slug of the organization to expose and restart the backend."
            ),
        )
    org = session.exec(select(Organization).where(Organization.slug == slug)).first()
    if not org:
        log.warning("PUBLIC_ALTERS_ORG_SLUG points to a missing organization", slug=slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot list alters: no organization with slug '{slug}' exists, "
                "but PUBLIC_ALTERS_ORG_SLUG points to it. Fix the setting or create the organization."
            ),
        )
    return org


@router.get(
    "/alters",
    response_model=PublicAltersResponse,
    summary="List the public alters",
    responses=LIST_PUBLIC_ALTERS_RESPONSES,
)
async def list_alters(
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PublicAltersResponse:
    """
    List the alters of the one organization named in `PUBLIC_ALTERS_ORG_SLUG`.

    No authentication. Newest first. Never includes venue or attendee data.
    The response carries `Cache-Control: public, max-age=300`.
    """
    org = _get_public_org_or_404(session, settings)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return PublicAltersResponse(alters=list_public_alters(session, org))
