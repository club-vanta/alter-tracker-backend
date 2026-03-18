from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.core.config import get_settings

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True: validates connections before use (handles stale connections
# after Postgres restarts, which is common in dev with devenv).
engine = create_engine(
    settings.database_url,
    echo=settings.debug,  # SQL logging only in debug mode
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


def get_session() -> Generator[Session]:
    """FastAPI dependency that provides a database session per request."""
    with Session(engine) as session:
        yield session
