"""
Mazmo API client.

Encapsulates all outbound HTTP logic so the router stays thin.
All methods are async and use a shared httpx.AsyncClient.

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
import logging
from itertools import islice
from typing import NewType, TypedDict

import httpx

from app.core.config import Settings
from app.schemas.schemas import MazmoRsvpEntry, MazmoUserEntry

log = logging.getLogger(__name__)

# Distinct type for Mazmo user IDs — prevents accidentally mixing them with
# other integers (e.g. internal DB IDs) at the type checker level.
MazmoUserId = NewType("MazmoUserId", int)

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

    # ── Step 1: RSVPs ─────────────────────────────────────────────────────────

    async def fetch_rsvps(self) -> dict[MazmoUserId, MazmoRsvpEntry]:
        """
        Returns a mapping of  MazmoUserId → MazmoRsvpEntry  for every
        guest who has RSVP'd to the event thread.
        """
        s = self._settings
        url = (
            f"{s.mazmo_base_url}/communities/{s.mazmo_community_slug}"
            f"/threads/{s.mazmo_thread_slug}-{s.mazmo_thread_id}"
        )
        log.info("Fetching RSVPs from %s", url)

        response = await self._client.get(url)
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

        raw_results: list[dict[str, RawUserEntry]] = await asyncio.gather(
            *[_fetch_batch(b) for b in batches]
        )

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
                f"Mazmo {context} returned HTTP {response.status_code}. "
                f"Body: {response.text[:200]}",
                request=response.request,
                response=response,
            )
