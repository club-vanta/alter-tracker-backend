"""
Mazmo API client.

Encapsulates all outbound HTTP logic so the router stays thin.
All methods are async and use a shared httpx.AsyncClient.

URL Transformation
──────────────────
Frontend URLs:  https://mazmo.net/{community}/{thread-slug}-{thread_id}
API URLs:       https://prod.mazmoapi.net/communities/{community}/threads/{thread-slug}-{thread_id}

The transformation is encapsulated in _to_api_url().

Two-step fetch flow
───────────────────
Step 1 - Thread endpoint
  GET /communities/{community_slug}/threads/{thread_slug}-{thread_id}
  Response shape (we only care about the `event.rsvps` field):
  {
    "event": {
      "rsvps": {
        "0": { "userId": 195749, "joinedAt": "2026-03-17T00:25:32.744Z" },
        "1": { "userId": 153151, "joinedAt": "..." },
        ...
      }
    }
  }

Step 2 - Users endpoint (batched to avoid URL length limits)
  GET /users?ids=195749,153151,...
  Response shape:
  {
    "195749": { "username": "...", "displayname": "..." },
    ...
  }

Headers mimic a real browser session to avoid 403s from Mazmo's CDN/WAF.
"""

import asyncio
from datetime import datetime
from itertools import islice
from typing import NamedTuple, TypedDict
from urllib.parse import urlparse

import httpx
import structlog

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.schemas import MazmoRsvpEntry, MazmoUserEntry

log = structlog.get_logger(__name__)


# Browser-like headers that Mazmo's API expects.
_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Origin": "https://mazmo.net",
    "Referer": "https://mazmo.net/",
}


# ── Raw API response shapes ───────────────────────────────────────────────────


class RawRsvpEntry(TypedDict):
    userId: MazmoUserId
    joinedAt: str


class RawUserEntry(TypedDict):
    username: str
    displayname: str


class RawRsvpsDict(TypedDict):
    rsvps: dict[str, RawRsvpEntry]


class RawEventField(TypedDict):
    event: RawRsvpsDict


class RawEventData(TypedDict):
    date: str


class RawThreadResponse(TypedDict):
    event: RawEventData


# ── Custom exceptions ────────────────────────────────────────────────────────


class MazmoNetworkError(Exception):
    """Raised when Mazmo API is unreachable."""

    pass


class MazmoAPIError(Exception):
    """Raised when Mazmo API returns an error status."""

    pass


# ── Return types ──────────────────────────────────────────────────────────────


class MazmoUserWithId(NamedTuple):
    """Combined result from the single-user lookup endpoint."""

    mazmo_user_id: MazmoUserId
    username: str
    displayname: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _batched(iterable: list[MazmoUserId], n: int) -> list[list[MazmoUserId]]:
    """Split a list into successive n-sized chunks."""
    it = iter(iterable)
    result: list[list[MazmoUserId]] = []
    while chunk := list(islice(it, n)):
        result.append(chunk)
    return result


# ── Client ────────────────────────────────────────────────────────────────────


