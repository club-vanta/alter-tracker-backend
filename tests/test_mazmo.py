"""Tests for the Mazmo API client.

These tests verify the MazmoClient correctly transforms URLs, fetches RSVPs
and user details, handles errors, and batches requests appropriately.
All HTTP calls are mocked - we're testing our logic, not the external API.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.services.mazmo import (
    MazmoAPIError,
    MazmoClient,
    MazmoNetworkError,
    _batched,
)

# ── URL transformation ────────────────────────────────────────────────────────


def test_batched_splits_list_into_chunks():
    """
    Verify that _batched correctly splits a list into n-sized chunks.

    WHY: We batch user ID requests to avoid URL length limits.
    """
    items = [MazmoUserId(n) for n in [1, 2, 3, 4, 5, 6, 7]]
    result = _batched(items, 3)

    assert result == [
        [MazmoUserId(1), MazmoUserId(2), MazmoUserId(3)],
        [MazmoUserId(4), MazmoUserId(5), MazmoUserId(6)],
        [MazmoUserId(7)],
    ]


def test_batched_handles_empty_list():
    """
    Verify that _batched handles empty lists gracefully.
    """
    result = _batched([], 3)  # type: ignore[arg-type]
    assert result == []


def test_batched_handles_list_smaller_than_batch_size():
    """
    Verify that _batched works when list is smaller than batch size.
    """
    items = [MazmoUserId(1), MazmoUserId(2)]
    result = _batched(items, 10)

    assert result == [[MazmoUserId(1), MazmoUserId(2)]]


@pytest.mark.asyncio
async def test_mazmo_client_transforms_frontend_url_to_api_url():
    """
    Verify that frontend URLs are correctly transformed to API URLs.

    WHY: Mazmo has different URLs for frontend and API. We need to
    transform user-provided frontend URLs to hit the correct API endpoint.
    """
    settings = get_settings()
    client = MazmoClient(settings)

    frontend_url = "https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217"
    api_url = client._to_api_url(frontend_url)

    assert (
        api_url
        == "https://prod.mazmoapi.net/communities/eventos-reuniones-argentina/threads/alter-cordoba-4217"
    )


# ── fetch_rsvps ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_rsvps_returns_parsed_rsvp_entries():
    """
    Verify that fetch_rsvps correctly parses the Mazmo thread response.

    WHY: The Mazmo API returns RSVPs in a nested structure. We need to
    extract and parse them into our schema.
    """
    settings = get_settings()

    mock_response = {
        "event": {
            "rsvps": {
                "0": {"userId": 111, "joinedAt": "2026-03-17T00:25:32.744Z"},
                "1": {"userId": 222, "joinedAt": "2026-03-17T01:30:00.000Z"},
            }
        }
    }

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.is_error = False
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            result = await client.fetch_rsvps("https://mazmo.net/test/meetup-123")

    assert len(result) == 2
    assert MazmoUserId(111) in result
    assert MazmoUserId(222) in result
    assert result[MazmoUserId(111)].userId == 111


@pytest.mark.asyncio
async def test_fetch_rsvps_raises_on_http_error():
    """
    Verify that HTTP errors are raised appropriately.

    WHY: If Mazmo returns an error status, we need to surface it
    so the router can translate it to the appropriate HTTP status.
    """
    settings = get_settings()

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.is_error = True
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.request = httpx.Request("GET", "https://test.com")
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.fetch_rsvps("https://mazmo.net/test/meetup-123")


@pytest.mark.asyncio
async def test_fetch_rsvps_raises_on_malformed_response():
    """
    Verify that malformed responses raise ValueError.

    WHY: If Mazmo changes their API format, we should fail clearly
    rather than returning garbage data.
    """
    settings = get_settings()

    mock_response = {"unexpected": "format"}

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.is_error = False
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            with pytest.raises(ValueError, match="Unexpected Mazmo thread response"):
                await client.fetch_rsvps("https://mazmo.net/test/meetup-123")


# ── fetch_users ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_users_returns_parsed_user_entries():
    """
    Verify that fetch_users correctly parses user detail responses.
    """
    settings = get_settings()

    mock_response = {
        "111": {"username": "alice", "displayname": "Alice Wonderland"},
        "222": {"username": "bob", "displayname": "Bob Builder"},
    }

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.is_error = False
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            result = await client.fetch_users([MazmoUserId(111), MazmoUserId(222)])

    assert len(result) == 2
    assert result[MazmoUserId(111)].username == "alice"
    assert result[MazmoUserId(222)].displayname == "Bob Builder"


@pytest.mark.asyncio
async def test_fetch_users_returns_empty_dict_for_empty_list():
    """
    Verify that fetch_users handles empty user ID lists gracefully.

    WHY: Edge case - if no RSVPs, we shouldn't make any API calls.
    """
    settings = get_settings()

    async with MazmoClient(settings) as client:
        result = await client.fetch_users([])

    assert result == {}


@pytest.mark.asyncio
async def test_fetch_users_batches_requests():
    """
    Verify that fetch_users batches large user lists to avoid URL length limits.

    WHY: Mazmo may reject URLs that are too long. We batch requests
    to keep query strings manageable.
    """
    settings = get_settings()
    # Override batch size for testing
    settings.mazmo_user_batch_size = 2

    user_ids = [MazmoUserId(n) for n in [111, 222, 333, 444, 555]]

    call_count = 0

    async def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        # Return users for whatever IDs were requested
        ids = params["ids"].split(",") if params else []
        mock_resp.json.return_value = {
            uid: {"username": f"user{uid}", "displayname": f"User {uid}"} for uid in ids
        }
        mock_resp.is_error = False
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
        async with MazmoClient(settings) as client:
            result = await client.fetch_users(user_ids)

    # With batch size 2 and 5 users, we need 3 batches
    assert call_count == 3
    assert len(result) == 5


# ── fetch_meetup_date ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_meetup_date_returns_parsed_datetime():
    """
    Verify that fetch_meetup_date correctly parses the event date.
    """
    settings = get_settings()

    mock_response = {"event": {"date": "2026-04-15T19:00:00.000Z"}}

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            result = await client.fetch_meetup_date("https://mazmo.net/test/meetup-123")

    assert result == datetime(2026, 4, 15, 19, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fetch_meetup_date_raises_network_error_on_connection_failure():
    """
    Verify that connection errors are wrapped in MazmoNetworkError.

    WHY: We distinguish network errors (can't reach Mazmo) from API errors
    (Mazmo returned an error) for better error messages.
    """
    settings = get_settings()

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        async with MazmoClient(settings) as client:
            with pytest.raises(MazmoNetworkError, match="Cannot reach Mazmo"):
                await client.fetch_meetup_date("https://mazmo.net/test/meetup-123")


@pytest.mark.asyncio
async def test_fetch_meetup_date_raises_api_error_on_http_error():
    """
    Verify that HTTP errors are wrapped in MazmoAPIError.
    """
    settings = get_settings()

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=httpx.Request("GET", "https://test.com"),
            response=mock_resp,
        )
        mock_get.return_value = mock_resp

        async with MazmoClient(settings) as client:
            with pytest.raises(MazmoAPIError, match="Mazmo returned 404"):
                await client.fetch_meetup_date("https://mazmo.net/test/meetup-123")
