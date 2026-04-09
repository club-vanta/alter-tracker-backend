from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.services.health import HealthResponse, get_health_status

router = APIRouter(tags=["meta"])


@router.get("/", include_in_schema=False)
async def root(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "about": "API for event tracker, see more at /docs , /redoc, or /openapi.json",
        "app": settings.app_name,
    }


@router.get("/ping")
async def ping() -> dict:
    return {"ping": "pong"}


# GET and HEAD are intentionally separate handlers instead of a single
# api_route(methods=["GET","HEAD"]) so that:
#   1. GET appears in Swagger with its full response model.
#   2. HEAD appears in Swagger with its own description (see below), making it
#      visible to anyone reading the docs — it's not an implementation detail.
#   3. The two responses stay independent: GET returns a JSON body, HEAD returns
#      no body (correct per RFC 9110).
@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        200: {"description": "All services healthy"},
        503: {"description": "One or more critical services unhealthy"},
    },
    summary="Check health of all dependencies",
)
async def health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """
    Comprehensive health check that verifies connectivity to:
    - **Database**: Critical - app cannot function without it
    - **Mazmo API**: Non-critical - app works but can't sync new guests

    Returns HTTP 200 if all critical services are healthy.
    Returns HTTP 503 if any critical service is unhealthy.

    The response body includes individual service status and latency.
    """
    result = await get_health_status(session, settings)

    # Return 503 if unhealthy, 200 otherwise (including degraded)
    status_code = 503 if result.status == "unhealthy" else 200

    return JSONResponse(content=result.model_dump(), status_code=status_code)


# HEAD is kept as a separate handler (instead of relying on FastAPI's automatic
# HEAD-from-GET derivation) so it can carry its own OpenAPI description.
# This endpoint is polled by UptimeRobot for uptime monitoring — it sends HEAD
# requests because it only needs the status code, not the response body.
@router.head(
    "/health",
    responses={
        200: {"description": "All services healthy"},
        503: {"description": "One or more critical services unhealthy"},
    },
    summary="Health check (no body) — used by UptimeRobot",
)
async def health_head(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """
    Same health logic as `GET /health` but returns no response body, as required
    by RFC 9110 for HEAD requests.

    **Used by UptimeRobot** for uptime monitoring. UptimeRobot sends HEAD
    requests to avoid downloading the response body on every check. Returns
    200 if healthy, 503 if any critical service is down.
    """
    result = await get_health_status(session, settings)
    status_code = 503 if result.status == "unhealthy" else 200
    # HEAD must not include a body (RFC 9110 §9.3.2) — content=None ensures that.
    return JSONResponse(content=None, status_code=status_code)
