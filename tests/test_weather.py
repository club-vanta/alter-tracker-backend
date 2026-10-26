"""Tests for the weather service: summary math, readiness, OpenMeteoClient and the upsert.

Open-Meteo is never called: httpx.AsyncClient.get is patched, like tests/test_geocoding.py.
"""

import asyncio
import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlmodel import Session, col, select, update
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.models.models import Meetup, MeetupWeather, Organization
from app.services.weather import (
    SOURCE,
    MissingLocationError,
    NoWeatherDataError,
    OpenMeteoClient,
    WeatherLocationChangedError,
    WeatherTimeoutError,
    WeatherUnavailableError,
    fetch_and_store_weather,
    is_weather_ready,
    summarize_weather,
    weather_ready_at,
    weather_too_early_detail,
)
from tests.conftest import make_meetup

START = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
LAT = -34.5875
LON = -58.4306


def hourly_payload(
    temperature: list[float | None],
    apparent: list[float | None],
    precipitation: list[float | None],
    wind: list[float | None],
    first_hour: datetime = datetime(2026, 10, 24, 20, 0),
) -> dict[str, list[Any]]:
    """Build an Open-Meteo hourly block; times are naive UTC strings like the provider sends."""
    times = [(first_hour + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(temperature))]
    return {
        "time": times,
        "temperature_2m": temperature,
        "apparent_temperature": apparent,
        "precipitation": precipitation,
        "wind_speed_10m": wind,
        "weather_code": [3] * len(times),
        "is_day": [0] * len(times),
    }


def window_payload(precipitation: list[float | None] | None = None) -> dict[str, list[Any]]:
    """Ten hours from 20:00 UTC; the window for START uses 22:00 through 04:00 (7 points)."""
    return hourly_payload(
        temperature=[99.0, 99.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 99.0],
        apparent=[99.0, 99.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 99.0],
        precipitation=precipitation or [9.0, 9.0, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 9.0],
        wind=[99.0, 99.0, 5.0, 10.0, 15.0, 20.0, 12.5, 7.0, 3.0, 99.0],
    )


def api_response(hourly: dict[str, list[Any]]) -> MagicMock:
    resp = MagicMock()
    resp.is_error = False
    resp.json.return_value = {"hourly": hourly}
    return resp


@pytest.fixture(autouse=True)
def mock_get() -> Iterator[AsyncMock]:
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mocked:
        mocked.return_value = api_response(window_payload())
        yield mocked


# -- summarize_weather --------------------------------------------------------


def test_summarize_weather_computes_exact_stats_over_the_window_hours_only():
    """WHY: Hours before floor_hour(start) belong to the before window, not to the during averages."""
    summary = summarize_weather(window_payload(), START)

    assert summary is not None
    # During hours 22,23,00,01,02,03,04 -> temps 10..22, apparent 8..20, wind 5..3
    assert summary.avg_temperature == 16.0
    assert summary.avg_apparent_temperature == 14.0
    assert summary.total_precipitation_mm == 0.1
    assert summary.max_wind_kmh == 20.0
    assert summary.rained is False


