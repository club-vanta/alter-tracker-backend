"""
Shared pytest fixtures.

Architecture
------------
- A single test database (`alter_event_tracker_test`) is created once per
  session and Alembic migrations are run against it.
- Each test runs inside a transaction that is rolled back at the end, so
  tests are fully isolated without needing to recreate the DB every time.
- The FastAPI app's `get_session` dependency is overridden to use the same
  transactional session, so changes made by the app are visible in the test
  and are rolled back along with everything else.
- `client` is a synchronous TestClient (no async needed in tests).
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import get_password_hash
from app.domain_types import MazmoUserId
from app.main import app
from app.models.models import (
    Guest,
    Meetup,
    MeetupRsvp,
    Organization,
    OrganizationBan,
    OrgRole,
    PossibleRoles,
    Role,
    User,
    UserOrganization,
)

settings = get_settings()

# -- Test database URL --------------------------------------------------------
TEST_DATABASE_URL = settings.database_url.replace("/alter_event_tracker", "/alter_event_tracker_test")

# URL to connect to the default postgres DB (needed to CREATE DATABASE)
ADMIN_DATABASE_URL = settings.database_url.replace("/alter_event_tracker", "/postgres")

# -- Engine (module-level, created once) --------------------------------------
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
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'alter_event_tracker_test'")).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE alter_event_tracker_test"))
    admin_engine.dispose()


# -- Session-scoped: create DB schema once ------------------------------------


@pytest.fixture(scope="session", autouse=False)
def setup_test_database():
    """
    Run once before the entire test session:
      1. Create the test DB if it doesn't exist.
      2. Drop all tables (clean slate).
      3. Recreate schema via SQLModel metadata.
      4. Seed the user_roles lookup rows and arrival_order trigger.
    """
    _ensure_test_database_exists()
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)

    # Seed roles and create the arrival_order trigger function
    with test_engine.connect() as conn:
        conn.execute(text("INSERT INTO user_roles (name) VALUES ('USER'), ('SITE_ADMIN') ON CONFLICT DO NOTHING"))
        # Create the trigger function for arrival_order (same as in migration)
        conn.execute(
            text("""
            CREATE OR REPLACE FUNCTION set_arrival_order()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.has_arrived = TRUE
                   AND (OLD.has_arrived = FALSE OR OLD.has_arrived IS NULL)
                THEN
                    NEW.arrival_time := NOW();
                    NEW.arrival_order := COALESCE(
                        (SELECT MAX(arrival_order) + 1
                         FROM meetup_rsvps
                         WHERE meetup_id = NEW.meetup_id),
                        1
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        )
        # Create trigger on meetup_rsvps if table exists
        conn.execute(
            text("""
            DROP TRIGGER IF EXISTS trg_set_arrival_order ON meetup_rsvps;
            CREATE TRIGGER trg_set_arrival_order
                BEFORE UPDATE ON meetup_rsvps
                FOR EACH ROW
                EXECUTE FUNCTION set_arrival_order();
        """)
        )
        conn.commit()

    yield

    # Teardown -- drop everything after the session
    SQLModel.metadata.drop_all(test_engine)


