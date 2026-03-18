from sqlmodel import SQLModel, create_engine, Session
from app.core.config import get_settings
from typing import Generator

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


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a database session per request."""
    with Session(engine) as session:
        yield session
