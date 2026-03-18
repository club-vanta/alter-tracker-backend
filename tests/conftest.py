"""
Shared pytest fixtures.

Architecture
────────────
- A single test database (`alter_event_tracker_test`) is created once per
  session and Alembic migrations are run against it.
- Each test runs inside a transaction that is rolled back at the end, so
  tests are fully isolated without needing to recreate the DB every time.
- The FastAPI app's `get_session` dependency is overridden to use the same
  transactional session, so changes made by the app are visible in the test
  and are rolled back along with everything else.
- `client` is a synchronous TestClient (no async needed in tests).
"""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import get_password_hash
from app.main import app
from app.models.models import Guest, PossibleRoles, Role, User

settings = get_settings()

# ── Test database URL ─────────────────────────────────────────────────────────
TEST_DATABASE_URL = settings.database_url.replace(
    "/alter_event_tracker", "/alter_event_tracker_test"
)

# URL to connect to the default postgres DB (needed to CREATE DATABASE)
ADMIN_DATABASE_URL = settings.database_url.replace("/alter_event_tracker", "/postgres")

# ── Engine (module-level, created once) ───────────────────────────────────────
test_engine = create_engine(TEST_DATABASE_URL, echo=False)


def _ensure_test_database_exists() -> None:
    """
    Creates the test database if it doesn't exist.
    Must connect to a different DB (postgres) to issue CREATE DATABASE,
    since you can't create a DB inside a transaction.
    """
    admin_engine = create_engine(
        ADMIN_DATABASE_URL,
        echo=False,
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'alter_event_tracker_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE alter_event_tracker_test"))
    admin_engine.dispose()


# ── Session-scoped: create DB schema once ────────────────────────────────────


@pytest.fixture(scope="session", autouse=False)
def setup_test_database():
    """
    Run once before the entire test session:
      1. Create the test DB if it doesn't exist.
      2. Drop all tables (clean slate).
      3. Recreate schema via SQLModel metadata.
      4. Seed the user_roles lookup rows and arrival_order sequence.
    """
    _ensure_test_database_exists()
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)

    # Seed roles and arrival_order sequence
    with test_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO user_roles (name) VALUES ('STAFF'), ('ADMIN') ON CONFLICT DO NOTHING")
        )
        conn.execute(
            text("CREATE SEQUENCE IF NOT EXISTS arrival_order_seq START 1 INCREMENT 1 NO CYCLE")
        )
        conn.commit()

    yield

    # Teardown — drop everything after the session
    SQLModel.metadata.drop_all(test_engine)


# ── Function-scoped: each test gets a rolled-back transaction ─────────────────


@pytest.fixture()
def session(setup_test_database):
    """
    Provides a database session that wraps each test in a transaction.
    The transaction is always rolled back, keeping tests fully isolated.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    test_session = Session(bind=connection)

    yield test_session

    test_session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(session: Session):
    """
    FastAPI TestClient with the `get_session` dependency overridden to use
    the test session — so the app and the test share the same transaction.
    """

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers to create model instances directly ────────────────────────────────


def _get_role(session: Session, role: PossibleRoles) -> Role:
    from sqlmodel import select

    return session.exec(select(Role).where(Role.name == role.value)).one()


@pytest.fixture()
def staff_role(session: Session) -> Role:
    return _get_role(session, PossibleRoles.STAFF)


@pytest.fixture()
def admin_role(session: Session) -> Role:
    return _get_role(session, PossibleRoles.ADMIN)


def make_user(
    session: Session,
    *,
    username: str = "testuser",
    password: str = "a-very-secure-passphrase",
    is_approved: bool = True,
    role: PossibleRoles = PossibleRoles.STAFF,
) -> User:
    """Helper to create a User directly in the test session."""
    from sqlmodel import select

    role_row = session.exec(select(Role).where(Role.name == role.value)).one()
    user = User(
        username=username,
        hashed_password=get_password_hash(password),
        is_approved=is_approved,
        role_id=role_row.id,
    )
    session.add(user)
    session.flush()  # get the ID without committing
    session.refresh(user)
    return user


def make_guest(
    session: Session,
    *,
    mazmo_user_id: int = 1,
    username: str = "guestuser",
    displayname: str = "Guest User",
    has_arrived: bool = False,
) -> Guest:
    """Helper to create a Guest directly in the test session."""
    from datetime import datetime

    guest = Guest(
        mazmo_user_id=mazmo_user_id,
        username=username,
        displayname=displayname,
        rsvp_time=datetime.now(UTC),
        has_arrived=has_arrived,
    )
    session.add(guest)
    session.flush()
    session.refresh(guest)
    return guest


def get_auth_headers(client: TestClient, username: str, password: str) -> dict:
    """Login and return Authorization headers."""
    resp = client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── Common fixtures for admin and staff users ─────────────────────────────────


@pytest.fixture()
def admin_user(session: Session) -> User:
    return make_user(session, username="admin", role=PossibleRoles.ADMIN)


@pytest.fixture()
def staff_user(session: Session) -> User:
    return make_user(session, username="staff", role=PossibleRoles.STAFF)


@pytest.fixture()
def pending_user(session: Session) -> User:
    return make_user(session, username="pending", is_approved=False, role=PossibleRoles.STAFF)


@pytest.fixture()
def admin_headers(client: TestClient, admin_user: User) -> dict:
    return get_auth_headers(client, "admin", "a-very-secure-passphrase")


@pytest.fixture()
def staff_headers(client: TestClient, staff_user: User) -> dict:
    return get_auth_headers(client, "staff", "a-very-secure-passphrase")
