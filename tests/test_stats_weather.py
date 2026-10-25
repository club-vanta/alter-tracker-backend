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
    located: bool = False,
) -> Meetup:
    n = next(_ids)
    meetup = make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=when,
        is_finalized=True,
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
                hourly={},
                fetched_at=RSVP_TIME,
                source="open-meteo-historical-forecast",
            )
        )
        session.flush()
    return meetup


def _seed_aggregate(session: Session, org: Organization) -> dict[str, Meetup]:
    """Six finalized meetups. Expected no-show rates: rainy 7/20, dry 3/12."""
    return {
        "m1": _meetup(
            session,
            org,
            datetime(2026, 4, 5, 22, tzinfo=UTC),
            attended=8,
            no_show=2,
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
            attended=5,
            no_show=5,
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
            attended=6,
            no_show=2,
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


def test_weather_stats_groups_rainy_and_dry_with_pooled_no_show(session: Session):
    org = make_org(session)
    _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    # rainy: m1 + m3 -> attended (8 + 5) / 2 = 6.5, no-show (2 + 5) / (10 + 10) = 0.35
    assert stats.rainy is not None
    assert (stats.rainy.events, stats.rainy.avg_attended, stats.rainy.no_show_rate) == (2, 6.5, 0.35)
    # dry: m2 + m5 -> attended (3 + 6) / 2 = 4.5, no-show (1 + 2) / (4 + 8) = 0.25
    assert stats.dry is not None
    assert (stats.dry.events, stats.dry.avg_attended, stats.dry.no_show_rate) == (2, 4.5, 0.25)


def test_weather_stats_lists_all_five_bands_in_order(session: Session):
    org = make_org(session)
    _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [(b.band, b.events, b.avg_attended, b.no_show_rate) for b in stats.temperature_bands] == [
        (StatsTemperatureBand.BELOW_10, 1, 3.0, 0.25),
        (StatsTemperatureBand.FROM_10_TO_15, 1, 8.0, 0.2),
        (StatsTemperatureBand.FROM_15_TO_20, 1, 4.0, 0.0),
        (StatsTemperatureBand.FROM_20_TO_25, 0, None, None),
        (StatsTemperatureBand.FROM_25, 1, 5.0, 0.5),
    ]


def test_weather_stats_counts_only_meetups_without_a_row_as_without_weather(session: Session):
    org = make_org(session)
    _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    # m6 has no row; m4 (rained null) and m5 (temperature null) have a row and are not counted here.
    assert stats.without_weather == 1


def test_weather_stats_lists_meetups_by_date_with_null_weather_fields_without_a_row(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert [m.meetup_id for m in stats.meetups] == [seed[k].id for k in ("m1", "m2", "m3", "m4", "m5", "m6")]
    m1, _, _, m4, m5, m6 = stats.meetups
    assert (m1.attended, m1.no_show_rate, m1.avg_temperature, m1.rained, m1.total_precipitation_mm) == (
        8,
        0.2,
        10.0,
        True,
        1.5,
    )
    assert (m4.avg_temperature, m4.rained, m4.total_precipitation_mm) == (17.5, None, None)
    assert (m5.avg_temperature, m5.rained) == (None, False)
    assert (m6.attended, m6.no_show_rate, m6.avg_temperature, m6.rained, m6.total_precipitation_mm) == (
        2,
        0.0,
        None,
        None,
        None,
    )


def test_weather_stats_empty_groups_have_null_figures(session: Session):
    org = make_org(session)

    stats = weather_stats(session, org.id, ALL_TIME, None)

    assert stats.meetups == []
    assert stats.event is None
    assert stats.comparison_kind is None
    assert stats.comparison is None
    assert stats.without_weather == 0
    assert stats.rainy is not None
    assert (stats.rainy.events, stats.rainy.avg_attended, stats.rainy.no_show_rate) == (0, None, None)
    assert [(b.band, b.events, b.avg_attended, b.no_show_rate) for b in stats.temperature_bands] == [
        (band, 0, None, None) for band in StatsTemperatureBand
    ]


def test_weather_stats_respects_the_period_and_skips_unfinalized_meetups(session: Session):
    org = make_org(session)
    inside = _meetup(
        session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=4, weather=True, temperature=12.0, rained=False
    )
    _meetup(
        session, org, datetime(2026, 2, 5, 22, tzinfo=UTC), attended=9, weather=True, temperature=12.0, rained=False
    )
    unfinalized = make_meetup(session, org=org, date=datetime(2026, 4, 6, 22, tzinfo=UTC), is_finalized=False)
    assert unfinalized.is_finalized is False

    stats = weather_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 5, 1)), None)

    assert [m.meetup_id for m in stats.meetups] == [inside.id]


