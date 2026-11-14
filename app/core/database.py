from collections.abc import Callable, Generator

from sqlmodel import Session, create_engine

from app.core.config import get_settings

settings = get_settings()

# Every connection talks to Postgres in UTC, whatever the server's TimeZone
# setting. timestamptz values then come back as UTC datetimes, and naive
# values written to a timestamp column are not shifted by the server's zone.
UTC_SESSION: dict[str, str] = {"options": "-c TimeZone=UTC"}

# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True: validates connections before use (handles stale connections
# after Postgres restarts, which is common in dev with devenv).
engine = create_engine(
    settings.database_url,
    echo=settings.debug,  # SQL logging only in debug mode
    pool_pre_ping=True,
    connect_args=UTC_SESSION,
    pool_size=10,
    max_overflow=20,
)


def get_session() -> Generator[Session]:
    """FastAPI dependency that provides a database session per request."""
    with Session(engine) as session:
        yield session


type SessionFactory = Callable[[], Session]


def get_session_factory() -> SessionFactory:
    """FastAPI dependency for work that needs its own short session (overridden in tests)."""
    return lambda: Session(engine)
