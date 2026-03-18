"""Tests for the /auth router."""

from fastapi import status

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import make_user


# ── Register ──────────────────────────────────────────────────────────────────


def test_register_new_account_is_pending_approval_by_default(client: TestClient):
    resp = client.post(
        "/auth/register",
        json={
            "username": "newuser",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["username"] == "newuser"
    assert data["is_approved"] is False
    assert data["role"]["name"] == "STAFF"
    assert "hashed_password" not in data


def test_register_duplicate_username_returns_409_conflict(
    client: TestClient, session: Session
):
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
    resp = client.post(
        "/auth/register",
        json={
            "username": "validuser",
            "password": "tooshort",
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_register_without_password_returns_422_unprocessable_entity(client: TestClient):
    resp = client.post("/auth/register", json={"username": "onlyusername"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── Login ─────────────────────────────────────────────────────────────────────


def test_login_with_valid_credentials_returns_200_ok_with_bearer_token(
    client: TestClient, session: Session
):
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


def test_login_with_wrong_password_returns_401_unauthorized(
    client: TestClient, session: Session
):
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
    resp = client.post(
        "/auth/token",
        data={
            "username": "doesnotexist",
            "password": "somepassword",
        },
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_with_unapproved_account_returns_403_forbidden(
    client: TestClient, session: Session
):
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
    resp = client.post("/auth/token", data={"username": "someone"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── /auth/userinfo ──────────────────────────────────────────────────────────────────


def test_userinfo_returns_200_ok_with_current_user_profile(
    client: TestClient, admin_headers: dict
):
    resp = client.get("/auth/userinfo", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["username"] == "admin"
    assert data["is_approved"] is True


def test_userinfo_without_token_returns_401_unauthorized(client: TestClient):
    resp = client.get("/auth/userinfo")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_with_tampered_token_returns_401_unauthorized(client: TestClient):
    resp = client.get(
        "/auth/userinfo", headers={"Authorization": "Bearer notavalidtoken"}
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_with_expired_token_returns_401_unauthorized(
    client: TestClient, admin_user
):
    from datetime import timedelta
    from app.core.security import create_access_token

    expired_token = create_access_token(
        data={"sub": admin_user.username},
        expires_delta=timedelta(seconds=-1),
    )
    resp = client.get(
        "/auth/userinfo", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_userinfo_unapproved_user_cannot_obtain_token_returns_403_forbidden(
    client: TestClient, session: Session
):
    make_user(session, username="unapproved2", is_approved=False)
    resp = client.post(
        "/auth/token",
        data={
            "username": "unapproved2",
            "password": "a-very-secure-passphrase",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
