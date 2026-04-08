from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.logging import setup_logging
from app.middleware import RequestContextMiddleware
from app.routers import auth, events, guests, meetups, staff
from app.services.health import HealthResponse, get_health_status

settings = get_settings()

# ── Logging ───────────────────────────────────────────────────────────────────
# Must be configured at module level, before uvicorn sets up its own logging
setup_logging(json_logs=settings.json_logs, log_level=settings.log_level)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once on startup, then once on shutdown (after yield)."""
    # === Integración con Prometheus
    # See https://github.com/trallnag/prometheus-fastapi-instrumentator?tab=readme-ov-file#exposing-endpoint
    # This here accepts the same arguments any FastAPI endpoint does, i saw it in the source code :D
    instrumentator.expose(
        app,
        include_in_schema=True,
        tags=["internal_usage"],
        summary="Prometheus metrics",
        response_description="Returns the metrics collected by Prometheus instrumentator",
    )
    yield
    # Clean shutdown hooks go here if needed


# ── App factory ───────────────────────────────────────────────────────────────

tags_metadata = [
    {
        "name": "auth",
        "description": (
            "Authentication endpoints. Create an account with `POST /auth/register`, "
            "then ask an admin to approve it via `PATCH /staff/{id}/approve`. "
            "Once approved, login with `POST /auth/token` to get a JWT."
        ),
    },
    {
        "name": "staff",
        "description": (
            "Staff and admin management. List, approve, disable, and manage roles "
            "for staff accounts. Most operations require admin privileges."
        ),
    },
    {
        "name": "guests",
        "description": (
            "Guest identity and ban management. View all known guests, manage ban status. "
            "Note: meetup-specific guest operations (sync, check-in) are under `meetups` tag."
        ),
    },
    {
        "name": "meetups",
        "description": (
            "Meetup operations. Create meetups, sync guest lists from Mazmo, view RSVPs, "
            "and perform check-ins. Requires approved staff access."
        ),
    },
    {
        "name": "events",
        "description": (
            "Audit event log. These are internal system events logged for auditing, "
            "not to be confused with Mazmo 'eventos' (meetups)."
        ),
    },
    {
        "name": "meta",
        "description": "Service metadata endpoints: health checks, root info, and ping.",
    },
    {
        "name": "internal_usage",
        "description": "Internal endpoints for monitoring and observability (Prometheus metrics).",
    },
]


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

# Integración con Prometheus
# see https://github.com/trallnag/prometheus-fastapi-instrumentator
instrumentator = Instrumentator().instrument(app)

# ── Middleware ────────────────────────────────────────────────────────────────
# Order matters: middleware is applied in reverse order of registration
# (last registered = first to process request)

# CORS must be outermost to handle preflight requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server (default)
        "http://localhost:5200",  # Vite dev server (alt — when 5173 is taken)
        "http://localhost:4173",  # Vite preview
    ],
    # Covers club-vanta.com and any subdomain (e.g. app.club-vanta.com)
    allow_origin_regex=r"https://(.*\.)?club-vanta\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request context adds request_id to all logs
app.add_middleware(RequestContextMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(staff.router)
app.include_router(guests.router)
app.include_router(meetups.router)
app.include_router(events.router)


# ── Root---------─────────────────────────────────────────────────────────────
@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "about": "API for event tracker, see more at /docs , /redoc, or /openapi.json",
        "app": settings.app_name,
    }


# ── Health check ─────────────────────────────────────────────────────────────
# GET and HEAD are intentionally separate handlers instead of a single
# api_route(methods=["GET","HEAD"]) so that:
#   1. GET appears in Swagger with its full response model.
#   2. HEAD appears in Swagger with its own description (see below), making it
#      visible to anyone reading the docs — it's not an implementation detail.
#   3. The two responses stay independent: GET returns a JSON body, HEAD returns
#      no body (correct per RFC 9110).
@app.get(
    "/health",
    tags=["meta"],
    response_model=HealthResponse,
    responses={
        200: {"description": "All services healthy"},
        503: {"description": "One or more critical services unhealthy"},
    },
    summary="Check health of all dependencies",
)
async def health(
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
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
@app.head(
    "/health",
    tags=["meta"],
    responses={
        200: {"description": "All services healthy"},
        503: {"description": "One or more critical services unhealthy"},
    },
    summary="Health check (no body) — used by UptimeRobot",
)
async def health_head(
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
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


# ── Ping --------─────────────────────────────────────────────────────────────
@app.get("/ping", tags=["meta"])
async def ping() -> dict:
    return {"ping": "pong"}
