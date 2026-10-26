"""Tests for the org weather statistics service and endpoint."""

from datetime import UTC, date, datetime, timedelta
from itertools import count

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import (
    GuestType,
    Meetup,
    MeetupWeather,
    Organization,
    OrgRole,
    StatsTemperatureBand,
    WeatherStatus,
)
from app.services.stats.scope import StatsPeriod
from app.services.stats.weather import band_for_temperature, weather_stats
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

ALL_TIME = StatsPeriod(None, None)
RSVP_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_ids = count(1000)
_UNSET = object()
HOURLY_KEYS = (
    "time",
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
    "is_day",
)


def _meetup(
    session: Session,
    org: Organization,
    when: datetime,
    *,
    attended: int = 0,
    no_show: int = 0,
    cancelled: int = 0,
    weather: bool = False,
    temperature: float | None = None,
    rained: bool | None = None,
    precipitation: float | None | object = _UNSET,
    before_temperature: float | None = None,
    hourly: dict | None = None,
    located: bool = False,
    finalized: bool = True,
) -> Meetup:
    n = next(_ids)
    meetup = make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=when,
        is_finalized=finalized,
    )
    if located or weather:
        meetup.latitude = -34.6
        meetup.longitude = -58.4
        session.flush()
    for kind, amount in (("attended", attended), ("no_show", no_show), ("cancelled", cancelled)):
        for _ in range(amount):
            guest = next(_ids)
            make_rsvp(
                session,
                meetup=meetup,
                guest=make_guest(session, mazmo_user_id=guest, mazmo_handle=f"guest{guest}", displayname=f"G {guest}"),
                rsvp_time=RSVP_TIME,
                has_arrived=kind == "attended",
                cancelled_rsvp=kind == "cancelled",
            )
    if weather:
        if precipitation is _UNSET:
            precipitation = None if rained is None else (1.5 if rained else 0.0)
        session.add(
            MeetupWeather(
                meetup_id=meetup.id,
                avg_temperature=temperature,
                avg_apparent_temperature=temperature,
                total_precipitation_mm=precipitation,  # type: ignore[arg-type]
                max_wind_kmh=12.0,
                rained=rained,
                before_avg_temperature=before_temperature,
                hourly=hourly if hourly is not None else {name: [] for name in HOURLY_KEYS},
                fetched_at=RSVP_TIME,
                source="open-meteo-historical-forecast",
            )
        )
        session.flush()
    return meetup


def _seed_aggregate(session: Session, org: Organization) -> dict[str, Meetup]:
    """Six finalized meetups. Groups differ in denominators and rates, so pooled and mean-of-rates differ."""
    return {
        "m1": _meetup(
            session,
            org,
            datetime(2026, 4, 5, 22, tzinfo=UTC),
            attended=1,
            no_show=1,
            weather=True,
            temperature=10.0,
            rained=True,
        ),
        "m2": _meetup(
            session,
            org,
            datetime(2026, 4, 12, 22, tzinfo=UTC),
            attended=3,
            no_show=1,
            weather=True,
            temperature=9.9,
            rained=False,
        ),
        "m3": _meetup(
            session,
            org,
            datetime(2026, 4, 19, 22, tzinfo=UTC),
            attended=9,
            no_show=1,
            weather=True,
            temperature=25.0,
            rained=True,
        ),
        "m4": _meetup(
            session, org, datetime(2026, 4, 26, 22, tzinfo=UTC), attended=4, weather=True, temperature=17.5, rained=None
        ),
        "m5": _meetup(
            session,
            org,
            datetime(2026, 5, 3, 22, tzinfo=UTC),
            attended=2,
            no_show=6,
            weather=True,
            temperature=None,
            rained=False,
        ),
        "m6": _meetup(session, org, datetime(2026, 5, 10, 22, tzinfo=UTC), attended=2),
    }


# -- band_for_temperature --


@pytest.mark.parametrize(
    ("temperature", "band"),
    [
        (-3.0, StatsTemperatureBand.BELOW_10),
        (9.9, StatsTemperatureBand.BELOW_10),
        (10.0, StatsTemperatureBand.FROM_10_TO_15),
        (14.9, StatsTemperatureBand.FROM_10_TO_15),
        (15.0, StatsTemperatureBand.FROM_15_TO_20),
        (20.0, StatsTemperatureBand.FROM_20_TO_25),
        (24.9, StatsTemperatureBand.FROM_20_TO_25),
        (25.0, StatsTemperatureBand.FROM_25),
        (38.2, StatsTemperatureBand.FROM_25),
    ],
)
def test_band_for_temperature_includes_the_lower_bound(temperature: float, band: StatsTemperatureBand):
    assert band_for_temperature(temperature) is band


