"""
Weather of a meetup, from Open-Meteo's historical forecast API.

Powers the weather tab of the org statistics. Encapsulates the outbound HTTP
logic and the summary math so routers and the worker stay thin. Mirrors
MazmoClient: an async context manager around one shared httpx.AsyncClient.

Request
-------
  GET {open_meteo_base_url}?latitude=..&longitude=..&start_date=..&end_date=..
      &hourly=temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code,is_day
      &timezone=UTC&wind_speed_unit=kmh
start_date/end_date are the UTC dates covering the whole window, so a night
event that crosses midnight asks for two days.

Window
------
[floor_hour(date) - 3h, date + 6h), split in two:
  before: floor_hour(date) - 3h <= t < floor_hour(date), the 3 hours people
          see while deciding whether to go out.
  during: floor_hour(date) <= t < date + 6h, so an event at 22:30 uses 22:00
          through 04:00 (the hour that contains the start counts).

Summary
-------
Per window: averages of temperature and apparent temperature and the sum of
precipitation, each rounded to 1 decimal, and rained at >= 0.2 mm. The max
wind is a during figure only. Nulls are skipped per series, so a missing
temperature does not drop that hour's precipitation. Nothing is summarized
(None) only when every series is empty in both windows.

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
from sqlmodel import Session, col, select

from app.core.config import Settings
from app.core.outbound import OUTBOUND_HEADERS
from app.models.models import Meetup, MeetupWeather, WeatherStatus
from app.services.timeutil import as_utc

log = structlog.get_logger(__name__)

SOURCE = "open-meteo-historical-forecast"

# The first four are summarized; weather_code and is_day are only kept for the hourly chart.
_SUMMARIZED_VARIABLES = ("temperature_2m", "apparent_temperature", "precipitation", "wind_speed_10m")
_HOURLY_VARIABLES = (*_SUMMARIZED_VARIABLES, "weather_code", "is_day")
_BEFORE = timedelta(hours=3)
_WINDOW = timedelta(hours=6)
# The provider publishes a finished hour with some delay, so wait before the first fetch.
_PROVIDER_DELAY = timedelta(hours=2)
READY_AFTER = _WINDOW + _PROVIDER_DELAY
_RAIN_THRESHOLD_MM = 0.2

type HourlyData = dict[str, list[str | int | float | None]]

# -- Errors -------------------------------------------------------------------


class WeatherUnavailableError(Exception):
    """Open-Meteo timed out, failed, or returned something we cannot read."""


class WeatherTimeoutError(WeatherUnavailableError):
    """Open-Meteo did not answer within the request timeout."""


class NoWeatherDataError(WeatherUnavailableError):
    """Open-Meteo answered, but every series is empty inside the window (nothing was stored)."""


class WeatherLocationChangedError(WeatherUnavailableError):
    """The meetup moved while its weather was being fetched, so the answer was discarded (nothing was stored)."""


class MissingLocationError(Exception):
    """fetch_and_store_weather was called for a meetup without coordinates (a caller bug)."""


# -- Summary ------------------------------------------------------------------


@dataclass(frozen=True)
class WindowSummary:
    """Stats over the hours of one window; each is None when its series has no value there."""

    avg_temperature: float | None
    avg_apparent_temperature: float | None
    total_precipitation_mm: float | None
    rained: bool | None


@dataclass(frozen=True)
class WeatherSummary:
    """The during window flat (as stored since the first version), the before window, and the raw arrays of both."""

    avg_temperature: float | None
    avg_apparent_temperature: float | None
    total_precipitation_mm: float | None
    max_wind_kmh: float | None
    rained: bool | None
    before: WindowSummary
    hourly: HourlyData


def _floor_hour(start: datetime) -> datetime:
    return as_utc(start).replace(minute=0, second=0, microsecond=0)


def _window_start(start: datetime) -> datetime:
    return _floor_hour(start) - _BEFORE


def _window_end(start: datetime) -> datetime:
    return as_utc(start) + _WINDOW


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _present(hourly: HourlyData, indexes: list[int]) -> dict[str, list[float]]:
    """The non-null values of each summarized series at the given hours."""
    return {
        name: [float(v) for v in (hourly[name][i] for i in indexes) if v is not None] for name in _SUMMARIZED_VARIABLES
    }


def _window_summary(present: dict[str, list[float]]) -> WindowSummary:
    precipitation = present["precipitation"]
    total = round(sum(precipitation), 1) if precipitation else None
    return WindowSummary(
        avg_temperature=_mean(present["temperature_2m"]),
        avg_apparent_temperature=_mean(present["apparent_temperature"]),
        total_precipitation_mm=total,
        rained=None if total is None else total >= _RAIN_THRESHOLD_MM,
    )


def summarize_weather(hourly: HourlyData, start: datetime) -> WeatherSummary | None:
    """Summarize the hourly arrays over the before and during windows, or None without any value in either."""
    start_hour = _floor_hour(start)
    window_start, window_end = _window_start(start), _window_end(start)
    moments = [as_utc(datetime.fromisoformat(str(t))) for t in hourly["time"]]
    before = [i for i, t in enumerate(moments) if window_start <= t < start_hour]
    during = [i for i, t in enumerate(moments) if start_hour <= t < window_end]
    before_present, during_present = _present(hourly, before), _present(hourly, during)

    if not any(before_present.values()) and not any(during_present.values()):
        return None

    used = before + during
    during_summary = _window_summary(during_present)
    wind = during_present["wind_speed_10m"]
    return WeatherSummary(
        avg_temperature=during_summary.avg_temperature,
        avg_apparent_temperature=during_summary.avg_apparent_temperature,
        total_precipitation_mm=during_summary.total_precipitation_mm,
        max_wind_kmh=round(max(wind), 1) if wind else None,
        rained=during_summary.rained,
        before=_window_summary(before_present),
        hourly={name: [hourly[name][i] for i in used] for name in ("time", *_HOURLY_VARIABLES)},
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
        first_day = _window_start(start).date()
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
    - WeatherLocationChangedError when the meetup's coordinates changed while
      the provider was answering: the answer belongs to the old place, so
      nothing is written (the caller that moved it fetches again).
    - Safe to run twice at once: the primary key plus ON CONFLICT DO UPDATE
      leave one row.
    """
    if meetup.latitude is None or meetup.longitude is None:
        raise MissingLocationError(f"Cannot fetch weather: meetup {meetup.id} has no coordinates.")

    latitude, longitude = meetup.latitude, meetup.longitude
    hourly = await client.fetch_hourly(latitude, longitude, meetup.date)
    summary = summarize_weather(hourly, meetup.date)
    if summary is None:
        raise NoWeatherDataError("Open-Meteo has no data for the window of this meetup")

    # Lock the meetup row while checking it still sits where we fetched for, so a
    # concurrent move waits until this write is committed and then deletes the row.
    unmoved = select(Meetup.id).where(
        col(Meetup.id) == meetup.id, col(Meetup.latitude) == latitude, col(Meetup.longitude) == longitude
    )
    if session.exec(unmoved.with_for_update()).first() is None:
        raise WeatherLocationChangedError(f"Meetup {meetup.id} moved while its weather was being fetched")

    # Building the model applies its defaults (fetched_at), the single source of truth.
    row = MeetupWeather(
        meetup_id=meetup.id,
        avg_temperature=summary.avg_temperature,
        avg_apparent_temperature=summary.avg_apparent_temperature,
        total_precipitation_mm=summary.total_precipitation_mm,
        max_wind_kmh=summary.max_wind_kmh,
        rained=summary.rained,
        before_avg_temperature=summary.before.avg_temperature,
        before_avg_apparent_temperature=summary.before.avg_apparent_temperature,
        before_total_precipitation_mm=summary.before.total_precipitation_mm,
        before_rained=summary.before.rained,
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