class MazmoClient:
    """
    Thin async wrapper around the Mazmo internal API.
    Instantiate once per sync request (or inject as a FastAPI dependency).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=settings.mazmo_request_timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "MazmoClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    # ── URL transformation ────────────────────────────────────────────────────

    def _to_api_url(self, frontend_url: str) -> str:
        """
        Transform mazmo.net URL to prod.mazmoapi.net URL.

        https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217
        → https://prod.mazmoapi.net/communities/eventos-reuniones-argentina/threads/alter-cordoba-4217

        https://mazmo.net/+eventos-reuniones-argentina/alter-tal-selmo-opgnjcy4d0u
        → https://prod.mazmoapi.net/communities/eventos-reuniones-argentina/threads/alter-tal-selmo-opgnjcy4d0u
        """
        parsed = urlparse(frontend_url)
        path_parts = parsed.path.strip("/").split("/")
        # Some communities use '+' as a frontend prefix (e.g. +eventos-reuniones-argentina);
        # the API doesn't accept it.
        community = path_parts[0].lstrip("+")
        thread = path_parts[1]
        return f"{self._settings.mazmo_base_url}/communities/{community}/threads/{thread}"

    # ── Step 1: RSVPs ─────────────────────────────────────────────────────────

    async def fetch_rsvps(self, mazmo_url: str) -> dict[MazmoUserId, MazmoRsvpEntry]:
        """
        Returns a mapping of  MazmoUserId → MazmoRsvpEntry  for every
        guest who has RSVP'd to the event thread.

        Args:
            mazmo_url: Frontend URL like https://mazmo.net/{community}/{thread-slug}-{id}
        """
        api_url = self._to_api_url(mazmo_url)
        log.info("Fetching RSVPs from %s", api_url)

        response = await self._client.get(api_url)
        self._raise_for_status(response, "thread endpoint")

        try:
            body: RawEventField = response.json()
            raw_rsvps: dict[str, RawRsvpEntry] = body["event"]["rsvps"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Unexpected Mazmo thread response shape - could not find "
                f"event.rsvps. Keys found: {list(response.json().keys())}"
            ) from exc

        result: dict[MazmoUserId, MazmoRsvpEntry] = {}
        for _index, entry in raw_rsvps.items():
            parsed = MazmoRsvpEntry.model_validate(entry)
            result[MazmoUserId(parsed.userId)] = parsed

        log.info("Fetched %d RSVPs from Mazmo", len(result))
        return result

    # ── Fetch meetup date ─────────────────────────────────────────────────────

    async def fetch_meetup_date(self, mazmo_url: str) -> datetime:
        """
        Fetch the event date from a Mazmo thread URL.

        Args:
            mazmo_url: Frontend URL like https://mazmo.net/{community}/{thread-slug}-{id}

        Returns:
            The event date as a datetime object.

        Raises:
            MazmoNetworkError: If Mazmo API is unreachable.
            MazmoAPIError: If Mazmo API returns an error status.
        """
        api_url = self._to_api_url(mazmo_url)
        try:
            log.info("Fetching meetup date from %s", api_url)
            response = await self._client.get(api_url)
            response.raise_for_status()
            body: RawThreadResponse = response.json()
            date_str = body["event"]["date"]
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except httpx.RequestError as exc:
            raise MazmoNetworkError(f"Cannot reach Mazmo: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise MazmoAPIError(f"Mazmo returned {exc.response.status_code} for API URL {api_url}") from exc

    # ── Single user by username ───────────────────────────────────────────────

    async def fetch_user_by_username(self, username: str) -> MazmoUserWithId:
        """
        Looks up a Mazmo user by their username handle.

        Args:
            username: Mazmo username, e.g. "cindydark"

        Returns:
            MazmoUserWithId with mazmo_user_id, username, and displayname.

        Raises:
            MazmoNetworkError: If Mazmo API is unreachable.
            MazmoAPIError: If Mazmo API returns an error status (including 404).
        """
        url = f"{self._settings.mazmo_base_url}/users/{username}"
        try:
            resp = await self._client.get(url)
            self._raise_for_status(resp, context=f"fetch user by username '{username}'")
        except httpx.HTTPStatusError as exc:
            raise MazmoAPIError(f"Mazmo returned {exc.response.status_code} for username '{username}'") from exc
        except httpx.RequestError as exc:
            raise MazmoNetworkError(f"Cannot reach Mazmo: {exc}") from exc

        data = resp.json()
        return MazmoUserWithId(
            mazmo_user_id=MazmoUserId(int(data["id"])),
            username=data["username"],
            displayname=data["displayname"],
        )

    # ── Step 2: User details (batched) ────────────────────────────────────────

    async def fetch_users(self, user_ids: list[MazmoUserId]) -> dict[MazmoUserId, MazmoUserEntry]:
        """
        Returns a mapping of  MazmoUserId → MazmoUserEntry  for every ID
        provided. IDs are sent in batches to avoid URL length limits.
        """
        if not user_ids:
            return {}

        batch_size = self._settings.mazmo_user_batch_size
        url = f"{self._settings.mazmo_base_url}/users"

        batches = _batched(user_ids, batch_size)
        log.info(
            "Fetching %d user(s) from Mazmo in %d batch(es)",
            len(user_ids),
            len(batches),
        )

        async def _fetch_batch(ids: list[MazmoUserId]) -> dict[str, RawUserEntry]:
            resp = await self._client.get(url, params={"ids": ",".join(map(str, ids))})
            self._raise_for_status(resp, f"users endpoint (batch of {len(ids)})")
            return resp.json()  # type: ignore[return-value]

        raw_results: list[dict[str, RawUserEntry]] = await asyncio.gather(*[_fetch_batch(b) for b in batches])

        result: dict[MazmoUserId, MazmoUserEntry] = {}
        for batch_data in raw_results:
            for str_id, user_data in batch_data.items():
                parsed = MazmoUserEntry.model_validate(user_data)
                result[MazmoUserId(int(str_id))] = parsed

        log.info("Fetched details for %d user(s)", len(result))
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        """
        Raises an HTTPStatusError if the response indicates an error (4xx or 5xx).
        The `context` string is included in the message to identify which endpoint
        failed, since this method is called from multiple places.
        """
        if response.is_error:
            raise httpx.HTTPStatusError(
                f"Mazmo {context} returned HTTP {response.status_code}. Body: {response.text[:200]}",
                request=response.request,
                response=response,
            )