# -- weather_stats: aggregate --


def test_weather_stats_lists_all_five_bands_in_order(session: Session):
    org = make_org(session)
    _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [(b.band, b.events, b.avg_attended, b.no_show_rate) for b in stats.temperature_bands] == [
        (StatsTemperatureBand.BELOW_10, 1, 3.0, 0.25),
        (StatsTemperatureBand.FROM_10_TO_15, 1, 1.0, 0.5),
        (StatsTemperatureBand.FROM_15_TO_20, 1, 4.0, 0.0),
        (StatsTemperatureBand.FROM_20_TO_25, 0, None, None),
        (StatsTemperatureBand.FROM_25, 1, 9.0, 0.1),
    ]


def test_weather_stats_lists_meetups_by_date_with_null_weather_fields_without_a_row(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [m.meetup_id for m in stats.meetups] == [seed[k].id for k in ("m1", "m2", "m3", "m4", "m5", "m6")]
    m1, _, _, m4, m5, m6 = stats.meetups
    assert (m1.attended, m1.no_show_rate, m1.avg_temperature, m1.rained, m1.total_precipitation_mm) == (
        1,
        0.5,
        10.0,
        True,
        1.5,
    )
    assert (m4.avg_temperature, m4.rained, m4.total_precipitation_mm) == (17.5, None, None)
    assert [m.has_weather for m in stats.meetups] == [True, True, True, True, True, False]
    assert (m5.avg_temperature, m5.rained) == (None, False)
    assert (m6.attended, m6.no_show_rate, m6.avg_temperature, m6.rained, m6.total_precipitation_mm) == (
        2,
        0.0,
        None,
        None,
        None,
    )


def test_weather_stats_bands_before_use_the_before_average(session: Session):
    org = make_org(session)
    # Cold before (8.0) but mild during (16.0), and the reverse.
    _meetup(
        session,
        org,
        datetime(2026, 4, 5, 22, tzinfo=UTC),
        attended=2,
        weather=True,
        temperature=16.0,
        before_temperature=8.0,
        rained=False,
    )
    _meetup(
        session,
        org,
        datetime(2026, 4, 12, 22, tzinfo=UTC),
        attended=6,
        weather=True,
        temperature=9.0,
        before_temperature=21.0,
        rained=False,
    )

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [(b.band, b.events, b.avg_attended) for b in stats.temperature_bands if b.events] == [
        (StatsTemperatureBand.BELOW_10, 1, 6.0),
        (StatsTemperatureBand.FROM_15_TO_20, 1, 2.0),
    ]
    assert [(b.band, b.events, b.avg_attended) for b in stats.temperature_bands_before if b.events] == [
        (StatsTemperatureBand.BELOW_10, 1, 2.0),
        (StatsTemperatureBand.FROM_20_TO_25, 1, 6.0),
    ]
    assert [b.band for b in stats.temperature_bands_before] == list(StatsTemperatureBand)


def test_weather_stats_rows_carry_before_avg_temperature(session: Session):
    org = make_org(session)
    _meetup(
        session,
        org,
        datetime(2026, 4, 5, 22, tzinfo=UTC),
        weather=True,
        temperature=16.0,
        before_temperature=8.0,
        rained=False,
    )
    _meetup(session, org, datetime(2026, 4, 12, 22, tzinfo=UTC))

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [m.before_avg_temperature for m in stats.meetups] == [8.0, None]


def test_weather_stats_empty_groups_have_null_figures(session: Session):
    org = make_org(session)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert stats.meetups == []
    assert stats.event is None
    assert stats.comparison_kind is None
    assert stats.comparison is None
    assert [(b.band, b.events, b.avg_attended, b.no_show_rate) for b in stats.temperature_bands] == [
        (band, 0, None, None) for band in StatsTemperatureBand
    ]
    assert stats.temperature_bands_before == stats.temperature_bands


def test_weather_stats_respects_the_period_and_skips_unfinalized_meetups(session: Session):
    org = make_org(session)
    inside = _meetup(
        session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=4, weather=True, temperature=12.0, rained=False
    )
    _meetup(
        session, org, datetime(2026, 2, 5, 22, tzinfo=UTC), attended=9, weather=True, temperature=12.0, rained=False
    )
    _meetup(
        session,
        org,
        datetime(2026, 4, 6, 22, tzinfo=UTC),
        attended=7,
        no_show=3,
        weather=True,
        temperature=30.0,
        rained=True,
        finalized=False,
    )

    stats = weather_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 5, 1)), None)

    assert [m.meetup_id for m in stats.meetups] == [inside.id]
    # The unfinalized meetup's row (30.0 degrees) reaches no band.
    assert [(b.band, b.events) for b in stats.temperature_bands if b.events] == [
        (StatsTemperatureBand.FROM_10_TO_15, 1)
    ]


