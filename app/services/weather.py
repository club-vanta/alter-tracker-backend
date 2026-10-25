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
[date, date + 6h). Hours used: every hourly timestamp t with
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
Timeouts, transport errors, non-2xx statuses and malformed JSON all raise
WeatherUnavailableError; callers decide whether that is a 502 or a log line.
An answer with no usable value in the window raises its subclass NoWeatherDataError.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session

from app.core.config import Settings
from app.models.models import Meetup, MeetupWeather

log = structlog.get_logger(__name__)

SOURCE = "open-meteo-historical-forecast"

# The provider asks API users to identify the app.
_HEADERS: dict[str, str] = {
    "User-Agent": "alter-tracker/1.0 (club-vanta.com)",
    "Accept": "application/json",
}

_HOURLY_VARIABLES = ("temperature_2m", "apparent_temperature", "precipitation", "wind_speed_10m")
_WINDOW = timedelta(hours=6)
# The provider publishes a finished hour with some delay, so wait before the first fetch.
_PROVIDER_DELAY = timedelta(hours=2)
_RAIN_THRESHOLD_MM = 0.2

type HourlyData = dict[str, list[str | float | None]]

# -- Errors -------------------------------------------------------------------


class WeatherUnavailableError(Exception):
    """Open-Meteo timed out, failed, or returned something we cannot read."""


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


def _as_utc(moment: datetime) -> datetime:
    """Naive datetimes are taken as UTC, aware ones converted to it."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _window_end(start: datetime) -> datetime:
    return _as_utc(start) + _WINDOW


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def summarize_weather(hourly: HourlyData, start: datetime) -> WeatherSummary | None:
    """Summarize the hourly arrays over [floor_hour(start), start + 6h), or None without any value."""
    window_start = _as_utc(start).replace(minute=0, second=0, microsecond=0)
    window_end = _window_end(start)
    times = hourly["time"]
    indexes = [i for i, t in enumerate(times) if window_start <= _as_utc(datetime.fromisoformat(str(t))) < window_end]
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


def is_weather_ready(meetup: Meetup, now: datetime) -> bool:
    """True when the meetup has coordinates and its window ended at least 2 hours ago."""
    if meetup.latitude is None or meetup.longitude is None:
        return False
    return _window_end(meetup.date) + _PROVIDER_DELAY <= _as_utc(now)


# -- Client -------------------------------------------------------------------


class OpenMeteoClient:
    """Thin async wrapper around the Open-Meteo historical forecast API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=settings.open_meteo_request_timeout)

    async def __aenter__(self) -> "OpenMeteoClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def fetch_hourly(self, latitude: float, longitude: float, start: datetime) -> HourlyData:
        """Fetch the hourly arrays of the UTC days covering the window of a meetup starting at `start`."""
        first_day = _as_utc(start).date()
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
        started = time.monotonic()
        try:
            response = await self._client.get(self._settings.open_meteo_base_url, params=params)
        except httpx.HTTPError as exc:
            log.warning("Open-Meteo request failed", error=type(exc).__name__, elapsed=time.monotonic() - started)
            raise WeatherUnavailableError(f"Open-Meteo request failed: {type(exc).__name__}") from exc
        if response.is_error:
            log.warning("Open-Meteo returned an error", status=response.status_code, elapsed=time.monotonic() - started)
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
            log.warning("Open-Meteo returned unreadable data", error=type(exc).__name__)
            raise WeatherUnavailableError("Open-Meteo returned data in an unexpected format") from exc
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
        log.warning("Weather skipped: no data in the window", meetup_id=str(meetup.id))
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
    stored = session.get(MeetupWeather, meetup.id, populate_existing=True)
    assert stored is not None  # just upserted in this transaction
    return stored
