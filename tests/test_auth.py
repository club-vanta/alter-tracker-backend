"""Tests for the /auth router.

These tests verify the authentication flow: registration, login, and token validation.
Security is critical here - we must ensure passwords are never leaked, unapproved
users can't access the system, and tokens are properly validated.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import User
from tests.conftest import make_user

# ── Register ──────────────────────────────────────────────────────────────────


def test_register_new_account_success(client: TestClient, session: Session):
    """
    Verify that a new user can register with valid credentials.

    WHY: This is the happy path for onboarding new staff members. We check that:
    - The account is created but NOT approved (security: requires admin review)
    - The default role is STAFF (least privilege principle)
    - The password hash is never exposed in the response (security)
    """
    payload = {
        "username": "newuser",
        "password": "a-very-secure-passphrase",
    }

    resp = client.post("/auth/register", json=payload)
    data = resp.json()

    assert resp.status_code == status.HTTP_201_CREATED
    assert data["username"] == "newuser", "Username should match payload"
    assert data["is_approved"] is False, "New accounts should not be approved by default"
    assert data["role"]["name"] == "STAFF", "Default role should be STAFF"
    assert "hashed_password" not in data, "Hashed password must not leak in response"

    db_user = session.exec(select(User).where(User.username == "newuser")).first()
    assert db_user is not None, "User should be persisted in the database"


def test_register_duplicate_username_returns_409_conflict(client: TestClient, session: Session):
    """
    Verify that registering with an existing username fails.

    WHY: Usernames must be unique to avoid confusion and security issues.
    A clear 409 Conflict helps the frontend show an appropriate error message.
    """
    make_user(session, username="existing")
    resp = client.post(
        "/auth/register",
        json={
            "username": "existing",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "already taken" in resp.json()["detail"]


def test_register_username_longer_than_64_chars_returns_422_unprocessable_entity(
    client: TestClient,
):
    """
    Verify that overly long usernames are rejected.

    WHY: Prevents database issues and potential DoS via extremely long strings.
    The 64-char limit matches the database column constraint.
    """
    resp = client.post(
        "/auth/register",
        json={
            "username": "a" * 65,
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_register_password_shorter_than_8_chars_returns_422_unprocessable_entity(
    client: TestClient,
):
    """
    Verify that weak passwords are rejected at registration.

    WHY: Short passwords are trivially brute-forceable. We enforce a minimum
    length to protect staff accounts from compromise.
    """
    resp = client.post(
        "/auth/register",
        json={
            "username": "validuser",
            "password": "tooshort",
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_register_without_password_returns_422_unprocessable_entity(client: TestClient):
    """
    Verify that registration requires a password field.

    WHY: Ensures the API validates required fields. A missing password would
    cause server errors if not caught early.
    """
    resp = client.post("/auth/register", json={"username": "onlyusername"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── Login ─────────────────────────────────────────────────────────────────────


def test_login_with_valid_credentials_returns_200_ok_with_bearer_token(
    client: TestClient, session: Session
):
    """
    Verify that valid credentials produce a JWT token.

    WHY: This is the core authentication flow. The returned token is used
    for all subsequent API calls. We verify the token_type is "bearer" to
    ensure OAuth2-compatible responses.
    """
    make_user(session, username="loginuser", password="a-very-secure-passphrase")
    resp = client.post(
        "/auth/token",
        data={
            "username": "loginuser",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_with_wrong_password_returns_401_unauthorized(client: TestClient, session: Session):
    """
    Verify that wrong passwords are rejected.

    WHY: Core security requirement. We use 401 (not 403) to avoid revealing
    whether the username exists - both wrong username and wrong password
    return the same error.
    """
    make_user(session, username="loginuser2", password="a-very-secure-passphrase")
    resp = client.post(
        "/auth/token",
        data={
            "username": "loginuser2",
            "password": "wrongpassword",
        },
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_with_nonexistent_username_returns_401_unauthorized(client: TestClient):
    """
    Verify that nonexistent usernames return the same error as wrong passwords.

    WHY: Security best practice - don't reveal whether a username exists.
    Attackers can't enumerate valid usernames by checking error messages.
    """
    resp = client.post(
        "/auth/token",
        data={
            "username": "doesnotexist",
            "password": "somepassword",
        },
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_with_unapproved_account_returns_403_forbidden(client: TestClient, session: Session):
    """
    Verify that unapproved accounts cannot login even with correct credentials.

    WHY: New registrations require admin approval before access is granted.
    This prevents unauthorized access if someone discovers the registration endpoint.
    403 (not 401) because credentials ARE valid, but access is denied.
    """
    make_user(
        session,
        username="pendinguser",
        password="a-very-secure-passphrase",
        is_approved=False,
    )
    resp = client.post(
        "/auth/token",
        data={
            "username": "pendinguser",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert "pending" in resp.json()["detail"].lower()


def test_login_without_password_field_returns_422_unprocessable_entity(
    client: TestClient,
):
    """
    Verify that login requires both username and password fields.

    WHY: Validates that the OAuth2 password flow is correctly enforced.
    Missing fields should fail fast with a clear validation error.
    """
    resp = client.post("/auth/token", data={"username": "someone"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── /auth/userinfo ──────────────────────────────────────────────────────────────────


def test_userinfo_returns_200_ok_with_current_user_profile(client: TestClient, admin_headers: dict):
    """
    Verify that a valid token returns the current user's profile.

    WHY: Frontend needs to display user info (username, role) after login.
    This endpoint lets the client verify who is currently authenticated.
    """
    resp = client.get("/auth/userinfo", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["username"] == "admin"
    assert data["is_approved"] is True


def test_userinfo_without_token_returns_401_unauthorized(client: TestClient):
    """
    Verify that /userinfo requires authentication.

    WHY: User profile data should only be accessible to authenticated users.
    This protects against information disclosure.
    """
    resp = client.get("/auth/userinfo")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_with_tampered_token_returns_401_unauthorized(client: TestClient):
    """
    Verify that modified/fake tokens are rejected.

    WHY: Tokens are cryptographically signed. If someone tries to forge a token
    or modify its contents, the signature won't match and we must reject it.
    """
    resp = client.get("/auth/userinfo", headers={"Authorization": "Bearer notavalidtoken"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_with_expired_token_returns_401_unauthorized(client: TestClient, admin_user):
    """
    Verify that expired tokens are rejected.

    WHY: Tokens have an expiration time for security. Old tokens should not
    grant access - this limits the damage if a token is leaked.
    """
    from datetime import timedelta

    from app.core.security import create_access_token

    expired_token = create_access_token(
        data={"sub": admin_user.username, "role": admin_user.role.name},
        expires_delta=timedelta(seconds=-1),
    )
    resp = client.get("/auth/userinfo", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_unapproved_user_cannot_obtain_token_returns_403_forbidden(
    client: TestClient, session: Session
):
    """
    Verify that unapproved users are blocked at login, not just at /userinfo.

    WHY: Defense in depth - we block unapproved users at the token stage,
    not just when they try to use the token. This is a duplicate check
    but ensures the security boundary is at login.
    """
    make_user(session, username="unapproved2", is_approved=False)
    resp = client.post(
        "/auth/token",
        data={
            "username": "unapproved2",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_login_with_disabled_account_returns_403_forbidden(client: TestClient, session: Session):
    """
    Verify that disabled accounts cannot login even with correct credentials.

    WHY: Soft-deleted users should not be able to access the system.
    We check is_disabled at login to prevent disabled users from
    obtaining new tokens.
    """
    from datetime import UTC, datetime

    user = make_user(session, username="disableduser", password="a-very-secure-passphrase")
    user.is_disabled = True
    user.disabled_at = datetime.now(UTC)
    user.disabled_reason = "Testing disabled login"
    session.add(user)
    session.flush()

    resp = client.post(
        "/auth/token",
        data={
            "username": "disableduser",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert "disabled" in resp.json()["detail"].lower()
