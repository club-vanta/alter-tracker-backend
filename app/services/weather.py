"""
Weather of a meetup, from Open-Meteo's historical forecast API.

Powers the weather tab of the org statistics. Encapsulates the outbound HTTP
logic and the summary math so routers and the worker stay thin. Mirrors
MazmoClient: an async context manager around one shared httpx.AsyncClient.

Request
-------
  GET {open_meteo_base_url}?latitude=..&longitude=..&start_date=..&end_date=..
      &hourly=temperature_2m,apparent_temperature,precipitation,wind_speed_10m
      &timezone=UTC&wind_speed_unit=kmh
start_date/end_date are the UTC dates covering the window, so a night event
that crosses midnight asks for two days.

Window
------
[floor_hour(date), date + 6h). Hours used: every hourly timestamp t with
floor_hour(date) <= t < date + 6h, so an event at 22:30 uses 22:00 through
04:00 (the hour that contains the start counts).

Summary
-------
Averages of temperature and apparent temperature, the sum of precipitation
and the max wind, each rounded to 1 decimal. Nulls are skipped per series, so
a missing temperature does not drop that hour's precipitation. Nothing is
summarized (None) only when every series is empty inside the window.

Pacing
------
None here: the worker owns the 1 request per second pace.

Errors
------
Transport errors, non-2xx statuses and malformed JSON raise WeatherUnavailableError
(a timeout raises its subclass WeatherTimeoutError, which the refresh route maps
to 504 instead of 502). An answer with no usable value in the window raises its
subclass NoWeatherDataError. The client does not log: the message carries the
HTTP status or the failure type, and the caller logs it once with the meetup id.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import ColumnElement, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col

from app.core.config import Settings
from app.core.outbound import OUTBOUND_HEADERS
from app.models.models import Meetup, MeetupWeather, WeatherStatus
from app.services.timeutil import as_utc

log = structlog.get_logger(__name__)

SOURCE = "open-meteo-historical-forecast"

_HOURLY_VARIABLES = ("temperature_2m", "apparent_temperature", "precipitation", "wind_speed_10m")
_WINDOW = timedelta(hours=6)
# The provider publishes a finished hour with some delay, so wait before the first fetch.
_PROVIDER_DELAY = timedelta(hours=2)
READY_AFTER = _WINDOW + _PROVIDER_DELAY
_RAIN_THRESHOLD_MM = 0.2

type HourlyData = dict[str, list[str | float | None]]

# -- Errors -------------------------------------------------------------------


class WeatherUnavailableError(Exception):
    """Open-Meteo timed out, failed, or returned something we cannot read."""


class WeatherTimeoutError(WeatherUnavailableError):
    """Open-Meteo did not answer within the request timeout."""


class NoWeatherDataError(WeatherUnavailableError):
    """Open-Meteo answered, but every series is empty inside the window (nothing was stored)."""


class MissingLocationError(Exception):
    """fetch_and_store_weather was called for a meetup without coordinates (a caller bug)."""


# -- Summary ------------------------------------------------------------------


@dataclass(frozen=True)
class WeatherSummary:
    """Stats over the window hours, plus the raw arrays they came from."""

    avg_temperature: float | None
    avg_apparent_temperature: float | None
    total_precipitation_mm: float | None
    max_wind_kmh: float | None
    rained: bool | None
    hourly: HourlyData


def _window_end(start: datetime) -> datetime:
    return as_utc(start) + _WINDOW


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def summarize_weather(hourly: HourlyData, start: datetime) -> WeatherSummary | None:
    """Summarize the hourly arrays over [floor_hour(start), start + 6h), or None without any value."""
    window_start = as_utc(start).replace(minute=0, second=0, microsecond=0)
    window_end = _window_end(start)
    times = hourly["time"]
    indexes = [i for i, t in enumerate(times) if window_start <= as_utc(datetime.fromisoformat(str(t))) < window_end]
    used: HourlyData = {"time": [times[i] for i in indexes]}
    present: dict[str, list[float]] = {}
    for name in _HOURLY_VARIABLES:
        series = [hourly[name][i] for i in indexes]
        used[name] = series
        present[name] = [float(v) for v in series if v is not None]

    if not any(present.values()):
        return None

    precipitation = present["precipitation"]
    total = round(sum(precipitation), 1) if precipitation else None
    wind = present["wind_speed_10m"]
    return WeatherSummary(
        avg_temperature=_mean(present["temperature_2m"]),
        avg_apparent_temperature=_mean(present["apparent_temperature"]),
        total_precipitation_mm=total,
        max_wind_kmh=round(max(wind), 1) if wind else None,
        rained=None if total is None else total >= _RAIN_THRESHOLD_MM,
        hourly=used,
    )


# Shared with the weather refresh route so the documented messages are the real ones.
WEATHER_REFRESH_NO_LOCATION_DETAIL = "Cannot refresh weather: the meetup has no location. Set a location first."
WEATHER_NO_DATA_DETAIL = (
    "Cannot refresh weather: the weather provider has no data for this place and time. "
    "Check the meetup location, or try again later."
)
WEATHER_UNAVAILABLE_DETAIL = (
    "Cannot refresh weather: the weather provider is unavailable right now. Try again in a moment."
)
WEATHER_TIMEOUT_DETAIL = "Cannot refresh weather: the weather provider did not respond in time. Try again in a moment."


def weather_too_early_detail(ready_at: datetime) -> str:
    """The 409 message when the weather is not available yet, naming when it will be (UTC)."""
    hours = READY_AFTER // timedelta(hours=1)
    return (
        f"Cannot refresh weather: it is available {hours} hours after the event starts "
        f"(from {ready_at.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}). Try again then."
    )


def weather_ready_at(meetup: Meetup) -> datetime:
    """When the weather of the meetup becomes available: its window plus the provider delay after the start."""
    return as_utc(meetup.date) + READY_AFTER


def is_weather_ready(meetup: Meetup, now: datetime) -> bool:
    """True when the meetup has coordinates and its window ended at least 2 hours ago."""
    if meetup.latitude is None or meetup.longitude is None:
        return False
    return weather_ready_at(meetup) <= as_utc(now)


def weather_status(meetup: Meetup, has_row: bool, now: datetime) -> WeatherStatus:
    """Classify a meetup's weather: no coordinates, then a stored row, then readiness."""
    if meetup.latitude is None or meetup.longitude is None:
        return WeatherStatus.NO_LOCATION
    if has_row:
        return WeatherStatus.OK
    return WeatherStatus.NOT_FETCHED if is_weather_ready(meetup, now) else WeatherStatus.PENDING