def test_weather_stats_audience_only(session: Session):
    org = make_org(session)
    meetup = _meetup(
        session,
        org,
        datetime(2026, 4, 5, 22, tzinfo=UTC),
        attended=1,
        no_show=1,
        weather=True,
        temperature=12.0,
        rained=False,
    )
    staff = next(_ids)
    make_rsvp(
        session,
        meetup=meetup,
        guest=make_guest(session, mazmo_user_id=staff, mazmo_handle=f"guest{staff}", displayname="Staff"),
        rsvp_time=RSVP_TIME,
        has_arrived=True,
        guest_type=GuestType.STAFF.value,
    )

    stats = weather_stats(session, org.id, ALL_TIME, None)

    [row] = stats.meetups
    assert (row.attended, row.no_show_rate) == (1, 0.5)
    [band] = [b for b in stats.temperature_bands if b.events]
    assert (band.events, band.avg_attended, band.no_show_rate) == (1, 1.0, 0.5)


# -- weather_stats: one event --


def test_weather_stats_one_event_returns_the_status_with_its_weather(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, seed["m1"].id)

    assert stats.event is not None
    assert stats.event.status is WeatherStatus.OK
    assert stats.event.ready is True
    assert stats.event.weather is not None
    assert (stats.event.weather.avg_temperature, stats.event.weather.rained) == (10.0, True)
    assert stats.meetups == []
    assert stats.temperature_bands == []
    assert stats.temperature_bands_before == []
    assert stats.comparison_kind is None
    assert stats.comparison is None


def _half_past_hourly() -> dict[str, list]:
    """The stored window of a 22:30 UTC meetup: 19:00 through 04:00 (10 hours)."""
    first = datetime(2026, 4, 5, 19, 0)
    times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(10)]
    return {
        "time": times,
        "temperature_2m": [14.0, 13.0, 12.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0],
        "apparent_temperature": [11.0, 10.0, 9.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
        "precipitation": [1.5, 2.0, 0.6, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, None],
        "wind_speed_10m": [30.0] * 10,
        "weather_code": [63, 63, 51, 3, 2, 0, 0, 0, 0, None],
        "is_day": [1, 0, 0, 0, 0, 0, 0, 0, 0, None],
    }


def test_weather_stats_one_event_returns_before_and_hours_with_offsets(session: Session):
    org = make_org(session)
    meetup = _meetup(
        session,
        org,
        datetime(2026, 4, 5, 22, 30, tzinfo=UTC),
        attended=1,
        weather=True,
        temperature=16.0,
        rained=False,
        hourly=_half_past_hourly(),
    )
    row = session.get_one(MeetupWeather, meetup.id)
    row.before_avg_temperature = 13.0
    row.before_avg_apparent_temperature = 10.0
    row.before_total_precipitation_mm = 4.1
    row.before_rained = True
    session.flush()

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    before = stats.event.before
    assert before is not None
    assert (before.avg_temperature, before.avg_apparent_temperature, before.total_precipitation_mm, before.rained) == (
        13.0,
        10.0,
        4.1,
        True,
    )
    hours = stats.event.hours
    assert [h.offset_minutes for h in hours] == [-210, -150, -90, -30, 30, 90, 150, 210, 270, 330]
    first, last = hours[0], hours[-1]
    assert first.time == datetime(2026, 4, 5, 19, 0, tzinfo=UTC)
    assert (first.temperature, first.apparent_temperature, first.precipitation_mm) == (14.0, 11.0, 1.5)
    assert (first.weather_code, first.is_day) == (63, True)
    assert (hours[1].is_day, hours[3].weather_code) == (False, 3)
    assert (last.precipitation_mm, last.weather_code, last.is_day) == (None, None, None)


def test_weather_stats_one_event_without_a_row_has_no_before_and_no_hours(session: Session):
    org = make_org(session)
    meetup = _meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=1, located=True)

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    assert (stats.event.before, stats.event.hours) == (None, [])


def test_weather_stats_one_event_ignores_period_bounds(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, StatsPeriod(date(2030, 1, 1), date(2030, 2, 1)), seed["m1"].id)

    assert stats.event is not None
    assert stats.event.status is WeatherStatus.OK


def test_weather_stats_one_event_without_location(session: Session):
    org = make_org(session)
    meetup = _meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=3, no_show=1)

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    assert (stats.event.status, stats.event.weather, stats.event.ready) == (WeatherStatus.NO_LOCATION, None, False)


