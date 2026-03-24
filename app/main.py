from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, events, guests, meetups, staff

settings = get_settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once on startup, then once on shutdown (after yield)."""
    yield
    # Clean shutdown hooks go here if needed


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production this list will contain only the Cloudflare-fronted frontend URL.
# For local dev we allow the Vite dev server origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:4173",  # Vite preview
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


# ── Ping --------─────────────────────────────────────────────────────────────
@app.get("/ping", tags=["meta"])
async def ping() -> dict:
    return {"ping": "pong"}