# -- Function-scoped: each test gets a rolled-back transaction ----------------


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
    the test session -- so the app and the test share the same transaction.
    """

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# -- Helpers to create model instances directly -------------------------------


def _get_role(session: Session, role: PossibleRoles) -> Role:
    from sqlmodel import select

    return session.exec(select(Role).where(Role.name == role.value)).one()


@pytest.fixture()
def user_role(session: Session) -> Role:
    return _get_role(session, PossibleRoles.USER)


@pytest.fixture()
def site_admin_role(session: Session) -> Role:
    return _get_role(session, PossibleRoles.SITE_ADMIN)


def make_user(
    session: Session,
    *,
    username: str = "testuser",
    password: str = "a-very-secure-passphrase",
    is_approved: bool = True,
    role: PossibleRoles = PossibleRoles.USER,
) -> User:
    """Helper to create a User directly in the test session."""
    from sqlmodel import select

    role_row = session.exec(select(Role).where(Role.name == role.value)).one()

    if role_row.id is None:
        raise Exception(
            "When making a user, the Role table had no matching role. "
            "That is an error, the role should exist before a new user is made"
        )
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


def make_org(
    session: Session,
    *,
    name: str = "Test Org",
    slug: str = "test-org",
    created_by_id: int | None = None,
) -> Organization:
    """Helper to create an Organization directly in the test session."""
    org = Organization(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        created_by_id=created_by_id,
    )
    session.add(org)
    session.flush()
    session.refresh(org)
    return org


def make_org_member(
    session: Session,
    *,
    org: Organization,
    user: User,
    role: OrgRole = OrgRole.STAFF,
) -> UserOrganization:
    """Helper to add a user as a member of an organization."""
    membership = UserOrganization(
        user_id=user.id,
        org_id=org.id,
        role=role,
    )
    session.add(membership)
    session.flush()
    session.refresh(membership)
    return membership


def make_guest(
    session: Session,
    *,
    mazmo_user_id: int | None = 1,
    mazmo_handle: str | None = "guestuser",
    displayname: str = "Guest User",
    instagram_username: str | None = None,
) -> Guest:
    """
    Helper to create a Guest (identity only) directly in the test session.

    Defaults to a Mazmo-linked guest (matches most existing tests). Pass
    mazmo_user_id=None, mazmo_handle=None for a manual (no-Mazmo) guest.
    """
    guest = Guest(
        mazmo_user_id=MazmoUserId(mazmo_user_id) if mazmo_user_id is not None else None,
        mazmo_handle=mazmo_handle,
        displayname=displayname,
        instagram_username=instagram_username,
    )
    session.add(guest)
    session.flush()
    session.refresh(guest)
    return guest


def make_meetup(
    session: Session,
    *,
    org: Organization,
    name: str = "Test Meetup",
    mazmo_meetup_url: str = "https://mazmo.net/test-community/test-meetup-123",
    date: datetime | None = None,
    requires_payment: bool = False,
) -> Meetup:
    """Helper to create a Meetup directly in the test session."""
    meetup = Meetup(
        org_id=org.id,
        name=name,
        mazmo_meetup_url=mazmo_meetup_url,
        date=date or datetime.now(UTC),
        requires_payment=requires_payment,
    )
    session.add(meetup)
    session.flush()
    session.refresh(meetup)
    return meetup


def make_rsvp(
    session: Session,
    *,
    meetup: Meetup,
    guest: Guest,
    has_arrived: bool = False,
    arrival_order: int | None = None,
    arrival_time: datetime | None = None,
    has_paid: bool = False,
    paid_at: datetime | None = None,
    paid_by_id: int | None = None,
    guest_type: str = "NORMAL",
) -> MeetupRsvp:
    """Helper to create a MeetupRsvp directly in the test session."""
    rsvp = MeetupRsvp(
        meetup_id=meetup.id,
        guest_id=guest.id,
        rsvp_time=datetime.now(UTC),
        has_arrived=has_arrived,
        arrival_order=arrival_order,
        arrival_time=arrival_time,
        has_paid=has_paid,
        paid_at=paid_at,
        paid_by_id=paid_by_id,
        guest_type=guest_type,
    )
    session.add(rsvp)
    session.flush()
    session.refresh(rsvp)
    return rsvp


def make_ban(
    session: Session,
    *,
    org: Organization,
    guest: Guest,
    banned_by: User,
    reason: str = "Test ban reason for isolation testing",
) -> OrganizationBan:
    """Helper to create an OrganizationBan directly in the test session."""
    ban = OrganizationBan(
        org_id=org.id,
        guest_id=guest.id,
        banned_by_id=banned_by.id,
        banned_at=datetime.now(UTC),
        reason=reason,
    )
    session.add(ban)
    session.flush()
    return ban


def get_auth_headers(client: TestClient, username: str, password: str) -> dict:
    """Login and return Authorization headers."""
    resp = client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# -- Common fixtures for site admin and regular users -------------------------


@pytest.fixture()
def site_admin_user(session: Session) -> User:
    return make_user(session, username="site_admin", role=PossibleRoles.SITE_ADMIN)


@pytest.fixture()
def regular_user(session: Session) -> User:
    return make_user(session, username="user", role=PossibleRoles.USER)


@pytest.fixture()
def pending_user(session: Session) -> User:
    return make_user(session, username="pending", is_approved=False, role=PossibleRoles.USER)


@pytest.fixture()
def org(session: Session) -> Organization:
    """Create a default test organization."""
    return make_org(session)


@pytest.fixture()
def staff_user(session: Session) -> User:
    """Regular user (staff capabilities come from org membership)."""
    return make_user(session, username="staff", role=PossibleRoles.USER)


@pytest.fixture()
def admin_user(session: Session) -> User:
    """Site admin user (bypasses all org checks)."""
    return make_user(session, username="admin", role=PossibleRoles.SITE_ADMIN)


@pytest.fixture()
def site_admin_headers(client: TestClient, site_admin_user: User) -> dict:
    return get_auth_headers(client, "site_admin", "a-very-secure-passphrase")


@pytest.fixture()
def admin_headers(client: TestClient, admin_user: User) -> dict:
    return get_auth_headers(client, "admin", "a-very-secure-passphrase")


@pytest.fixture()
def staff_headers(client: TestClient, staff_user: User) -> dict:
    return get_auth_headers(client, "staff", "a-very-secure-passphrase")


@pytest.fixture()
def meetup(session: Session, org: Organization) -> Meetup:
    """Create a default test meetup in the default test org."""
    return make_meetup(session, org=org)


# -- Shared Mock Data ---------------------------------------------------------

FAKE_RSVPS = {
    111: SimpleNamespace(userId=111, joinedAt=datetime(2026, 3, 17, tzinfo=UTC)),
    222: SimpleNamespace(userId=222, joinedAt=datetime(2026, 3, 17, tzinfo=UTC)),
}

FAKE_USERS = {
    111: SimpleNamespace(username="alice", displayname="Alice"),
    222: SimpleNamespace(username="bob", displayname="Bob"),
}

# --- Sync fixtures ---


@pytest.fixture
def mock_mazmo():
    """
    Automatically patches MazmoClient for any test that requests this fixture.
    Defaults to returning the successful FAKE data.

    The fetch_rsvps method now accepts a URL parameter (meetup-aware).
    """
    with patch("app.services.sync.MazmoClient") as MockClientClass:
        mock_instance = AsyncMock()

        # Default happy-path behaviour (meetup-aware - accepts URL param)
        mock_instance.fetch_rsvps.return_value = FAKE_RSVPS
        mock_instance.fetch_users.return_value = FAKE_USERS
        mock_instance.fetch_meetup_date.return_value = datetime(2026, 4, 1, tzinfo=UTC)

        # (Handling the 'async with' context manager)

        # When the 'async with' block starts, yield this exact mock object
        mock_instance.__aenter__.return_value = mock_instance

        # When the 'async with' block ends, just return None
        mock_instance.__aexit__.return_value = None

        # When someone calls MazmoClient(), give them the `mock_instance` pre-configured instance
        MockClientClass.return_value = mock_instance

        # Yield gives the mock to the test, and cleans up the patch after the test finishes
        yield mock_instance

        # Teardown happens after this line, but its implied


@pytest.fixture
def mock_mazmo_for_guests():
    """
    Patches MazmoClient for guests router tests (app.routers.guests).
    Defaults to returning a successful Mazmo user lookup.
    """
    with patch("app.routers.guests.MazmoClient") as MockClientClass:
        mock_instance = AsyncMock()

        # Default happy-path: cindydark found on Mazmo
        mock_instance.fetch_user_by_username.return_value = SimpleNamespace(
            mazmo_user_id=39119,
            username="cindydark",
            displayname="⚜️Lissandra⚜️",
        )

        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        MockClientClass.return_value = mock_instance

        yield mock_instance


@pytest.fixture
def mock_mazmo_for_meetups():
    """
    Patches MazmoClient for meetup router tests (also patches in routers.meetups).
    """
    with (
        patch("app.services.sync.MazmoClient") as SyncMockClass,
        patch("app.routers.meetups.MazmoClient") as RouterMockClass,
    ):
        mock_instance = AsyncMock()

        # Default happy-path behaviour
        mock_instance.fetch_rsvps.return_value = FAKE_RSVPS
        mock_instance.fetch_users.return_value = FAKE_USERS
        mock_instance.fetch_meetup_date.return_value = datetime(2026, 4, 1, tzinfo=UTC)

        # Context manager support
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None

        # Both patches return the same mock
        SyncMockClass.return_value = mock_instance
        RouterMockClass.return_value = mock_instance

        yield mock_instance
