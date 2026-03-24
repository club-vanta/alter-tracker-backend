"""Tests for the health check service.

These tests verify that the health check correctly reports the status of
external dependencies (database, Mazmo API) and aggregates them into an
overall health status.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import get_settings
from app.services.health import (
    ServiceStatus,
    check_database,
    check_mazmo,
    get_health_status,
)

# ── Database health checks ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_database_healthy_returns_healthy_status():
    """
    Verify that a working database connection returns healthy status.

    WHY: The database is critical - if SELECT 1 works, we're good.
    """
    mock_session = MagicMock()
    mock_session.exec.return_value = None  # SELECT 1 returns nothing important

    result = await check_database(mock_session)

    assert result.status == ServiceStatus.HEALTHY
    assert result.latency_ms >= 0
    assert result.message == "Everything OK"


@pytest.mark.asyncio
async def test_check_database_unhealthy_when_query_fails():
    """
    Verify that database errors are reported as unhealthy.

    WHY: If we can't execute a simple query, the app cannot function.
    The error message helps diagnose what went wrong.
    """
    mock_session = MagicMock()
    mock_session.exec.side_effect = Exception("Connection refused")

    result = await check_database(mock_session)

    assert result.status == ServiceStatus.UNHEALTHY
    assert result.message is not None and "Connection refused" in result.message


# ── Mazmo health checks ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_mazmo_healthy_when_reachable():
    """
    Verify that a reachable Mazmo API returns healthy status.

    WHY: If we can reach Mazmo at all (even 404), the service is up.
    """
    settings = get_settings()

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.head.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await check_mazmo(settings)

    assert result.status == ServiceStatus.HEALTHY
    assert result.message == "Everything OK"


@pytest.mark.asyncio
async def test_check_mazmo_healthy_even_on_404():
    """
    Verify that 404 from Mazmo is still considered healthy.

    WHY: 404 means the service is up, just that endpoint doesn't exist.
    We're checking connectivity, not endpoint validity.
    """
    settings = get_settings()

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.head.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await check_mazmo(settings)

    assert result.status == ServiceStatus.HEALTHY


@pytest.mark.asyncio
async def test_check_mazmo_degraded_on_5xx_error():
    """
    Verify that 5xx errors from Mazmo return degraded status.

    WHY: 5xx means Mazmo itself is having issues. We can't sync guests,
    but the app can still function for check-ins with existing data.
    """
    settings = get_settings()

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_client.head.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await check_mazmo(settings)

    assert result.status == ServiceStatus.DEGRADED
    assert result.message is not None and "503" in result.message


@pytest.mark.asyncio
async def test_check_mazmo_degraded_on_timeout():
    """
    Verify that connection timeouts return degraded status.

    WHY: Timeout means Mazmo is slow or unreachable. Non-critical
    since we can still work with cached guest data.
    """
    settings = get_settings()

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.TimeoutException("timed out")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await check_mazmo(settings)

    assert result.status == ServiceStatus.DEGRADED
    assert result.message is not None and "timed out" in result.message


@pytest.mark.asyncio
async def test_check_mazmo_degraded_on_network_error():
    """
    Verify that network errors return degraded status.

    WHY: Can't reach Mazmo at all (DNS failure, connection refused).
    Still degraded, not unhealthy, because Mazmo is non-critical.
    """
    settings = get_settings()

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await check_mazmo(settings)

    assert result.status == ServiceStatus.DEGRADED
    assert result.message is not None and "ConnectError" in result.message


# ── Aggregated health status ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_health_status_healthy_when_all_services_up():
    """
    Verify that overall status is healthy when all services are healthy.
    """
    settings = get_settings()
    mock_session = MagicMock()
    mock_session.exec.return_value = None

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.head.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await get_health_status(mock_session, settings)

    assert result.status == "healthy"
    assert "database" in result.services
    assert "mazmo" in result.services


@pytest.mark.asyncio
async def test_get_health_status_degraded_when_mazmo_down():
    """
    Verify that overall status is degraded when Mazmo is down but DB is up.

    WHY: Mazmo is non-critical. App can still function for check-ins.
    """
    settings = get_settings()
    mock_session = MagicMock()
    mock_session.exec.return_value = None

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.TimeoutException("timed out")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await get_health_status(mock_session, settings)

    assert result.status == "degraded"
    assert result.services["database"].status == ServiceStatus.HEALTHY
    assert result.services["mazmo"].status == ServiceStatus.DEGRADED


@pytest.mark.asyncio
async def test_get_health_status_unhealthy_when_database_down():
    """
    Verify that overall status is unhealthy when database is down.

    WHY: Database is critical - app cannot function without it.
    Even if Mazmo is healthy, overall status must be unhealthy.
    """
    settings = get_settings()
    mock_session = MagicMock()
    mock_session.exec.side_effect = Exception("Connection refused")

    with patch("app.services.health.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.head.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        MockClient.return_value = mock_client

        result = await get_health_status(mock_session, settings)

    assert result.status == "unhealthy"
    assert result.services["database"].status == ServiceStatus.UNHEALTHY
