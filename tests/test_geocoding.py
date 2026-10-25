"""Tests for the geocode proxy (GET /organizations/{org_id}/geocode) and PhotonClient.

Photon is never called: httpx.AsyncClient.get is patched, like tests/test_mazmo.py.
"""

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import get_settings
from app.models.models import Organization, OrgRole, User
from app.services import geocoding
from app.services.geocoding import GeocodingUnavailableError, PhotonClient, build_label
from tests.conftest import make_org_member

UNAVAILABLE = (
    "Cannot search addresses: the address search is unavailable right now. "
    "Try again in a moment, or enter the address later."
)


def feature(lon: float = -58.43, lat: float = -34.58, countrycode: str = "AR", **properties: Any) -> dict:
    return {
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"countrycode": countrycode, **properties},
    }


def photon_response(features: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.is_error = False
    resp.json.return_value = {"type": "FeatureCollection", "features": features}
    return resp


@pytest.fixture(autouse=True)
def fresh_rate_limiter() -> Iterator[None]:
    """Each test starts with no previous Photon call, so none of them waits."""
    geocoding._last_call = None
    yield
    geocoding._last_call = None


@pytest.fixture(autouse=True)
def mock_get() -> Iterator[AsyncMock]:
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mocked:
        mocked.return_value = photon_response([])
        yield mocked


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


def _url(org: Organization) -> str:
    return f"/organizations/{org.id}/geocode"


# -- Label --------------------------------------------------------------------


def test_label_from_a_full_feature():
    """WHY: The label is what the admin picks from, so every part must show in order."""
    props = {
        "name": "Club Vanta",
        "street": "Thames",
        "housenumber": "1500",
        "district": "Palermo",
        "city": "Buenos Aires",
        "state": "Ciudad Autonoma de Buenos Aires",
    }

    assert build_label(props) == "Club Vanta, Thames 1500, Palermo, Buenos Aires, Ciudad Autonoma de Buenos Aires"


def test_label_from_a_sparse_feature_skips_missing_parts():
    """WHY: Photon omits what it does not know; no empty segments or stray commas."""
    assert build_label({"street": "Thames", "city": "Buenos Aires", "district": ""}) == "Thames, Buenos Aires"


def test_label_uses_locality_when_there_is_no_district():
    """WHY: Outside big cities Photon gives a locality instead of a district."""
    assert build_label({"name": "Plaza", "locality": "Tigre", "state": "Buenos Aires"}) == "Plaza, Tigre, Buenos Aires"


def test_label_drops_consecutive_duplicates_only():
    """WHY: Buenos Aires is both city and state; repeating it is noise. A non-adjacent repeat stays."""
    props = {"name": "Buenos Aires", "city": "Buenos Aires", "state": "Buenos Aires", "district": "Centro"}

    assert build_label(props) == "Buenos Aires, Centro, Buenos Aires"


def test_label_uses_street_alone_without_housenumber():
    """WHY: A street without a number is still a valid address part."""
    assert build_label({"street": "Thames"}) == "Thames"


def test_label_prefers_district_over_locality():
    """WHY: When both exist the district is the more specific neighborhood."""
    assert build_label({"name": "Plaza", "district": "Palermo", "locality": "Tigre"}) == "Plaza, Palermo"


def test_label_drops_a_housenumber_that_has_no_street():
    """WHY: A bare number segment ("1500") tells the admin nothing."""
    assert (
        build_label({"name": "Club Vanta", "housenumber": "1500", "city": "Buenos Aires"}) == "Club Vanta, Buenos Aires"
    )


# -- Route --------------------------------------------------------------------


def test_geocode_returns_results_and_sends_the_exact_photon_params(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: The Buenos Aires bias and limit are part of the contract with Photon."""
    mock_get.return_value = photon_response([feature(lon=-58.4306, lat=-34.5875, name="Club Vanta", city="CABA")])

    resp = client.get(_url(org), params={"q": "club vanta"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == [{"label": "Club Vanta, CABA", "latitude": -34.5875, "longitude": -58.4306}]
    mock_get.assert_awaited_once_with(
        get_settings().photon_base_url,
        params={"q": "club vanta", "limit": 10, "lat": -34.6037, "lon": -58.3816},
    )


def test_geocode_sends_an_identifying_user_agent_and_a_5_second_timeout(
    client: TestClient, admin_headers: dict, org: Organization
):
    """WHY: Photon's fair use policy asks clients to identify themselves; a slow provider must fail fast."""
    with patch.object(httpx.AsyncClient, "__init__", autospec=True, side_effect=httpx.AsyncClient.__init__) as init:
        client.get(_url(org), params={"q": "thames"}, headers=admin_headers)

    assert init.call_args.kwargs["headers"]["User-Agent"] == "alter-tracker/1.0 (club-vanta.com)"
    assert init.call_args.kwargs["timeout"] == 5.0


def test_geocode_trims_the_query(client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock):
    """WHY: Pasted text often carries spaces; they must not count toward the length or reach Photon."""
    client.get(_url(org), params={"q": "  thames 1500  "}, headers=admin_headers)

    assert mock_get.await_args.kwargs["params"]["q"] == "thames 1500"


def test_geocode_drops_features_outside_argentina(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: The same street name exists in Uruguay and Spain; only Argentine matches are useful."""
    mock_get.return_value = photon_response(
        [
            feature(countrycode="UY", name="Calle Thames", city="Montevideo"),
            feature(countrycode="AR", name="Thames", city="Buenos Aires"),
            feature(countrycode="ES", name="Thames", city="Madrid"),
            {"geometry": {"coordinates": [0, 0]}, "properties": {"name": "No country"}},
        ]
    )

    resp = client.get(_url(org), params={"q": "thames"}, headers=admin_headers)

    assert [r["label"] for r in resp.json()] == ["Thames, Buenos Aires"]


def test_geocode_caps_results_at_five(client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock):
    """WHY: The dropdown shows a short list; the cap applies after filtering, in Photon's order."""
    mock_get.return_value = photon_response([feature(name=f"Place {n}") for n in range(10)])

    resp = client.get(_url(org), params={"q": "place"}, headers=admin_headers)

    assert [r["label"] for r in resp.json()] == [f"Place {n}" for n in range(5)]


def test_geocode_caps_after_filtering_not_before(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: Photon returns non-Argentine matches first; slicing before the filter would return nothing."""
    mock_get.return_value = photon_response(
        [feature(countrycode="UY", name=f"Foreign {n}") for n in range(6)]
        + [feature(name=f"Local {n}") for n in range(6)]
    )

    resp = client.get(_url(org), params={"q": "place"}, headers=admin_headers)

    assert [r["label"] for r in resp.json()] == [f"Local {n}" for n in range(5)]


@pytest.mark.parametrize(
    "bad_feature",
    [
        {"properties": {"countrycode": "AR", "name": "No geometry"}},
        {"properties": {"countrycode": "AR", "name": "Null coords"}, "geometry": {"coordinates": None}},
        {"properties": {"countrycode": "AR", "name": "Short coords"}, "geometry": {"coordinates": [-58.4]}},
        {"properties": {"countrycode": "AR", "name": "Text coords"}, "geometry": {"coordinates": ["a", "b"]}},
        {"properties": None, "geometry": {"coordinates": [-58.4, -34.5]}},
        "not-a-feature",
    ],
    ids=["no-geometry", "null-coords", "short-coords", "text-coords", "null-properties", "not-an-object"],
)
def test_geocode_skips_a_malformed_feature_instead_of_failing(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock, bad_feature: object
):
    """WHY: One odd feature must not turn a good search into a 502."""
    mock_get.return_value = photon_response([bad_feature, feature(name="Plaza")])  # type: ignore[list-item]

    resp = client.get(_url(org), params={"q": "plaza"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert [r["label"] for r in resp.json()] == ["Plaza"]


def test_geocode_returns_an_empty_list_when_nothing_matches(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: No matches is a normal answer, not an error."""
    resp = client.get(_url(org), params={"q": "zzzzzz"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


def test_geocode_skips_features_without_a_label(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: A match with nothing to show cannot be picked."""
    mock_get.return_value = photon_response([feature(), feature(name="Plaza")])

    resp = client.get(_url(org), params={"q": "plaza"}, headers=admin_headers)

    assert [r["label"] for r in resp.json()] == ["Plaza"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
    ids=["timeout", "connect-error"],
)
def test_geocode_returns_502_when_photon_cannot_be_reached(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock, failure: Exception
):
    """WHY: Photon down or slow must not hang or 500 the form; the UI shows a retry message."""
    mock_get.side_effect = failure

    resp = client.get(_url(org), params={"q": "thames"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp.json() == {"detail": UNAVAILABLE}


def test_geocode_returns_502_when_photon_answers_with_an_error_status(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock
):
    """WHY: Photon rate limits with 429 and fails with 5xx; both mean unavailable."""
    failed = MagicMock()
    failed.is_error = True
    failed.status_code = 429
    mock_get.return_value = failed

    resp = client.get(_url(org), params={"q": "thames"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp.json() == {"detail": UNAVAILABLE}


@pytest.mark.parametrize(
    "body",
    [
        ValueError("not json"),
        {"unexpected": []},
        {"features": 5},
    ],
    ids=["not-json", "no-features-key", "features-not-a-list"],
)
def test_geocode_returns_502_when_photon_sends_unreadable_data(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock, body: object
):
    """WHY: A changed Photon payload must surface as unavailable, not as a 500."""
    bad = MagicMock()
    bad.is_error = False
    if isinstance(body, Exception):
        bad.json.side_effect = body
    else:
        bad.json.return_value = body
    mock_get.return_value = bad

    resp = client.get(_url(org), params={"q": "thames"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp.json() == {"detail": UNAVAILABLE}


@pytest.mark.parametrize("q", ["ab", "  ab  ", "x" * 201], ids=["too-short", "short-after-trim", "too-long"])
def test_geocode_returns_422_for_a_query_outside_3_to_200_chars(
    client: TestClient, admin_headers: dict, org: Organization, mock_get: AsyncMock, q: str
):
    """WHY: Short queries match everything and waste the rate-limited provider."""
    resp = client.get(_url(org), params={"q": q}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_get.assert_not_awaited()


def test_geocode_returns_422_without_a_query(client: TestClient, admin_headers: dict, org: Organization):
    """WHY: q is required."""
    resp = client.get(_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_geocode_returns_403_for_org_staff(
    client: TestClient, staff_headers: dict, org: Organization, org_staff_member, mock_get: AsyncMock
):
    """WHY: Only org admins edit the venue, so only they search addresses."""
    resp = client.get(_url(org), params={"q": "thames"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN
    mock_get.assert_not_awaited()


def test_geocode_returns_401_without_auth(client: TestClient, org: Organization):
    """WHY: The route is not public."""
    resp = client.get(_url(org), params={"q": "thames"})

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_geocode_returns_404_for_an_unknown_org(client: TestClient, admin_headers: dict, mock_get: AsyncMock):
    """WHY: A site admin passes the role check for any id, so the org must be looked up."""
    resp = client.get(
        "/organizations/00000000-0000-0000-0000-000000000000/geocode", params={"q": "thames"}, headers=admin_headers
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Rate limiter -------------------------------------------------------------


class FakeClock:
    """A clock that only moves when the limiter sleeps, yielding to other tasks like a real sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self._real_sleep = asyncio.sleep

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await self._real_sleep(0)


@pytest.fixture()
def clock() -> Iterator[FakeClock]:
    fake = FakeClock()
    with patch.object(geocoding, "_monotonic", fake.monotonic), patch.object(geocoding, "_sleep", fake.sleep):
        yield fake


@pytest.mark.asyncio
async def test_concurrent_searches_reach_photon_at_least_one_second_apart(clock: FakeClock, mock_get: AsyncMock):
    """WHY: The lock is what spaces out simultaneous requests; without it they all fire at once."""
    call_times: list[float] = []

    async def record_call(*args: object, **kwargs: object) -> MagicMock:
        call_times.append(clock.now)
        return photon_response([])

    mock_get.side_effect = record_call

    async with PhotonClient(get_settings()) as photon:
        await asyncio.gather(photon.search("one"), photon.search("two"), photon.search("three"))

    assert len(call_times) == 3
    assert call_times[1] - call_times[0] >= 1.0
    assert call_times[2] - call_times[1] >= 1.0


@pytest.mark.asyncio
async def test_a_call_right_after_another_waits_exactly_the_one_second_interval(clock: FakeClock):
    """WHY: Photon asks for at most 1 request per second; callers wait, they are not rejected."""
    async with PhotonClient(get_settings()) as photon:
        await photon.search("first")
        assert clock.sleeps == []
        await photon.search("second")

    assert clock.sleeps == [1.0]


@pytest.mark.asyncio
async def test_a_call_after_the_interval_does_not_wait(clock: FakeClock):
    """WHY: The limiter must not slow down searches that are already spaced out."""
    geocoding._last_call = clock.now - 2.0

    async with PhotonClient(get_settings()) as photon:
        await photon.search("later")

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_failed_calls_still_count_toward_the_rate_limit(clock: FakeClock, mock_get: AsyncMock):
    """WHY: Retrying a failing provider in a tight loop would hammer it."""
    mock_get.side_effect = httpx.ReadTimeout("slow")

    async with PhotonClient(get_settings()) as photon:
        with pytest.raises(GeocodingUnavailableError):
            await photon.search("first")
        with pytest.raises(GeocodingUnavailableError):
            await photon.search("second")

    assert clock.sleeps == [1.0]