def test_summarize_weather_rounds_to_one_decimal():
    """WHY: The stored values are shown as is; no float noise like 0.30000000000000004."""
    payload = hourly_payload(
        temperature=[10.0, 10.0, 11.0],
        apparent=[1.0, 1.0, 2.0],
        precipitation=[0.1, 0.2, 0.0],
        wind=[10.04, 10.06, 1.0],
        first_hour=datetime(2026, 10, 24, 22, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.avg_temperature == 10.3
    assert summary.avg_apparent_temperature == 1.3
    assert summary.total_precipitation_mm == 0.3
    assert summary.max_wind_kmh == 10.1


def test_summarize_weather_rained_is_true_at_exactly_two_tenths():
    """WHY: Spec rule rained = total >= 0.2."""
    payload = window_payload([0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.total_precipitation_mm == 0.2
    assert summary.rained is True


def test_summarize_weather_rained_is_false_at_one_tenth():
    """WHY: A drizzle below 0.2 mm counts as dry."""
    payload = window_payload([0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.total_precipitation_mm == 0.1
    assert summary.rained is False


def test_summarize_weather_skips_nulls_per_series_independently():
    """WHY: A missing temperature at an hour must not drop that hour's precipitation (provider gaps)."""
    payload = hourly_payload(
        temperature=[None, 12.0, 14.0],
        apparent=[8.0, None, None],
        precipitation=[0.5, 0.5, None],
        wind=[None, None, 9.0],
        first_hour=datetime(2026, 10, 24, 22, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.avg_temperature == 13.0
    assert summary.avg_apparent_temperature == 8.0
    assert summary.total_precipitation_mm == 1.0
    assert summary.max_wind_kmh == 9.0
    assert summary.rained is True


def test_summarize_weather_keeps_a_series_that_is_entirely_null_as_none():
    """WHY: Only when every series is empty is there nothing to store; one empty series stays None."""
    payload = hourly_payload(
        temperature=[10.0, 12.0],
        apparent=[None, None],
        precipitation=[0.0, 0.0],
        wind=[4.0, 6.0],
        first_hour=datetime(2026, 10, 24, 22, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.avg_apparent_temperature is None
    assert summary.avg_temperature == 11.0


def test_summarize_weather_rained_is_unknown_without_any_precipitation_value():
    """WHY: Unknown precipitation is not the same as dry; rained stays NULL."""
    payload = hourly_payload(
        temperature=[10.0, 12.0],
        apparent=[9.0, 11.0],
        precipitation=[None, None],
        wind=[4.0, 6.0],
        first_hour=datetime(2026, 10, 24, 22, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.total_precipitation_mm is None
    assert summary.rained is None


def test_summarize_weather_returns_none_when_every_series_is_null_in_the_window():
    """WHY: Review focus 4: all-null data means no row is written."""
    payload = hourly_payload(
        temperature=[None] * 4,
        apparent=[None] * 4,
        precipitation=[None] * 4,
        wind=[None] * 4,
        first_hour=datetime(2026, 10, 24, 22, 0),
    )

    assert summarize_weather(payload, START) is None


def test_summarize_weather_ignores_data_outside_the_window():
    """WHY: Values only outside the window count as no data."""
    payload = hourly_payload(
        temperature=[10.0, 11.0],
        apparent=[10.0, 11.0],
        precipitation=[1.0, 1.0],
        wind=[10.0, 11.0],
        first_hour=datetime(2026, 10, 25, 6, 0),
    )

    assert summarize_weather(payload, START) is None


def test_summarize_weather_excludes_the_hour_at_start_plus_six():
    """WHY: The window is half open: start 22:00 sharp ends before 04:00."""
    start = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)

    summary = summarize_weather(window_payload(), start)

    assert summary is not None
    assert summary.hourly["time"][-1] == "2026-10-25T03:00"
    # 20:00 and 21:00 are before hours (the payload has no 19:00), then 22:00 through 03:00.
    assert len(summary.hourly["time"]) == 8


def before_payload() -> dict[str, list[Any]]:
    """Twelve hours from 18:00 UTC: 18:00 is outside, 19-21 are before START (22:30), 22-04 during, 05 outside."""
    return hourly_payload(
        temperature=[99.0, 14.0, 13.0, 12.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 99.0],
        apparent=[99.0, 11.0, 10.0, 9.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 99.0],
        precipitation=[9.0, 1.5, 2.0, 0.6, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 9.0],
        wind=[99.0, 30.0, 30.0, 30.0, 5.0, 10.0, 15.0, 20.0, 12.5, 7.0, 3.0, 99.0],
        first_hour=datetime(2026, 10, 24, 18, 0),
    )


def test_summarize_weather_splits_before_and_during_for_a_half_past_start():
    """WHY: Rain in the 3 hours before the start is what people saw while deciding to go out."""
    summary = summarize_weather(before_payload(), START)

    assert summary is not None
    assert summary.before.avg_temperature == 13.0
    assert summary.before.avg_apparent_temperature == 10.0
    assert summary.before.total_precipitation_mm == 4.1
    assert summary.before.rained is True
    assert summary.avg_temperature == 16.0
    assert summary.total_precipitation_mm == 0.1
    assert summary.rained is False
    # Wind stays a during figure: the 30 km/h before hours do not count.
    assert summary.max_wind_kmh == 20.0


def test_summarize_weather_keeps_the_whole_window_in_hourly():
    """WHY: The stats tab charts every hour from 3 before the start, with its weather code and day flag."""
    summary = summarize_weather(before_payload(), START)

    assert summary is not None
    assert summary.hourly["time"][0] == "2026-10-24T19:00"
    assert summary.hourly["time"][-1] == "2026-10-25T04:00"
    assert len(summary.hourly["time"]) == 10
    assert summary.hourly["weather_code"] == [3] * 10
    assert summary.hourly["is_day"] == [0] * 10


def test_summarize_weather_before_is_all_null_when_its_hours_are_null():
    """WHY: Unknown weather before the start is not the same as dry; the during summary still stands."""
    payload = hourly_payload(
        temperature=[None, None, None, 10.0],
        apparent=[None, None, None, 8.0],
        precipitation=[None, None, None, 0.0],
        wind=[None, None, None, 5.0],
        first_hour=datetime(2026, 10, 24, 19, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.before.avg_temperature is None
    assert summary.before.avg_apparent_temperature is None
    assert summary.before.total_precipitation_mm is None
    assert summary.before.rained is None
    assert summary.avg_temperature == 10.0


def test_summarize_weather_keeps_before_when_during_is_null():
    """WHY: A provider gap after the start must not throw away the before data."""
    payload = hourly_payload(
        temperature=[12.0, 12.0, 12.0, None],
        apparent=[10.0, 10.0, 10.0, None],
        precipitation=[0.0, 0.0, 0.0, None],
        wind=[5.0, 5.0, 5.0, None],
        first_hour=datetime(2026, 10, 24, 19, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.before.avg_temperature == 12.0
    assert summary.before.rained is False
    assert summary.avg_temperature is None
    assert summary.rained is None


def test_summarize_weather_ignores_hours_more_than_three_before_the_start_hour():
    """WHY: The before window is exactly 3 hours; an 18:00 storm for a 22:30 start does not count."""
    payload = hourly_payload(
        temperature=[None, 10.0],
        apparent=[None, 10.0],
        precipitation=[9.0, 0.0],
        wind=[None, 5.0],
        first_hour=datetime(2026, 10, 24, 18, 0),
    )

    summary = summarize_weather(payload, START)

    assert summary is not None
    assert summary.before.total_precipitation_mm == 0.0
    assert summary.hourly["time"] == ["2026-10-24T19:00"]


def test_summarize_weather_treats_the_start_in_utc():
    """WHY: A start expressed in another zone must still select the same UTC hours."""
    local_start = START.astimezone(timezone(timedelta(hours=-3)))

    assert summarize_weather(window_payload(), local_start) == summarize_weather(window_payload(), START)


# -- is_weather_ready ---------------------------------------------------------


def _meetup_at(date: datetime, latitude: float | None = LAT, longitude: float | None = LON) -> MagicMock:
    meetup = MagicMock()
    meetup.date = date
    meetup.latitude = latitude
    meetup.longitude = longitude
    return meetup


def test_is_weather_ready_is_false_without_coordinates():
    """WHY: No location, nothing to look up."""
    meetup = _meetup_at(START, latitude=None, longitude=None)

    assert is_weather_ready(meetup, START + timedelta(days=3)) is False


def test_is_weather_ready_is_false_with_only_one_coordinate():
    """WHY: Latitude and longitude are set together; half a pair is not a place."""
    assert is_weather_ready(_meetup_at(START, longitude=None), START + timedelta(days=3)) is False


def test_is_weather_ready_is_true_exactly_eight_hours_after_start():
    """WHY: Window (6h) plus the 2h provider delay: the boundary is inclusive."""
    assert is_weather_ready(_meetup_at(START), START + timedelta(hours=8)) is True


def test_is_weather_ready_is_false_seven_hours_fifty_nine_minutes_after_start():
    """WHY: One minute short of the 8h mark is still pending."""
    assert is_weather_ready(_meetup_at(START), START + timedelta(hours=7, minutes=59)) is False


# -- OpenMeteoClient ----------------------------------------------------------


def test_client_sends_the_exact_query_params(mock_get: AsyncMock):
    """WHY: The window dates, variables, UTC and km/h units are the contract with Open-Meteo."""

    async def run() -> dict[str, list[Any]]:
        async with OpenMeteoClient(get_settings()) as client:
            return await client.fetch_hourly(LAT, LON, START)

    hourly = asyncio.run(run())

    mock_get.assert_awaited_once_with(
        get_settings().open_meteo_base_url,
        params={
            "latitude": LAT,
            "longitude": LON,
            "start_date": "2026-10-24",
            "end_date": "2026-10-25",
            "hourly": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code,is_day",
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        },
    )
    assert hourly["time"][0] == "2026-10-24T20:00"


def test_client_uses_one_date_when_the_window_stays_inside_a_day(mock_get: AsyncMock):
    """WHY: A 12:00 UTC start ends at 18:00 the same day; asking for the next day wastes data."""

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, datetime(2026, 10, 24, 12, 0, tzinfo=UTC))

    asyncio.run(run())

    params = mock_get.await_args.kwargs["params"]
    assert params["start_date"] == params["end_date"] == "2026-10-24"


def test_client_asks_for_the_previous_day_when_before_crosses_midnight(mock_get: AsyncMock):
    """WHY: A 01:30 UTC start has its before hours (22:00 to 01:00) on the previous UTC date."""

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, datetime(2026, 10, 25, 1, 30, tzinfo=UTC))

    asyncio.run(run())

    params = mock_get.await_args.kwargs["params"]
    assert params["start_date"] == "2026-10-24"
    assert params["end_date"] == "2026-10-25"


def test_client_does_not_ask_for_the_next_day_when_the_window_ends_at_midnight(mock_get: AsyncMock):
    """WHY: The window is half open, so a 18:00 start ends exactly at 00:00 and needs no 25th."""

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, datetime(2026, 10, 24, 18, 0, tzinfo=UTC))

    asyncio.run(run())

    assert mock_get.await_args.kwargs["params"]["end_date"] == "2026-10-24"


def test_client_sends_an_identifying_user_agent_and_a_5_second_timeout():
    """WHY: Identify the app to the free API; a slow provider must fail fast."""

    async def run() -> None:
        async with OpenMeteoClient(get_settings()):
            pass

    with patch.object(httpx.AsyncClient, "__init__", autospec=True, side_effect=httpx.AsyncClient.__init__) as init:
        asyncio.run(run())

    assert init.call_args.kwargs["headers"]["User-Agent"] == "alter-tracker/1.0 (club-vanta.com)"
    assert init.call_args.kwargs["timeout"] == 5.0


def test_client_maps_a_connection_error_to_weather_unavailable(mock_get: AsyncMock):
    """WHY: Callers handle one error type whatever went wrong on the wire."""
    mock_get.side_effect = httpx.ConnectError("down")

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherUnavailableError, match="Open-Meteo request failed: ConnectError") as raised:
        asyncio.run(run())

    assert not isinstance(raised.value, WeatherTimeoutError)


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")])
def test_client_maps_a_timeout_to_weather_timeout(mock_get: AsyncMock, error: httpx.TimeoutException):
    """WHY: A provider that does not answer is a 504 for the refresh route, not a 502."""
    mock_get.side_effect = error

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherTimeoutError, match="Open-Meteo request timed out"):
        asyncio.run(run())


def test_client_does_not_log_provider_failures(mock_get: AsyncMock):
    """WHY: The caller logs each failure once, with the meetup id; the client only raises."""
    mock_get.side_effect = httpx.ReadTimeout("slow")

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with capture_logs() as logs, pytest.raises(WeatherUnavailableError):
        asyncio.run(run())

    assert logs == []


def test_client_maps_a_5xx_to_weather_unavailable(mock_get: AsyncMock):
    """WHY: A provider outage must not surface as a crash."""
    failure = MagicMock()
    failure.is_error = True
    failure.status_code = 503
    mock_get.return_value = failure

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherUnavailableError, match="503"):
        asyncio.run(run())


@pytest.mark.parametrize("body", [{}, {"hourly": {}}, {"hourly": {"time": ["x"]}}, ["not", "a", "dict"]])
def test_client_maps_unreadable_json_to_weather_unavailable(mock_get: AsyncMock, body: Any):
    """WHY: A changed payload shape is an outage for us, not a KeyError."""
    resp = MagicMock()
    resp.is_error = False
    resp.json.return_value = body
    mock_get.return_value = resp

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherUnavailableError):
        asyncio.run(run())


def _hourly_with(**overrides: list[Any]) -> dict[str, list[Any]]:
    hourly = hourly_payload([1.0, 2.0], [1.0, 2.0], [0.0, 0.0], [3.0, 4.0], first_hour=datetime(2026, 10, 24, 22, 0))
    hourly.update(overrides)
    return hourly


@pytest.mark.parametrize(
    "hourly",
    [
        _hourly_with(time=["2026-10-24T22:00", None]),
        _hourly_with(time=["2026-10-24T22:00", "not a time"]),
        _hourly_with(time=["2026-10-24T22:00", 5]),
        _hourly_with(temperature_2m=[1.0, "warm"]),
        _hourly_with(precipitation=[0.0, [1]]),
        _hourly_with(wind_speed_10m=[True, 2.0]),
        _hourly_with(apparent_temperature=[1.0]),
    ],
    ids=["null-time", "bad-time", "numeric-time", "string-value", "list-value", "bool-value", "short-array"],
)
def test_client_maps_malformed_values_to_weather_unavailable(mock_get: AsyncMock, hourly: dict[str, list[Any]]):
    """WHY: Bad provider data must surface as WeatherUnavailableError, never a raw ValueError or TypeError."""
    mock_get.return_value = api_response(hourly)

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherUnavailableError, match="unexpected format"):
        asyncio.run(run())


def test_client_maps_invalid_json_to_weather_unavailable(mock_get: AsyncMock):
    """WHY: A non-JSON 200 (proxy page) must not escape as ValueError."""
    resp = MagicMock()
    resp.is_error = False
    resp.json.side_effect = ValueError("no json")
    mock_get.return_value = resp

    async def run() -> None:
        async with OpenMeteoClient(get_settings()) as client:
            await client.fetch_hourly(LAT, LON, START)

    with pytest.raises(WeatherUnavailableError):
        asyncio.run(run())


# -- fetch_and_store_weather --------------------------------------------------


def _fetch(session: Session, meetup: Any) -> MeetupWeather:
    async def run() -> MeetupWeather:
        async with OpenMeteoClient(get_settings()) as client:
            return await fetch_and_store_weather(session, meetup, client)

    return asyncio.run(run())


def _located_meetup(session: Session, org: Organization):
    meetup = make_meetup(session, org=org, date=START)
    meetup.latitude = LAT
    meetup.longitude = LON
    session.add(meetup)
    session.flush()
    return meetup


def test_fetch_and_store_weather_writes_the_row_with_source_and_timestamp(
    session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: The stored row is what the stats and the read endpoint serve."""
    meetup = _located_meetup(session, org)
    before = datetime.now(UTC)

    row = _fetch(session, meetup)

    assert row.meetup_id == meetup.id
    assert row.avg_temperature == 16.0
    assert row.avg_apparent_temperature == 14.0
    assert row.total_precipitation_mm == 0.1
    assert row.max_wind_kmh == 20.0
    assert row.rained is False
    assert row.source == SOURCE == "open-meteo-historical-forecast"
    assert before <= row.fetched_at <= datetime.now(UTC)
    # window_payload starts at 20:00, so the stored window runs 20:00 (before) through 04:00 (during).
    assert row.hourly["time"][0] == "2026-10-24T20:00"
    assert len(row.hourly["time"]) == 9
    assert mock_get.await_args.kwargs["params"]["latitude"] == LAT


def test_fetch_and_store_weather_writes_the_before_summary(session: Session, org: Organization, mock_get: AsyncMock):
    """WHY: The stats tab compares the weather before the start with the weather during it."""
    mock_get.return_value = api_response(before_payload())
    meetup = _located_meetup(session, org)

    row = _fetch(session, meetup)

    assert row.before_avg_temperature == 13.0
    assert row.before_avg_apparent_temperature == 10.0
    assert row.before_total_precipitation_mm == 4.1
    assert row.before_rained is True
    assert row.hourly["time"][0] == "2026-10-24T19:00"
    assert row.hourly["weather_code"] == [3] * 10


def test_fetch_and_store_weather_second_call_overwrites_and_keeps_one_row(
    session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Refresh and a worker overlap must never create duplicates (review focus 2)."""
    meetup = _located_meetup(session, org)
    _fetch(session, meetup)
    mock_get.return_value = api_response(window_payload([0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))

    row = _fetch(session, meetup)

    rows = session.exec(select(MeetupWeather).where(MeetupWeather.meetup_id == meetup.id)).all()
    assert len(rows) == 1
    assert row.total_precipitation_mm == 2.0
    assert row.rained is True
    assert rows[0].total_precipitation_mm == 2.0


def test_fetch_and_store_weather_raises_no_data_and_writes_nothing_when_the_window_is_empty(
    session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Review focus 4: all-null data writes no row, so a later run can try again."""
    meetup = _located_meetup(session, org)
    mock_get.return_value = api_response(
        hourly_payload([None] * 3, [None] * 3, [None] * 3, [None] * 3, first_hour=datetime(2026, 10, 24, 22, 0))
    )

    with pytest.raises(NoWeatherDataError):
        _fetch(session, meetup)

    assert session.exec(select(MeetupWeather)).all() == []


def test_no_weather_data_error_is_a_weather_unavailable_error():
    """WHY: Callers that only care about 'provider could not help' catch one type."""
    assert issubclass(NoWeatherDataError, WeatherUnavailableError)


def test_fetch_and_store_weather_requires_a_location(session: Session, org: Organization, mock_get: AsyncMock):
    """WHY: Fetching for a meetup without coordinates is a caller bug; fail before any request."""
    meetup = make_meetup(session, org=org, date=START)

    with pytest.raises(MissingLocationError, match="no coordinates"):
        _fetch(session, meetup)

    mock_get.assert_not_awaited()
    assert session.exec(select(MeetupWeather)).all() == []


def test_fetch_and_store_weather_stores_unknown_rain_as_null(session: Session, org: Organization, mock_get: AsyncMock):
    """WHY: A row with no precipitation values keeps rained NULL instead of claiming it was dry."""
    meetup = _located_meetup(session, org)
    mock_get.return_value = api_response(window_payload([None] * 10))

    row = _fetch(session, meetup)

    assert row.total_precipitation_mm is None
    assert row.rained is None


def test_fetch_and_store_weather_propagates_provider_failures_and_writes_nothing(
    session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: Callers decide what a failure means (502, log and continue); the service must not hide it."""
    meetup = _located_meetup(session, org)
    mock_get.side_effect = httpx.ReadTimeout("slow")

    with pytest.raises(WeatherUnavailableError):
        _fetch(session, meetup)

    assert session.exec(select(MeetupWeather)).all() == []


def test_fetch_and_store_weather_discards_the_answer_when_the_meetup_moved_during_the_fetch(
    session: Session, org: Organization, mock_get: AsyncMock
):
    """WHY: A worker mid-fetch must not store the old place's weather after an admin moved the meetup."""
    meetup = _located_meetup(session, org)
    meetup_id = meetup.id

    def move_while_fetching(*args: Any, **kwargs: Any) -> MagicMock:
        session.exec(update(Meetup).where(col(Meetup.id) == meetup_id).values(latitude=LAT + 1.0))  # type: ignore[arg-type]
        return api_response(window_payload())

    mock_get.side_effect = move_while_fetching

    with pytest.raises(WeatherLocationChangedError):
        _fetch(session, meetup)

    assert session.exec(select(MeetupWeather)).all() == []


def test_weather_location_changed_error_is_a_weather_unavailable_error():
    """WHY: Callers that only log provider trouble (the inline fetch, the refresh route) already handle it."""
    assert issubclass(WeatherLocationChangedError, WeatherUnavailableError)


def test_too_early_detail_takes_its_hours_from_the_readiness_rule():
    """WHY: The 409 text must follow READY_AFTER instead of repeating a number."""
    ready_at = datetime(2026, 10, 25, 6, 30, tzinfo=UTC)

    with patch("app.services.weather.READY_AFTER", timedelta(hours=5)):
        assert weather_too_early_detail(ready_at) == (
            "Cannot refresh weather: it is available 5 hours after the event starts (from 2026-10-25T06:30:00Z). "
            "Try again then."
        )


def test_weather_ready_at_is_eight_hours_after_the_start(session: Session, org: Organization):
    """WHY: The refresh message tells the admin this exact time; the 6h window plus 2h provider delay."""
    meetup = make_meetup(session, org=org, date=datetime(2026, 10, 24, 22, 30, tzinfo=UTC))

    assert weather_ready_at(meetup) == datetime(2026, 10, 25, 6, 30, tzinfo=UTC)


# -- Migration 0025 -----------------------------------------------------------


def _migration_0025() -> ModuleType:
    path = Path(__file__).parent.parent / "alembic" / "versions" / "0025_meetup_weather_before.py"
    spec = importlib.util.spec_from_file_location("migration_0025", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0025_adds_the_before_columns_and_empties_the_table(session: Session, org: Organization):
    """
    WHY: Stored rows lack the before hours, and the worker only fills meetups without a row,
    so the upgrade must delete them. Runs the real downgrade then upgrade inside the test
    transaction (Postgres DDL is transactional, so the rollback restores the schema).
    """
    meetup = _located_meetup(session, org)
    _fetch(session, meetup)
    migration = _migration_0025()
    context = MigrationContext.configure(session.connection())

    with Operations.context(context):
        migration.downgrade()
        migration.upgrade()

    columns = {c["name"] for c in sa_inspect(session.connection()).get_columns("meetup_weather")}
    assert {
        "before_avg_temperature",
        "before_avg_apparent_temperature",
        "before_total_precipitation_mm",
        "before_rained",
    } <= columns
    assert session.exec(text("SELECT count(*) FROM meetup_weather")).one()[0] == 0  # type: ignore[call-overload]