def test_weather_stats_one_event_located_without_a_row_is_not_fetched(session: Session):
    org = make_org(session)
    meetup = _meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=1, located=True)

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    assert (stats.event.status, stats.event.weather, stats.event.ready) == (WeatherStatus.NOT_FETCHED, None, True)


def test_weather_stats_one_event_too_recent_is_pending(session: Session):
    org = make_org(session)
    meetup = _meetup(session, org, datetime.now(UTC) - timedelta(hours=1), attended=1, located=True)

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    assert (stats.event.status, stats.event.ready) == (WeatherStatus.PENDING, False)


# -- GET /organizations/{org_id}/stats/weather --


def _stats_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/weather"


def test_weather_stats_returns_401_without_auth(client: TestClient, org: Organization):
    assert client.get(_stats_url(org)).status_code == status.HTTP_401_UNAUTHORIZED


def test_weather_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_stats_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_weather_stats_returns_403_for_admin_of_other_org(client: TestClient, session: Session, org: Organization):
    other_org = make_org(session, name="Other Org", slug="other-org")
    other_admin = make_user(session, username="other_admin")
    make_org_member(session, org=other_org, user=other_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "other_admin", "a-very-secure-passphrase")

    resp = client.get(_stats_url(org), headers=headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_weather_stats_returns_the_exact_aggregate_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    seed = _seed_aggregate(session, org)

    resp = client.get(_stats_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["event"] is None
    assert body["comparison_kind"] is None
    assert body["comparison"] is None
    assert "rainy" not in body
    assert "dry" not in body
    assert "without_weather" not in body
    assert [b["events"] for b in body["temperature_bands_before"]] == [0, 0, 0, 0, 0]
    assert body["temperature_bands"] == [
        {"band": "BELOW_10", "events": 1, "avg_attended": 3.0, "no_show_rate": 0.25},
        {"band": "FROM_10_TO_15", "events": 1, "avg_attended": 1.0, "no_show_rate": 0.5},
        {"band": "FROM_15_TO_20", "events": 1, "avg_attended": 4.0, "no_show_rate": 0.0},
        {"band": "FROM_20_TO_25", "events": 0, "avg_attended": None, "no_show_rate": None},
        {"band": "FROM_25", "events": 1, "avg_attended": 9.0, "no_show_rate": 0.1},
    ]
    assert body["meetups"][0] == {
        "meetup_id": str(seed["m1"].id),
        "name": seed["m1"].name,
        "date": "2026-04-05T22:00:00",
        "attended": 1,
        "no_show_rate": 0.5,
        "avg_temperature": 10.0,
        "rained": True,
        "total_precipitation_mm": 1.5,
        "before_avg_temperature": None,
        "has_weather": True,
    }
    assert [m["has_weather"] for m in body["meetups"]] == [True, True, True, True, True, False]
    assert body["meetups"][5]["avg_temperature"] is None
    assert body["meetups"][5]["rained"] is None


def test_weather_stats_returns_the_event_in_one_event_mode(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    seed = _seed_aggregate(session, org)

    resp = client.get(_stats_url(org), params={"meetup_id": str(seed["m3"].id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["event"] == {
        "status": "OK",
        "weather": {
            "avg_temperature": 25.0,
            "avg_apparent_temperature": 25.0,
            "total_precipitation_mm": 1.5,
            "max_wind_kmh": 12.0,
            "rained": True,
            "fetched_at": "2026-01-01T00:00:00Z",
            "source": "open-meteo-historical-forecast",
        },
        "ready": True,
        "before": {
            "avg_temperature": None,
            "avg_apparent_temperature": None,
            "total_precipitation_mm": None,
            "rained": None,
        },
        "hours": [],
    }
    assert body["meetups"] == []
    assert body["temperature_bands"] == []
    assert body["temperature_bands_before"] == []
    assert body["comparison_kind"] is None


def test_weather_stats_returns_422_when_from_not_before_to(client: TestClient, org: Organization, admin_headers: dict):
    resp = client.get(_stats_url(org), params={"from": "2026-05-01", "to": "2026-05-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["detail"] == (
        "Cannot compute stats: 'from' (2026-05-01) must be before 'to' (2026-05-01). Pick a later 'to' date."
    )


def test_weather_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_stats_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_weather_stats_returns_404_for_meetup_of_other_org(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    other_org = make_org(session, name="Other Org", slug="other-org")
    foreign = _meetup(session, other_org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    resp = client.get(_stats_url(org), params={"meetup_id": str(foreign.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_weather_stats_returns_empty_aggregate_for_empty_period(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_stats_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["meetups"] == []
    assert len(body["temperature_bands"]) == 5
    assert len(body["temperature_bands_before"]) == 5
