"""
Geocoding client over Photon (https://photon.komoot.io).

Powers the address search an org admin uses to set a meetup's location.
Encapsulates the outbound HTTP logic so the router stays thin. Mirrors
MazmoClient: an async context manager around one shared httpx.AsyncClient.

Request
-------
  GET {photon_base_url}?q=...&limit=10&lat=-34.6037&lon=-58.3816
The lat/lon pair biases results toward Buenos Aires. Photon returns GeoJSON;
we keep features whose properties.countrycode is "AR", drop repeated labels
(the first feature with a label wins) and return at most 5.

Label
-----
The non-empty parts of name, "street housenumber", district (or locality),
city and state, joined with ", ", consecutive duplicates removed.

Rate limit
----------
Photon's fair use policy asks clients to be gentle. At most 1 request per
second leaves this process, enforced by a module-level asyncio.Lock plus the
monotonic time of the last call. Callers wait their turn; they are never
rejected. The limit is per process, not per deployment.

Errors
------
Timeouts, transport errors, non-2xx statuses and malformed JSON all raise
GeocodingUnavailableError; the router maps it to 502.
"""

import asyncio
import time
from typing import Any

import httpx
import structlog

from app.core.config import Settings
from app.schemas import GeocodeResult

log = structlog.get_logger(__name__)

# Photon's fair use policy asks for a User-Agent that identifies the app.
_HEADERS: dict[str, str] = {
    "User-Agent": "alter-tracker/1.0 (club-vanta.com)",
    "Accept": "application/json",
}

# Buenos Aires, used to bias results toward the club's city.
_BIAS_LAT = -34.6037
_BIAS_LON = -58.3816

_PHOTON_LIMIT = 10
_MAX_RESULTS = 5
_COUNTRY_CODE = "AR"
_MIN_INTERVAL_SECONDS = 1.0

# -- Rate limiter -------------------------------------------------------------

# Seams for tests: the limiter reads the clock and sleeps only through these.
_sleep = asyncio.sleep
_monotonic = time.monotonic

_rate_lock = asyncio.Lock()
_last_call: float | None = None


async def _wait_for_turn() -> None:
    """Hold the lock until at least _MIN_INTERVAL_SECONDS passed since the last call."""
    global _last_call
    async with _rate_lock:
        if _last_call is not None:
            wait = _MIN_INTERVAL_SECONDS - (_monotonic() - _last_call)
            if wait > 0:
                await _sleep(wait)
        _last_call = _monotonic()


# -- Errors -------------------------------------------------------------------


class GeocodingUnavailableError(Exception):
    """Photon timed out, failed, or returned something we cannot read."""


# -- Parsing ------------------------------------------------------------------


def build_label(properties: dict[str, Any]) -> str:
    """Join the non-empty address parts of a Photon feature, dropping consecutive duplicates."""
    # A housenumber means nothing without its street, so it is dropped alone.
    street = f"{properties['street']} {properties.get('housenumber') or ''}".strip() if properties.get("street") else ""
    parts = [
        properties.get("name"),
        street,
        properties.get("district") or properties.get("locality"),
        properties.get("city"),
        properties.get("state"),
    ]
    label: list[str] = []
    for part in parts:
        text = str(part).strip() if part else ""
        if text and (not label or label[-1] != text):
            label.append(text)
    return ", ".join(label)


def _parse_feature(feature: Any) -> GeocodeResult | None:
    """Return a result for an Argentine feature with a label and coordinates, else None.

    A malformed feature is skipped rather than failing the whole search.
    """
    try:
        properties = feature["properties"]
        if properties.get("countrycode") != _COUNTRY_CODE:
            return None
        label = build_label(properties)
        longitude, latitude = feature["geometry"]["coordinates"][:2]
        if not label:
            return None
        return GeocodeResult(label=label, latitude=latitude, longitude=longitude)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# -- Client -------------------------------------------------------------------


class PhotonClient:
    """Thin async wrapper around the Photon search API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=settings.photon_request_timeout)

    async def __aenter__(self) -> "PhotonClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def search(self, query: str) -> list[GeocodeResult]:
        """Search addresses in Argentina matching the query, at most 5."""
        params: dict[str, str | float | int] = {
            "q": query,
            "limit": _PHOTON_LIMIT,
            "lat": _BIAS_LAT,
            "lon": _BIAS_LON,
        }
        await _wait_for_turn()
        started = time.monotonic()
        try:
            response = await self._client.get(self._settings.photon_base_url, params=params)
        except httpx.HTTPError as exc:
            log.warning("Photon request failed", error=type(exc).__name__, elapsed=time.monotonic() - started)
            raise GeocodingUnavailableError(f"Photon request failed: {type(exc).__name__}") from exc
        if response.is_error:
            log.warning("Photon returned an error", status=response.status_code, elapsed=time.monotonic() - started)
            raise GeocodingUnavailableError(f"Photon returned HTTP {response.status_code}")

        try:
            features: list[Any] = response.json()["features"]
            results = [_parse_feature(feature) for feature in features]
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Photon returned unreadable data", error=type(exc).__name__)
            raise GeocodingUnavailableError("Photon returned data in an unexpected format") from exc
        # Photon can return different places that read the same; show each label once.
        unique: dict[str, GeocodeResult] = {}
        for result in results:
            if result is not None:
                unique.setdefault(result.label, result)
        return list(unique.values())[:_MAX_RESULTS]
