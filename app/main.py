from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.middleware import RequestContextMiddleware
from app.routers import auth, events, guests, meetups, meta, organizations, staff, users

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
            "Authentication endpoints. Create an account with `POST /auth/register` "
            "(auto-approved), then login with `POST /auth/token` to get a JWT."
        ),
    },
    {
        "name": "organizations",
        "description": (
            "Organization management. Create orgs, manage memberships, and handle "
            "org-scoped guest bans. Most operations require SITE_ADMIN or org admin privileges."
        ),
    },
    {
        "name": "staff",
        "description": (
            "Staff and admin management. List, approve, disable, and manage roles "
            "for staff accounts. Most operations require SITE_ADMIN privileges."
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
        "name": "users",
        "description": "User search. Search active users by username to add them to organizations.",
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
    # Also covers LAN origins for local dev (phone testing, etc.)
    allow_origin_regex=settings.allowed_domains_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request context adds request_id to all logs
app.add_middleware(RequestContextMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(staff.router)
app.include_router(guests.router)
app.include_router(meetups.router)
app.include_router(events.router)
app.include_router(users.router)
app.include_router(meta.router)
