"""Tests for the guest sync endpoint. Mazmo HTTP client is always mocked."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import Guest
from tests.conftest import make_guest

# ── Sync behaviour ────────────────────────────────────────────────────────────


def test_sync_inserts_all_rsvpd_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 2
    assert data["skipped"] == 0
    assert data["total_in_db"] == 2
    usernames = {g.username for g in session.exec(select(Guest)).all()}
    assert usernames == {"alice", "bob"}


def test_sync_skips_existing_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    make_guest(session, mazmo_user_id=111, username="alice")

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 1
    assert data["skipped"] == 1
    assert data["total_in_db"] == 2


def test_sync_does_not_overwrite_checkin_data_of_arrived_guests_returns_200_ok(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    alice = make_guest(session, mazmo_user_id=111, username="alice", has_arrived=True)
    alice.arrival_order = 1
    alice.arrival_time = datetime(2026, 3, 17, 22, 0, tzinfo=UTC)
    session.add(alice)
    session.flush()

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(alice)
    assert alice.has_arrived is True
    assert alice.arrival_order == 1


def test_sync_with_empty_rsvp_list_returns_200_ok_with_zero_counts(
    client: TestClient, admin_headers: dict, mock_mazmo: AsyncMock
):

    mock_mazmo.fetch_rsvps.return_value = {}

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["inserted"] == 0
    assert data["skipped"] == 0


def test_sync_is_accessible_by_regular_staff_returns_200_ok(
    client: TestClient, staff_headers: dict, mock_mazmo: AsyncMock
):
    resp = client.post("/guests/sync", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK


# ── Auth guards ───────────────────────────────────────────────────────────────


def test_sync_without_token_returns_401_unauthorized(client: TestClient):
    resp = client.post("/guests/sync")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Mazmo API error handling ──────────────────────────────────────────────────


def test_sync_when_mazmo_returns_503_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict, mock_mazmo: AsyncMock
):
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("503", request=request, response=response)
    mock_mazmo.fetch_rsvps.side_effect = error

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY


def test_sync_when_mazmo_is_unreachable_responds_with_504_gateway_timeout(
    client: TestClient, admin_headers: dict, mock_mazmo: AsyncMock
):
    request = httpx.Request("GET", "https://prod.mazmoapi.net")
    error = httpx.ConnectError("Connection refused", request=request)

    mock_mazmo.fetch_rsvps.side_effect = error

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_sync_when_mazmo_returns_unexpected_shape_responds_with_502_bad_gateway(
    client: TestClient, admin_headers: dict, mock_mazmo: AsyncMock
):
    # This directly simulates getting a 200 OK, but failing to parse the JSON shape
    mock_mazmo.fetch_rsvps.side_effect = ValueError("Missing 'joinedAt' key in response")

    resp = client.post("/guests/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
