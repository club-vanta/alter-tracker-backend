"""
Weather statistics: how rain and temperature relate to attendance.

Attendance figures come from the attendance service's per-meetup rows (never
recounted); this module only joins them with each meetup's stored weather
(MeetupWeather) and groups them. Groups pool the rows through summarize_rows,
so a group's no-show rate is sum(no_show) / sum(expected) over its meetups,
not an average of rates.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.models.models import Meetup, MeetupWeather, StatsTemperatureBand, WeatherStatus
from app.services.stats.attendance import (
    MeetupAttendanceRow,
    meetup_attendance_rows,
    single_meetup_attendance_rows,
    summarize_rows,
)
from app.services.stats.scope import StatsPeriod, finalized_meetup_conditions
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
    has_weather is True when a weather row exists, the same rule as without_weather.
    """

    meetup_id: uuid.UUID
    name: str
    date: datetime
    attended: int
    no_show_rate: float | None
    avg_temperature: float | None
    rained: bool | None
    total_precipitation_mm: float | None
    has_weather: bool


@dataclass(frozen=True)
class WeatherGroup:
    """Events of one group; avg_attended and no_show_rate are None for a group with no events."""

    events: int
    avg_attended: float | None
    no_show_rate: float | None


@dataclass(frozen=True)
class TemperatureBandRow:
    band: StatsTemperatureBand
    events: int
    avg_attended: float | None
    no_show_rate: float | None


@dataclass(frozen=True)
class WeatherEvent:
    """The weather standing of one meetup plus its attendance."""

    status: WeatherStatus
    weather: MeetupWeather | None
    ready: bool
    attended: int
    no_show_rate: float | None


@dataclass(frozen=True)
class WeatherStats:
    """
    Either one event (event set, every aggregate field empty) or the aggregate
    (event None). The tab compares groups of events against each other, so
    comparison_kind and comparison are always None.
    """

    event: WeatherEvent | None
    meetups: list[WeatherMeetupRow]
    rainy: WeatherGroup | None
    dry: WeatherGroup | None
    temperature_bands: list[TemperatureBandRow]
    without_weather: int
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


def _group(rows: list[MeetupAttendanceRow]) -> WeatherGroup:
    events, avg_attended, no_show_rate = _pooled(rows)
    return WeatherGroup(events=events, avg_attended=avg_attended, no_show_rate=no_show_rate)


def _band_rows(
    attendance: list[tuple[MeetupAttendanceRow, MeetupWeather | None]],
) -> list[TemperatureBandRow]:
    by_band: dict[StatsTemperatureBand, list[MeetupAttendanceRow]] = {band: [] for band in StatsTemperatureBand}
    for row, weather in attendance:
        if weather is not None and weather.avg_temperature is not None:
            by_band[band_for_temperature(weather.avg_temperature)].append(row)
    return [TemperatureBandRow(band, *_pooled(rows)) for band, rows in by_band.items()]


# -- Queries --


def _weather_by_meetup(session: Session, conditions: list) -> dict[uuid.UUID, MeetupWeather]:
    query = select(MeetupWeather).join(Meetup, col(Meetup.id) == col(MeetupWeather.meetup_id)).where(*conditions)
    return {weather.meetup_id: weather for weather in session.exec(query)}


def _event(session: Session, org_id: uuid.UUID, meetup_id: uuid.UUID, now: datetime) -> WeatherEvent:
    [row] = single_meetup_attendance_rows(session, org_id, meetup_id)
    meetup = session.get_one(Meetup, meetup_id)
    weather = session.get(MeetupWeather, meetup_id)
    return WeatherEvent(
        status=weather_status(meetup, weather is not None, now),
        weather=weather,
        ready=is_weather_ready(meetup, now),
        attended=row.attended,
        no_show_rate=summarize_rows([row]).no_show_rate,
    )


# -- Entry point --


def weather_stats(
    session: Session, org_id: uuid.UUID, period: StatsPeriod, meetup_id: uuid.UUID | None
) -> WeatherStats:
    """
    Weather against attendance for the period's finalized meetups, or the
    weather of one meetup. The caller guarantees meetup_id is a finalized
    meetup of the org.

    Grouping: rainy is rained True and dry is rained False; a row whose rained
    is None (no precipitation data) joins neither. A row whose avg_temperature
    is None joins no band. without_weather counts only meetups with no row.
    """
    now = datetime.now(UTC)
    if meetup_id is not None:
        return WeatherStats(
            event=_event(session, org_id, meetup_id, now),
            meetups=[],
            rainy=None,
            dry=None,
            temperature_bands=[],
            without_weather=0,
            comparison_kind=None,
            comparison=None,
        )

    rows = meetup_attendance_rows(session, org_id, period)
    weather_of = _weather_by_meetup(session, finalized_meetup_conditions(org_id, period))
    paired = [(row, weather_of.get(row.meetup_id)) for row in rows]

    def with_rained(value: bool) -> list[MeetupAttendanceRow]:
        return [row for row, weather in paired if weather is not None and weather.rained is value]

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
                has_weather=weather is not None,
            )
            for row, weather in paired
        ],
        rainy=_group(with_rained(True)),
        dry=_group(with_rained(False)),
        temperature_bands=_band_rows(paired),
        without_weather=sum(1 for _, weather in paired if weather is None),
        comparison_kind=None,
        comparison=None,
    )