def test_weather_stats_audience_only(session: Session):
    org = make_org(session)
    meetup = _meetup(
        session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=1, weather=True, temperature=12.0, rained=False
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

    assert stats.meetups[0].attended == 1


# -- weather_stats: one event --


def test_weather_stats_one_event_returns_the_status_with_its_attendance(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, ALL_TIME, seed["m1"].id)

    assert stats.event is not None
    assert stats.event.status is WeatherStatus.OK
    assert stats.event.ready is True
    assert stats.event.weather is not None
    assert (stats.event.weather.avg_temperature, stats.event.weather.rained) == (10.0, True)
    assert (stats.event.attended, stats.event.no_show_rate) == (8, 0.2)
    assert stats.meetups == []
    assert stats.rainy is None
    assert stats.dry is None
    assert stats.temperature_bands == []
    assert stats.without_weather == 0
    assert stats.comparison_kind is None
    assert stats.comparison is None


def test_weather_stats_one_event_ignores_period_bounds(session: Session):
    org = make_org(session)
    seed = _seed_aggregate(session, org)

    stats = weather_stats(session, org.id, StatsPeriod(date(2030, 1, 1), date(2030, 2, 1)), seed["m1"].id)

    assert stats.event is not None
    assert stats.event.attended == 8


def test_weather_stats_one_event_without_location(session: Session):
    org = make_org(session)
    meetup = _meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC), attended=3, no_show=1)

    stats = weather_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.event is not None
    assert (stats.event.status, stats.event.weather, stats.event.ready) == (WeatherStatus.NO_LOCATION, None, False)
    assert (stats.event.attended, stats.event.no_show_rate) == (3, 0.25)


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
    assert body["without_weather"] == 1
    assert body["rainy"] == {"events": 2, "avg_attended": 6.5, "no_show_rate": 0.35}
    assert body["dry"] == {"events": 2, "avg_attended": 4.5, "no_show_rate": 0.25}
    assert body["temperature_bands"] == [
        {"band": "BELOW_10", "events": 1, "avg_attended": 3.0, "no_show_rate": 0.25},
        {"band": "FROM_10_TO_15", "events": 1, "avg_attended": 8.0, "no_show_rate": 0.2},
        {"band": "FROM_15_TO_20", "events": 1, "avg_attended": 4.0, "no_show_rate": 0.0},
        {"band": "FROM_20_TO_25", "events": 0, "avg_attended": None, "no_show_rate": None},
        {"band": "FROM_25", "events": 1, "avg_attended": 5.0, "no_show_rate": 0.5},
    ]
    assert body["meetups"][0] == {
        "meetup_id": str(seed["m1"].id),
        "name": seed["m1"].name,
        "date": "2026-04-05T22:00:00",
        "attended": 8,
        "no_show_rate": 0.2,
        "avg_temperature": 10.0,
        "rained": True,
        "total_precipitation_mm": 1.5,
    }
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
        "attended": 5,
        "no_show_rate": 0.5,
    }
    assert body["meetups"] == []
    assert body["rainy"] is None
    assert body["dry"] is None
    assert body["temperature_bands"] == []
    assert body["without_weather"] == 0
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
    assert body["rainy"] == {"events": 0, "avg_attended": None, "no_show_rate": None}
    assert len(body["temperature_bands"]) == 5
    assert body["without_weather"] == 0
