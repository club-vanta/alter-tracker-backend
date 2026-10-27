"""
Weather statistics: how temperature relates to attendance, and the hour by
hour weather of one meetup.

Attendance figures come from the attendance service's per-meetup rows (never
recounted); this module only joins them with each meetup's stored weather
(MeetupWeather) and groups them in temperature bands, once by the average
during the meetup and once by the average in the 3 hours before it. Bands
pool the rows through summarize_rows, so a band's no-show rate is
sum(no_show) / sum(expected) over its meetups, not an average of rates.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.models.models import Meetup, MeetupWeather, StatsTemperatureBand, WeatherStatus
from app.services.stats.attendance import MeetupAttendanceRow, meetup_attendance_rows, summarize_rows
from app.services.stats.scope import StatsPeriod, finalized_meetup_conditions
from app.services.timeutil import as_utc
from app.services.weather import is_weather_ready, weather_status

# Lower bound (inclusive) of each band above the first, in ascending order.
_BAND_LOWER_BOUNDS: tuple[tuple[float, StatsTemperatureBand], ...] = (
    (25.0, StatsTemperatureBand.FROM_25),
    (20.0, StatsTemperatureBand.FROM_20_TO_25),
    (15.0, StatsTemperatureBand.FROM_15_TO_20),
    (10.0, StatsTemperatureBand.FROM_10_TO_15),
)

# -- Result shapes --


@dataclass(frozen=True)
class WeatherMeetupRow:
    """
    One finalized meetup with its attendance and weather (weather fields None without a value).
    has_weather is True when a weather row exists.
    """

    meetup_id: uuid.UUID
    name: str
    date: datetime
    attended: int
    no_show_rate: float | None
    avg_temperature: float | None
    rained: bool | None
    total_precipitation_mm: float | None
    before_avg_temperature: float | None
    has_weather: bool


@dataclass(frozen=True)
class TemperatureBandRow:
    """Meetups of one band; avg_attended and no_show_rate are None for a band with no events."""

    band: StatsTemperatureBand
    events: int
    avg_attended: float | None
    no_show_rate: float | None


@dataclass(frozen=True)
class WeatherWindow:
    """The summary of the 3 hours before a meetup's start; each field None without a value."""

    avg_temperature: float | None
    avg_apparent_temperature: float | None
    total_precipitation_mm: float | None
    rained: bool | None


@dataclass(frozen=True)
class WeatherHour:
    """One stored hour; offset_minutes runs from the meetup start to the hour's start (negative before)."""

    time: datetime
    offset_minutes: int
    temperature: float | None
    apparent_temperature: float | None
    precipitation_mm: float | None
    weather_code: int | None
    is_day: bool | None


@dataclass(frozen=True)
class WeatherEvent:
    """The weather standing of one meetup, its before window, and its stored hours (None and [] without a row)."""

    status: WeatherStatus
    weather: MeetupWeather | None
    ready: bool
    before: WeatherWindow | None
    hours: list[WeatherHour]


@dataclass(frozen=True)
class WeatherStats:
    """
    Either one event (event set, every aggregate field empty) or the aggregate
    (event None). The tab compares groups of events against each other, so
    comparison_kind and comparison are always None.
    """

    event: WeatherEvent | None
    meetups: list[WeatherMeetupRow]
    temperature_bands: list[TemperatureBandRow]
    temperature_bands_before: list[TemperatureBandRow]
    comparison_kind: None
    comparison: None


# -- Grouping --


def band_for_temperature(temperature: float) -> StatsTemperatureBand:
    """The band of an average temperature; each lower bound belongs to its own band."""
    for lower_bound, band in _BAND_LOWER_BOUNDS:
        if temperature >= lower_bound:
            return band
    return StatsTemperatureBand.BELOW_10


def _pooled(rows: list[MeetupAttendanceRow]) -> tuple[int, float | None, float | None]:
    totals = summarize_rows(rows)
    return totals.events, totals.avg_attended_per_event, totals.no_show_rate


def _band_rows(
    attendance: list[tuple[MeetupAttendanceRow, MeetupWeather | None]],
    temperature_of: Callable[[MeetupWeather], float | None],
) -> list[TemperatureBandRow]:
    by_band: dict[StatsTemperatureBand, list[MeetupAttendanceRow]] = {band: [] for band in StatsTemperatureBand}
    for row, weather in attendance:
        temperature = None if weather is None else temperature_of(weather)
        if temperature is not None:
            by_band[band_for_temperature(temperature)].append(row)
    return [TemperatureBandRow(band, *_pooled(rows)) for band, rows in by_band.items()]


