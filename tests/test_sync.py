"""Tests for the guest sync endpoint. Mazmo HTTP client is always mocked."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import Guest
from tests.conftest import make_guest

FAKE_RSVPS = {
    111: type("R", (), {"userId": 111, "joinedAt": datetime(2026, 3, 17, tzinfo=UTC)})(),
    222: type("R", (), {"userId": 222, "joinedAt": datetime(2026, 3, 17, tzinfo=UTC)})(),
}

FAKE_USERS = {
    111: type("U", (), {"username": "alice", "displayname": "Alice"})(),
    222: type("U", (), {"username": "bob", "displayname": "Bob"})(),
}


def mock_mazmo_client(rsvps=None, users=None, rsvp_error=None, user_error=None):
    mock = AsyncMock()
    if rsvp_error:
        mock.fetch_rsvps.side_effect = rsvp_error
    else:
        mock.fetch_rsvps.return_value = FAKE_RSVPS if rsvps is None else rsvps
    if user_error:
        mock.fetch_users.side_effect = user_error
    else:
        mock.fetch_users.return_value = FAKE_USERS if users is None else users
    cm = AsyncMock()
    cm.__aenter__.return_value = mock
    cm.__aexit__.return_value = None
    return cm


# ── Sync behaviour ────────────────────────────────────────────────────────────


def test_sync_inserts_all_rsvpd_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session
):
    with patch("app.services.sync.MazmoClient", return_value=mock_mazmo_client()):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 2
    assert data["skipped"] == 0
    assert data["total_in_db"] == 2
    usernames = {g.username for g in session.exec(select(Guest)).all()}
    assert usernames == {"alice", "bob"}


def test_sync_skips_existing_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session
):
    make_guest(session, mazmo_user_id=111, username="alice")

    with patch("app.services.sync.MazmoClient", return_value=mock_mazmo_client()):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 1
    assert data["skipped"] == 1
    assert data["total_in_db"] == 2


def test_sync_does_not_overwrite_checkin_data_of_arrived_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session
):
    alice = make_guest(session, mazmo_user_id=111, username="alice", has_arrived=True)
    alice.arrival_order = 1
    alice.arrival_time = datetime(2026, 3, 17, 22, 0, tzinfo=UTC)
    session.add(alice)
    session.flush()

    with patch("app.services.sync.MazmoClient", return_value=mock_mazmo_client()):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(alice)
    assert alice.has_arrived is True
    assert alice.arrival_order == 1


def test_sync_with_empty_rsvp_list_returns_200_ok_with_zero_counts(
    client: TestClient, admin_headers: dict
):
    with patch("app.services.sync.MazmoClient", return_value=mock_mazmo_client(rsvps={})):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 0
    assert data["skipped"] == 0


def test_sync_is_accessible_by_regular_staff_returns_200_ok(
    client: TestClient, staff_headers: dict
):
    with patch("app.services.sync.MazmoClient", return_value=mock_mazmo_client(rsvps={})):
        resp = client.post("/guests/sync", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK


# ── Auth guards ───────────────────────────────────────────────────────────────


def test_sync_without_token_returns_401_unauthorized(client: TestClient):
    resp = client.post("/guests/sync")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Mazmo API error handling ──────────────────────────────────────────────────


def test_sync_when_mazmo_returns_503_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict
):
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("503", request=request, response=response)

    with patch(
        "app.services.sync.MazmoClient",
        return_value=mock_mazmo_client(rsvp_error=error),
    ):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


def test_sync_when_mazmo_is_unreachable_responds_with_504_gateway_timeout(
    client: TestClient, admin_headers: dict
):
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    error = httpx.ConnectError("Connection refused", request=request)

    with patch(
        "app.services.sync.MazmoClient",
        return_value=mock_mazmo_client(rsvp_error=error),
    ):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_sync_when_mazmo_returns_unexpected_shape_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict
):
    with patch(
        "app.services.sync.MazmoClient",
        return_value=mock_mazmo_client(rsvp_error=ValueError("Unexpected shape")),
    ):
        resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
