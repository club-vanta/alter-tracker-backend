"""Tests for the weather of a meetup: inline fetch on location change, refresh and read endpoints.

Open-Meteo is never called: httpx.AsyncClient.get is patched, like tests/test_weather.py.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session
from structlog.testing import capture_logs

from app.models.models import Meetup, MeetupWeather, Organization, OrgRole, User
from app.services.weather import SOURCE
from tests.conftest import make_meetup, make_org, make_org_member

LAT = -34.5875
LON = -58.4306
LOCATION = {"location_label": "Club Vanta, Palermo, Buenos Aires", "latitude": LAT, "longitude": LON}
MOVED = {"location_label": "Teatro Colon, Buenos Aires", "latitude": -34.6010, "longitude": -58.3830}
CLEARED = {"location_label": None, "latitude": None, "longitude": None}

NO_LOCATION_DETAIL = "Cannot refresh weather: the meetup has no location. Set a location first."
NO_DATA_DETAIL = (
    "Cannot refresh weather: the weather provider has no data for this place and time. "
    "Check the meetup location, or try again later."
)
UNAVAILABLE_DETAIL = "Cannot refresh weather: the weather provider is unavailable right now. Try again in a moment."
TIMEOUT_DETAIL = "Cannot refresh weather: the weather provider did not respond in time. Try again in a moment."


def too_early_detail(meetup: Meetup) -> str:
    """The message names the start plus 8 hours, in UTC (the test database returns naive UTC datetimes)."""
    ready_at = meetup.date.replace(tzinfo=UTC) + timedelta(hours=8)
    return (
        "Cannot refresh weather: it is available 8 hours after the event starts "
        f"(from {ready_at:%Y-%m-%dT%H:%M:%SZ}). Try again then."
    )


def hourly_for(date: datetime, temperature: float | None, precipitation: float | None) -> dict[str, list[Any]]:
    """Seven hourly points covering the window of a meetup starting at `date` (naive UTC strings)."""
    # The test database returns naive datetimes that are UTC.
    first = (date if date.tzinfo is None else date.astimezone(UTC)).replace(
        minute=0, second=0, microsecond=0, tzinfo=None
    )
    times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(7)]
    return {
        "time": times,
        "temperature_2m": [temperature] * 7,
        "apparent_temperature": [None if temperature is None else temperature - 2] * 7,
        "precipitation": [precipitation] * 7,
        "wind_speed_10m": [None if temperature is None else 12.0] * 7,
        "weather_code": [None if temperature is None else 0] * 7,
        "is_day": [None if temperature is None else 0] * 7,
    }


def provider_response(hourly: dict[str, list[Any]]) -> MagicMock:
    resp = MagicMock()
    resp.is_error = False
    resp.json.return_value = {"hourly": hourly}
    return resp


@pytest.fixture()
def mock_get() -> Iterator[AsyncMock]:
    """Patch the provider call; each test sets return_value or side_effect."""
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mocked:
        yield mocked


def serve(mock_get: AsyncMock, meetup: Meetup, temperature: float | None = 20.0, precipitation: float | None = 0.0):
    mock_get.return_value = provider_response(hourly_for(meetup.date, temperature, precipitation))


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


def started(hours_ago: float) -> datetime:
    """On the hour, so the window has exactly six hourly points."""
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).replace(minute=0, second=0, microsecond=0)


def located(session: Session, org: Organization, *, hours_ago: float, url: str = "t1") -> Meetup:
    """A meetup that started `hours_ago` hours ago, already at LOCATION."""
    meetup = make_meetup(session, org=org, date=started(hours_ago), mazmo_meetup_url=f"https://mazmo.net/test/{url}")
    meetup.location_label = LOCATION["location_label"]
    meetup.latitude = LAT
    meetup.longitude = LON
    session.add(meetup)
    session.flush()
    return meetup


def unlocated(session: Session, org: Organization, *, hours_ago: float) -> Meetup:
    return make_meetup(session, org=org, date=started(hours_ago))


def stored_row(session: Session, meetup: Meetup, **overrides: Any) -> MeetupWeather:
    values: dict[str, Any] = {
        "avg_temperature": 5.0,
        "avg_apparent_temperature": 3.0,
        "total_precipitation_mm": 1.2,
        "max_wind_kmh": 30.0,
        "rained": True,
        "hourly": {"time": []},
        "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
        "source": SOURCE,
    } | overrides
    row = MeetupWeather(meetup_id=meetup.id, **values)
    session.add(row)
    session.flush()
    return row


def row_of(session: Session, meetup: Meetup) -> MeetupWeather | None:
    session.expire_all()
    return session.get(MeetupWeather, meetup.id)


def location_url(meetup: Meetup) -> str:
    return f"/organizations/{meetup.org_id}/meetups/{meetup.id}/location"


def weather_url(meetup: Meetup) -> str:
    return f"/organizations/{meetup.org_id}/meetups/{meetup.id}/weather"


def refresh_url(meetup: Meetup) -> str:
    return f"{weather_url(meetup)}/refresh"


# -- PATCH location: inline fetch ---------------------------------------------


def test_set_location_on_a_ready_meetup_fetches_the_weather_inline(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Staff who fix the venue after the event see the weather at once, not on the next worker pass."""
    meetup = unlocated(session, org, hours_ago=12)
    serve(mock_get, meetup, temperature=20.0, precipitation=0.5)

    resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["latitude"] == LAT
    row = row_of(session, meetup)
    assert row is not None
    assert (row.avg_temperature, row.total_precipitation_mm, row.rained) == (20.0, 3.0, True)
    assert row.source == SOURCE
    assert mock_get.await_count == 1
    assert mock_get.await_args.kwargs["params"]["latitude"] == LAT
    assert mock_get.await_args.kwargs["params"]["longitude"] == LON


