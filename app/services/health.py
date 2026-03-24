"""
Health check service.

Provides connectivity checks for external dependencies:
  - Database (PostgreSQL)
  - Mazmo API

Each check returns a status and latency. The overall health is "healthy"
only if all critical dependencies are reachable.
"""

import time
from enum import StrEnum
from typing import Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import Settings


class ServiceStatus(StrEnum):
    """Status of an individual service check."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"  # For non-critical services


class ServiceCheck(BaseModel):
    """Result of a single service health check."""

    status: ServiceStatus
    latency_ms: float
    message: str | None = None


class HealthResponse(BaseModel):
    """
    Full health check response.

    The overall status is "healthy" only if all critical services are healthy.
    Non-critical services being down results in "degraded" status.
    """

    status: Literal["healthy", "unhealthy", "degraded"]
    services: dict[str, ServiceCheck]


async def check_database(session: Session) -> ServiceCheck:
    """
    Check database connectivity by executing a simple query.

    Uses SELECT 1 which is the most lightweight query possible.
    """
    start = time.perf_counter()
    try:
        session.exec(text("SELECT 1"))  # type: ignore[arg-type]
        latency = (time.perf_counter() - start) * 1000
        return ServiceCheck(
            status=ServiceStatus.HEALTHY,
            latency_ms=round(latency, 2),
            message="Everything OK",
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ServiceCheck(
            status=ServiceStatus.UNHEALTHY,
            latency_ms=round(latency, 2),
            message=str(exc),
        )


async def check_mazmo(settings: Settings) -> ServiceCheck:
    """
    Check Mazmo API connectivity.

    Makes a lightweight HEAD request to the API base URL. We consider Mazmo
    "healthy" if we can reach it at all (even a 404 means the service is up).
    Only network errors indicate Mazmo is truly unreachable.

    Note: Mazmo is considered non-critical - the app can function without it
    (just can't sync new guests or add new meetups).

    So Mazmo being down = degraded, not unhealthy.
    """
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # HEAD request to base URL - lightweight connectivity check
            response = await client.head(settings.mazmo_base_url)
            latency = (time.perf_counter() - start) * 1000

            # Any response (even 404, 403) means Mazmo is reachable
            # Only 5xx errors indicate Mazmo itself is having issues
            if response.status_code >= 500:
                return ServiceCheck(
                    status=ServiceStatus.DEGRADED,
                    latency_ms=round(latency, 2),
                    message=f"Mazmo returned {response.status_code}",
                )

            return ServiceCheck(
                status=ServiceStatus.HEALTHY,
                latency_ms=round(latency, 2),
                message="Everything OK",
            )
    except httpx.TimeoutException:
        latency = (time.perf_counter() - start) * 1000
        return ServiceCheck(
            status=ServiceStatus.DEGRADED,
            latency_ms=round(latency, 2),
            message="Connection timed out",
        )
    except httpx.RequestError as exc:
        latency = (time.perf_counter() - start) * 1000
        return ServiceCheck(
            status=ServiceStatus.DEGRADED,
            latency_ms=round(latency, 2),
            message=f"Cannot reach Mazmo: {type(exc).__name__}",
        )


async def get_health_status(session: Session, settings: Settings) -> HealthResponse:
    """
    Run all health checks and return aggregated status.

    Critical services (database): unhealthy → overall unhealthy
    Non-critical services (mazmo): unhealthy → overall degraded
    """
    db_check = await check_database(session)
    mazmo_check = await check_mazmo(settings)

    services = {
        "database": db_check,
        "mazmo": mazmo_check,
    }

    # Determine overall status
    if db_check.status == ServiceStatus.UNHEALTHY:
        # Critical service down
        overall = "unhealthy"
    elif mazmo_check.status != ServiceStatus.HEALTHY:
        # Non-critical service degraded
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthResponse(status=overall, services=services)