def weather_ready_clause(now: datetime) -> ColumnElement[bool]:
    """SQL twin of is_weather_ready, for queries that select ready meetups (keep the two in step)."""
    return and_(
        col(Meetup.latitude).is_not(None),
        col(Meetup.longitude).is_not(None),
        col(Meetup.date) <= as_utc(now) - READY_AFTER,
    )


# -- Client -------------------------------------------------------------------


class OpenMeteoClient:
    """Thin async wrapper around the Open-Meteo historical forecast API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(headers=OUTBOUND_HEADERS, timeout=settings.open_meteo_request_timeout)

    async def __aenter__(self) -> "OpenMeteoClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def fetch_hourly(self, latitude: float, longitude: float, start: datetime) -> HourlyData:
        """Fetch the hourly arrays of the UTC days covering the window of a meetup starting at `start`."""
        first_day = as_utc(start).date()
        # The window is half open, so one microsecond back keeps a midnight end on the same day.
        last_day = (_window_end(start) - timedelta(microseconds=1)).date()
        params: dict[str, str | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": first_day.isoformat(),
            "end_date": last_day.isoformat(),
            "hourly": ",".join(_HOURLY_VARIABLES),
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        }
        try:
            response = await self._client.get(self._settings.open_meteo_base_url, params=params)
        except httpx.TimeoutException as exc:
            raise WeatherTimeoutError(f"Open-Meteo request timed out: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise WeatherUnavailableError(f"Open-Meteo request failed: {type(exc).__name__}") from exc
        if response.is_error:
            raise WeatherUnavailableError(f"Open-Meteo returned HTTP {response.status_code}")
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> HourlyData:
        """Read and validate the hourly block: equal-length arrays, ISO times, numeric-or-null values."""
        try:
            raw: dict[str, Any] = response.json()["hourly"]
            hourly: HourlyData = {name: list(raw[name]) for name in ("time", *_HOURLY_VARIABLES)}
            if len({len(series) for series in hourly.values()}) != 1:
                raise ValueError("arrays of different lengths")
            for moment in hourly["time"]:
                datetime.fromisoformat(moment)  # type: ignore[arg-type]
            for name in _HOURLY_VARIABLES:
                if not all(v is None or (isinstance(v, int | float) and not isinstance(v, bool)) for v in hourly[name]):
                    raise ValueError(f"non-numeric value in {name}")
        except (ValueError, KeyError, TypeError) as exc:
            raise WeatherUnavailableError(
                f"Open-Meteo returned data in an unexpected format: {type(exc).__name__}"
            ) from exc
        return hourly


# -- Persistence --------------------------------------------------------------


async def fetch_and_store_weather(session: Session, meetup: Meetup, client: OpenMeteoClient) -> MeetupWeather:
    """Fetch the weather of a located meetup and upsert its row, returning the stored row.

    Contract:
    - Precondition: the meetup has coordinates (callers check is_weather_ready
      first); otherwise MissingLocationError is raised and nothing is fetched.
    - NoWeatherDataError (a WeatherUnavailableError) when the provider had no
      value for the window: nothing is written, so a later run can retry.
    - WeatherUnavailableError for any other provider failure.
    - Safe to run twice at once: the primary key plus ON CONFLICT DO UPDATE
      leave one row.
    """
    if meetup.latitude is None or meetup.longitude is None:
        raise MissingLocationError(f"Cannot fetch weather: meetup {meetup.id} has no coordinates.")

    hourly = await client.fetch_hourly(meetup.latitude, meetup.longitude, meetup.date)
    summary = summarize_weather(hourly, meetup.date)
    if summary is None:
        raise NoWeatherDataError("Open-Meteo has no data for the window of this meetup")

    # Building the model applies its defaults (fetched_at), the single source of truth.
    row = MeetupWeather(
        meetup_id=meetup.id,
        avg_temperature=summary.avg_temperature,
        avg_apparent_temperature=summary.avg_apparent_temperature,
        total_precipitation_mm=summary.total_precipitation_mm,
        max_wind_kmh=summary.max_wind_kmh,
        rained=summary.rained,
        hourly=summary.hourly,
        source=SOURCE,
    )
    values = row.model_dump()
    stmt = pg_insert(MeetupWeather).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["meetup_id"],
        set_={column: stmt.excluded[column] for column in values if column != "meetup_id"},
    )
    session.exec(stmt)  # type: ignore[arg-type]
    session.commit()
    log.info("Weather stored", meetup_id=str(meetup.id), rained=summary.rained)
    return session.get_one(MeetupWeather, meetup.id, populate_existing=True)