def test_set_location_holds_no_transaction_on_the_request_session_during_the_inline_fetch(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: The request session must not sit idle in a transaction through the up to 5 s provider call."""
    meetup = unlocated(session, org, hours_ago=12)
    in_transaction: list[bool] = []

    async def fake_fetch(*args: object) -> None:
        in_transaction.append(session.in_transaction())

    with patch("app.routers.meetups._fetch_weather_after_location_change", fake_fetch):
        resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert in_transaction == [False]
    assert resp.json()["latitude"] == LAT


def test_set_location_on_a_meetup_that_is_not_ready_does_not_fetch(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: The window is not over yet (or the provider has not published it), so there is nothing to fetch."""
    meetup = unlocated(session, org, hours_ago=3)

    resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert mock_get.await_count == 0
    assert row_of(session, meetup) is None


def test_set_location_returns_200_and_stores_no_row_when_the_provider_is_down(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: An outage of Open-Meteo must never block saving a venue; the worker picks the meetup up later."""
    meetup = unlocated(session, org, hours_ago=12)
    mock_get.side_effect = httpx.ConnectTimeout("slow")

    resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["location_label"] == LOCATION["location_label"]
    assert row_of(session, meetup) is None


def test_set_location_returns_200_when_the_provider_has_no_data(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: NoWeatherDataError is a provider failure too; the save still succeeds and nothing is stored."""
    meetup = unlocated(session, org, hours_ago=12)
    serve(mock_get, meetup, temperature=None, precipitation=None)

    resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert row_of(session, meetup) is None


def test_set_location_returns_200_when_the_inline_fetch_raises_unexpectedly(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: The location save must never fail because of weather, whatever goes wrong in the fetch."""
    meetup = unlocated(session, org, hours_ago=12)

    with patch("app.routers.meetups.fetch_and_store_weather", AsyncMock(side_effect=RuntimeError("boom"))):
        resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["latitude"] == LAT
    assert row_of(session, meetup) is None
    session.expire_all()
    assert session.get(Meetup, meetup.id).latitude == LAT  # type: ignore[union-attr]


def test_set_location_returns_200_with_the_saved_body_when_the_inline_fetch_hits_a_database_error(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: A database error in the inline fetch must stay in its own session, not break the request's one."""
    meetup = unlocated(session, org, hours_ago=12)
    fetch_sessions: list[Session] = []

    async def failing_fetch(fetch_session: Session, *args: object) -> None:
        fetch_sessions.append(fetch_session)
        raise OperationalError("INSERT INTO meetup_weather", {}, Exception("connection lost"))

    with patch("app.routers.meetups.fetch_and_store_weather", failing_fetch):
        resp = client.patch(location_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "id": str(meetup.id),
        "name": "Test Meetup",
        "mazmo_meetup_url": meetup.mazmo_meetup_url,
        "date": meetup.date.isoformat(),
        "is_finalized": False,
        "finalized_at": None,
        "requires_payment": False,
        **LOCATION,
    }
    assert len(fetch_sessions) == 1
    assert fetch_sessions[0] is not session
    assert row_of(session, meetup) is None


def test_moving_the_location_replaces_the_old_weather_with_the_new_place(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Weather of the old address no longer describes the event; it is dropped, then refetched."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)
    rows_at_fetch: list[MeetupWeather | None] = []

    def provider(*args: object, **kwargs: object) -> MagicMock:
        rows_at_fetch.append(session.get(MeetupWeather, meetup.id, populate_existing=True))
        return provider_response(hourly_for(meetup.date, 25.0, 0.0))

    mock_get.side_effect = provider

    resp = client.patch(location_url(meetup), json=MOVED, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert rows_at_fetch == [None]  # the old row was already gone when the provider was asked
    params = mock_get.await_args.kwargs["params"]
    assert (params["latitude"], params["longitude"]) == (MOVED["latitude"], MOVED["longitude"])
    row = row_of(session, meetup)
    assert row is not None
    assert (row.avg_temperature, row.rained) == (25.0, False)
    assert row.fetched_at > datetime(2026, 1, 2, tzinfo=UTC)


def test_moving_the_location_while_the_provider_is_down_leaves_no_stale_row(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: A row for the old address must not outlive the move, even if the refetch fails."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup)
    mock_get.side_effect = httpx.ConnectTimeout("slow")

    resp = client.patch(location_url(meetup), json=MOVED, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert row_of(session, meetup) is None


def test_clearing_the_location_deletes_the_weather_row(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Without a place there is no weather to show, and nothing to fetch."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup)

    resp = client.patch(location_url(meetup), json=CLEARED, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert row_of(session, meetup) is None
    assert mock_get.await_count == 0


def test_changing_only_the_label_keeps_the_weather_row_and_does_not_refetch(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Same coordinates, same place: the stored weather is still right."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)

    resp = client.patch(
        location_url(meetup), json={**LOCATION, "location_label": "Vanta (entrada por Soler)"}, headers=admin_headers
    )

    assert resp.status_code == status.HTTP_200_OK
    row = row_of(session, meetup)
    assert row is not None
    assert (row.avg_temperature, row.fetched_at) == (5.0, datetime(2026, 1, 1, tzinfo=UTC))
    assert mock_get.await_count == 0


# -- POST weather/refresh -----------------------------------------------------


def test_refresh_overwrites_the_row_and_returns_it(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Admins re-pull the weather when the first answer was wrong or incomplete."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)
    serve(mock_get, meetup, temperature=22.0, precipitation=0.0)

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert (data["avg_temperature"], data["avg_apparent_temperature"]) == (22.0, 20.0)
    assert (data["total_precipitation_mm"], data["max_wind_kmh"], data["rained"]) == (0.0, 12.0, False)
    assert data["source"] == SOURCE
    assert "hourly" not in data
    assert datetime.fromisoformat(data["fetched_at"]) > datetime(2026, 1, 2, tzinfo=UTC)
    row = row_of(session, meetup)
    assert row is not None
    assert row.avg_temperature == 22.0


def test_refresh_creates_the_row_when_none_was_fetched_yet(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Refresh is also the manual way to fetch what the worker has not fetched yet."""
    meetup = located(session, org, hours_ago=12)
    serve(mock_get, meetup, temperature=18.0, precipitation=0.1)

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["avg_temperature"] == 18.0
    assert row_of(session, meetup) is not None


def test_refresh_returns_409_without_a_location(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: No coordinates, nothing to ask the provider; the message says to set a location first."""
    meetup = unlocated(session, org, hours_ago=12)

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json()["detail"] == NO_LOCATION_DETAIL
    assert mock_get.await_count == 0


@pytest.mark.parametrize("hours_ago", [-2, 3, 7], ids=["upcoming", "in-progress", "window-over-provider-delay"])
def test_refresh_returns_409_before_the_weather_is_available(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    org: Organization,
    mock_get: AsyncMock,
    hours_ago: float,
):
    """WHY: PENDING covers upcoming, running and just-finished meetups; the message names when it opens up."""
    meetup = located(session, org, hours_ago=hours_ago)

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json()["detail"] == too_early_detail(meetup)
    assert mock_get.await_count == 0


def test_refresh_returns_502_when_the_provider_has_no_data(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: 'No data for that place and time' is not an outage; the message tells the admin to check the venue."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)
    serve(mock_get, meetup, temperature=None, precipitation=None)

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp.json()["detail"] == NO_DATA_DETAIL
    row = row_of(session, meetup)
    assert row is not None
    assert row.avg_temperature == 5.0


def test_refresh_returns_502_when_the_provider_is_unavailable(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: An error answer or a dropped connection is retryable; the message says so and differs from the others."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)
    mock_get.side_effect = httpx.ConnectError("down")

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert resp.json()["detail"] == UNAVAILABLE_DETAIL
    row = row_of(session, meetup)
    assert row is not None
    assert row.avg_temperature == 5.0


def test_refresh_returns_504_when_the_provider_does_not_respond(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: CLAUDE.md maps a provider that does not respond to 504; the stored snapshot stays untouched."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, avg_temperature=5.0)
    mock_get.side_effect = httpx.ConnectTimeout("slow")

    resp = client.post(refresh_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert resp.json()["detail"] == TIMEOUT_DETAIL
    row = row_of(session, meetup)
    assert row is not None
    assert row.avg_temperature == 5.0


def test_refresh_logs_a_provider_failure_once_with_the_meetup_and_detail(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: The client no longer logs, so the one warning is the caller's and carries the meetup id."""
    meetup = located(session, org, hours_ago=12)
    failed = MagicMock()
    failed.is_error = True
    failed.status_code = 503
    mock_get.return_value = failed

    with capture_logs() as logs:
        client.post(refresh_url(meetup), headers=admin_headers)

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "Weather refresh failed"
    assert warnings[0]["meetup_id"] == str(meetup.id)
    assert warnings[0]["detail"] == "Open-Meteo returned HTTP 503"


def test_refresh_returns_403_for_org_staff(
    client: TestClient, staff_headers: dict, session: Session, org: Organization, org_staff_member
):
    """WHY: Only org admins trigger outbound calls."""
    meetup = located(session, org, hours_ago=12)

    resp = client.post(refresh_url(meetup), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_refresh_returns_401_without_auth(client: TestClient, session: Session, org: Organization):
    """WHY: The route is not public."""
    meetup = located(session, org, hours_ago=12)

    assert client.post(refresh_url(meetup)).status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_returns_404_for_a_meetup_of_another_org(
    client: TestClient, admin_headers: dict, session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Org admins must not touch another org's meetups."""
    foreign = located(session, make_org(session, name="Other", slug="other"), hours_ago=12, url="t2")

    resp = client.post(f"/organizations/{org.id}/meetups/{foreign.id}/weather/refresh", headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert mock_get.await_count == 0


# -- GET weather --------------------------------------------------------------


def test_read_reports_no_location(client: TestClient, admin_headers: dict, session: Session, org: Organization):
    """WHY: Without coordinates the UI offers to set a location, not to wait."""
    meetup = unlocated(session, org, hours_ago=12)

    resp = client.get(weather_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"status": "NO_LOCATION", "weather": None, "ready": False}


def test_read_reports_pending_while_the_window_is_not_over_plus_two_hours(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: Located but too recent: the weather will come, so the UI says to wait."""
    meetup = located(session, org, hours_ago=3)

    resp = client.get(weather_url(meetup), headers=admin_headers)

    assert resp.json() == {"status": "PENDING", "weather": None, "ready": False}


def test_read_reports_not_fetched_when_ready_without_a_row(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: Ready but empty means the worker has not got to it (or the provider failed): the UI offers a refresh."""
    meetup = located(session, org, hours_ago=12)

    resp = client.get(weather_url(meetup), headers=admin_headers)

    assert resp.json() == {"status": "NOT_FETCHED", "weather": None, "ready": True}


def test_read_returns_the_stored_weather(client: TestClient, admin_headers: dict, session: Session, org: Organization):
    """WHY: The meetup page shows the stored summary; hourly arrays stay out of the response."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup)

    resp = client.get(weather_url(meetup), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "OK"
    assert body["ready"] is True
    weather = body["weather"]
    assert weather["avg_temperature"] == 5.0
    assert weather["avg_apparent_temperature"] == 3.0
    assert weather["total_precipitation_mm"] == 1.2
    assert weather["max_wind_kmh"] == 30.0
    assert weather["rained"] is True
    assert weather["source"] == SOURCE
    assert datetime.fromisoformat(weather["fetched_at"]) == datetime(2026, 1, 1, tzinfo=UTC)
    assert set(weather) == {
        "avg_temperature",
        "avg_apparent_temperature",
        "total_precipitation_mm",
        "max_wind_kmh",
        "rained",
        "fetched_at",
        "source",
    }


def test_read_returns_explicit_nulls_for_series_the_provider_lacked(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: A row can lack a series (velvet shows 'Sin dato'); rained is null, not false, when rain is unknown."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup, total_precipitation_mm=None, rained=None, max_wind_kmh=None)

    weather = client.get(weather_url(meetup), headers=admin_headers).json()["weather"]

    assert (weather["total_precipitation_mm"], weather["rained"], weather["max_wind_kmh"]) == (None, None, None)
    assert weather["avg_temperature"] == 5.0


def test_read_reports_ok_when_a_row_exists(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: A stored row wins over the readiness clock."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup)

    assert client.get(weather_url(meetup), headers=admin_headers).json()["status"] == "OK"


def test_read_is_allowed_for_org_staff(
    client: TestClient, staff_headers: dict, session: Session, org: Organization, org_staff_member
):
    """WHY: Every member sees the weather, only admins refresh it."""
    meetup = located(session, org, hours_ago=12)
    stored_row(session, meetup)

    resp = client.get(weather_url(meetup), headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "OK"


def test_read_returns_401_without_auth(client: TestClient, session: Session, org: Organization):
    """WHY: The route is not public."""
    meetup = located(session, org, hours_ago=12)

    assert client.get(weather_url(meetup)).status_code == status.HTTP_401_UNAUTHORIZED


def test_read_returns_404_for_a_meetup_of_another_org(
    client: TestClient, admin_headers: dict, session: Session, org: Organization
):
    """WHY: Meetups of another org do not exist for this one."""
    foreign = located(session, make_org(session, name="Other", slug="other"), hours_ago=12, url="t2")

    resp = client.get(f"/organizations/{org.id}/meetups/{foreign.id}/weather", headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
