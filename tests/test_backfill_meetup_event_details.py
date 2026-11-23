"""Tests for scripts/backfill_meetup_event_details.py, which fills cover and excerpt on old meetups."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlmodel import Session

from app.core.config import get_settings
from app.services.mazmo import MazmoAPIError
from app.services.mazmo_event import MazmoEventDetails
from scripts.backfill_meetup_event_details import BackfillResult, backfill
from tests.conftest import make_meetup, make_org

URL_A = "https://mazmo.net/club/alter-a-1"
URL_B = "https://mazmo.net/club/alter-b-2"
EVENT_DATE = datetime(2026, 5, 1, 22, 0, tzinfo=UTC)


def _details(tag: str) -> MazmoEventDetails:
    return MazmoEventDetails(date=EVENT_DATE, cover_url=f"https://cdn.example/{tag}.jpg", excerpt=f"Excerpt {tag}")


@pytest.fixture
def mock_mazmo() -> Iterator[AsyncMock]:
    with patch("scripts.backfill_meetup_event_details.MazmoClient") as mock_class:
        mock = AsyncMock()
        mock.__aenter__.return_value = mock
        mock.__aexit__.return_value = None
        mock_class.return_value = mock
        yield mock


@pytest.mark.asyncio
async def test_backfill_fills_missing_cover_and_excerpt(session: Session, mock_mazmo: AsyncMock):
    """WHY: Past alters created before cover/excerpt existed must show them on the public site."""
    org = make_org(session)
    first = make_meetup(session, org=org, name="A", mazmo_meetup_url=URL_A)
    second = make_meetup(session, org=org, name="B", mazmo_meetup_url=URL_B)
    mock_mazmo.fetch_event_details.side_effect = lambda url: _details("a" if url == URL_A else "b")

    result = await backfill(session, get_settings())

    assert result == BackfillResult(updated=2, failed=0)
    session.refresh(first)
    session.refresh(second)
    assert (first.cover_url, first.excerpt) == ("https://cdn.example/a.jpg", "Excerpt a")
    assert (second.cover_url, second.excerpt) == ("https://cdn.example/b.jpg", "Excerpt b")
    assert first.date != EVENT_DATE  # the backfill never touches the date


@pytest.mark.asyncio
async def test_backfill_skips_meetups_that_already_have_cover(session: Session, mock_mazmo: AsyncMock):
    org = make_org(session)
    done = make_meetup(
        session, org=org, name="Done", mazmo_meetup_url=URL_A, cover_url="https://cdn.example/old.jpg", excerpt="Old"
    )
    make_meetup(session, org=org, name="Todo", mazmo_meetup_url=URL_B)
    mock_mazmo.fetch_event_details.return_value = _details("b")

    result = await backfill(session, get_settings())

    assert result == BackfillResult(updated=1, failed=0)
    mock_mazmo.fetch_event_details.assert_awaited_once_with(URL_B)
    session.refresh(done)
    assert (done.cover_url, done.excerpt) == ("https://cdn.example/old.jpg", "Old")


@pytest.mark.asyncio
async def test_backfill_counts_failures_and_continues(session: Session, mock_mazmo: AsyncMock):
    org = make_org(session)
    broken = make_meetup(session, org=org, name="Broken", mazmo_meetup_url=URL_A)
    fine = make_meetup(session, org=org, name="Fine", mazmo_meetup_url=URL_B)

    def fetch(url: str) -> MazmoEventDetails:
        if url == URL_A:
            raise MazmoAPIError("boom")
        return _details("b")

    mock_mazmo.fetch_event_details.side_effect = fetch

    result = await backfill(session, get_settings())

    assert result == BackfillResult(updated=1, failed=1)
    session.refresh(broken)
    session.refresh(fine)
    assert broken.cover_url is None
    assert fine.cover_url == "https://cdn.example/b.jpg"


@pytest.mark.asyncio
async def test_backfill_counts_unexpected_mazmo_body_as_failed_and_continues(session: Session):
    """WHY: A 200 with a malformed event (null date) must not abort the whole backfill."""
    org = make_org(session)
    bad = make_meetup(session, org=org, name="Bad", mazmo_meetup_url=URL_A)
    good = make_meetup(session, org=org, name="Good", mazmo_meetup_url=URL_B)
    bodies = {
        "alter-a-1": {"event": {"date": None, "cover": {"url": "x"}}},
        "alter-b-2": {"event": {"date": "2026-05-01T22:00:00.000Z", "cover": "https://cdn.example/b.jpg"}},
    }

    async def fake_get(self: httpx.AsyncClient, url: str, *args: object, **kwargs: object) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = next(body for key, body in bodies.items() if key in url)
        return resp

    with patch.object(httpx.AsyncClient, "get", fake_get):
        result = await backfill(session, get_settings())

    assert result == BackfillResult(updated=1, failed=1)
    session.refresh(bad)
    session.refresh(good)
    assert bad.cover_url is None
    assert good.cover_url == "https://cdn.example/b.jpg"