def _hours(weather: MeetupWeather, start: datetime) -> list[WeatherHour]:
    """The stored hourly arrays as one row per hour, offset from the meetup start."""
    hourly = weather.hourly
    # Offsets count whole minutes, so seconds in the start do not shift them by one.
    start = as_utc(start).replace(second=0, microsecond=0)
    times = hourly["time"]
    # A row written by a worker older than weather_code and is_day lacks them; it still charts.
    codes = hourly.get("weather_code") or [None] * len(times)
    day_flags = hourly.get("is_day") or [None] * len(times)
    hours = []
    for i, moment in enumerate(times):
        time = as_utc(datetime.fromisoformat(str(moment)))
        code, is_day = codes[i], day_flags[i]
        hours.append(
            WeatherHour(
                time=time,
                offset_minutes=(time - start) // timedelta(minutes=1),
                temperature=hourly["temperature_2m"][i],  # type: ignore[arg-type]
                apparent_temperature=hourly["apparent_temperature"][i],  # type: ignore[arg-type]
                precipitation_mm=hourly["precipitation"][i],  # type: ignore[arg-type]
                weather_code=None if code is None else int(code),
                is_day=None if is_day is None else bool(is_day),
            )
        )
    return hours


# -- Queries --


def _weather_by_meetup(session: Session, conditions: list) -> dict[uuid.UUID, MeetupWeather]:
    query = select(MeetupWeather).join(Meetup, col(Meetup.id) == col(MeetupWeather.meetup_id)).where(*conditions)
    return {weather.meetup_id: weather for weather in session.exec(query)}


def _event(session: Session, meetup_id: uuid.UUID, now: datetime) -> WeatherEvent:
    meetup = session.get_one(Meetup, meetup_id)
    weather = session.get(MeetupWeather, meetup_id)
    before = (
        None
        if weather is None
        else WeatherWindow(
            avg_temperature=weather.before_avg_temperature,
            avg_apparent_temperature=weather.before_avg_apparent_temperature,
            total_precipitation_mm=weather.before_total_precipitation_mm,
            rained=weather.before_rained,
        )
    )
    return WeatherEvent(
        status=weather_status(meetup, weather is not None, now),
        weather=weather,
        ready=is_weather_ready(meetup, now),
        before=before,
        hours=[] if weather is None else _hours(weather, meetup.date),
    )


# -- Entry point --


def weather_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> WeatherStats:
    """
    Temperature against attendance for the period's finalized meetups, or the
    weather of one meetup. The caller guarantees meetup_id is a finalized
    meetup of the org.

    A row whose avg_temperature is None joins no band of temperature_bands;
    one whose before_avg_temperature is None joins no band of
    temperature_bands_before.
    """
    now = datetime.now(UTC)
    if meetup_id is not None:
        return WeatherStats(
            event=_event(session, meetup_id, now),
            meetups=[],
            temperature_bands=[],
            temperature_bands_before=[],
            comparison_kind=None,
            comparison=None,
        )

    rows = meetup_attendance_rows(session, org_id, period)
    weather_of = _weather_by_meetup(session, finalized_meetup_conditions(org_id, period))
    paired = [(row, weather_of.get(row.meetup_id)) for row in rows]

    return WeatherStats(
        event=None,
        meetups=[
            WeatherMeetupRow(
                meetup_id=row.meetup_id,
                name=row.name,
                date=row.date,
                attended=row.attended,
                no_show_rate=summarize_rows([row]).no_show_rate,
                avg_temperature=weather.avg_temperature if weather else None,
                rained=weather.rained if weather else None,
                total_precipitation_mm=weather.total_precipitation_mm if weather else None,
                before_avg_temperature=weather.before_avg_temperature if weather else None,
                has_weather=weather is not None,
            )
            for row, weather in paired
        ],
        temperature_bands=_band_rows(paired, lambda w: w.avg_temperature),
        temperature_bands_before=_band_rows(paired, lambda w: w.before_avg_temperature),
        comparison_kind=None,
        comparison=None,
    )
