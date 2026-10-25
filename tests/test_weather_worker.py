"""Tests for the weather worker: selection, failure isolation, duplicates, pacing and the stop flag.

Open-Meteo is never called: a FakeClient stands in for OpenMeteoClient. The worker runs against the
rolled-back test session, handed out through a no-op context manager so run_once cannot close it.
"""

import asyncio
from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlmodel import Session, select

from app.jobs import weather_worker
from app.jobs.weather_worker import RunSummary, run_once
from app.models.models import Meetup, MeetupWeather, Organization
from app.services.weather import (
    HourlyData,
    NoWeatherDataError,
    OpenMeteoClient,
    WeatherUnavailableError,
    is_weather_ready,
)
from tests.conftest import make_meetup, make_org

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
LAT = -34.5875
LON = -58.4306


@pytest.fixture(autouse=True)
def clear_stop_flag() -> Iterator[None]:
    weather_worker.STOP.clear()
    yield
    weather_worker.STOP.clear()


class FakeClient:
    """Answers fetch_hourly with six constant hours from the start of the window; can fail per meetup date."""

    def __init__(self) -> None:
        self.calls: list[datetime] = []
        self.failures: dict[datetime, Exception] = {}

    async def fetch_hourly(self, latitude: float, longitude: float, start: datetime) -> HourlyData:
        self.calls.append(start)
        failure = self.failures.get(start)
        if failure is not None:
            raise failure
        first = start.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(6)]
        return {
            "time": list(times),
            "temperature_2m": [15.0] * 6,
            "apparent_temperature": [14.0] * 6,
            "precipitation": [0.0] * 6,
            "wind_speed_10m": [10.0] * 6,
        }


# Test limits: the factory hands out the one test session (a nullcontext, so run_once cannot close it),
# hence per-meetup session isolation and real concurrent writers are not exercised here; the overlap
# test inserts the competing row through that same session. The upsert's ON CONFLICT is what is proven.
def factory(session: Session):
    def open_session() -> AbstractContextManager[Session]:
        return nullcontext(session)

    return open_session


def run(
    session: Session, client: FakeClient, now: datetime = NOW, cooldown: dict[UUID, float] | None = None
) -> RunSummary:
    return asyncio.run(run_once(factory(session), cast(OpenMeteoClient, client), now, cooldown=cooldown))


def located(session: Session, org: Organization, date: datetime, *, name: str | None = None, located: bool = True):
    meetup = make_meetup(
        session,
        org=org,
        date=date,
        name=name or f"Meetup {date.isoformat()}",
        mazmo_meetup_url=f"https://mazmo.net/test/{date.isoformat()}",
    )
    if located:
        meetup.latitude = LAT
        meetup.longitude = LON
        session.add(meetup)
        session.flush()
    return meetup


def stored_ids(session: Session) -> list:
    return list(session.exec(select(MeetupWeather.meetup_id)).all())


@pytest.fixture()
def org(session: Session) -> Organization:
    return make_org(session)


@pytest.fixture()
def instant_clock() -> Iterator[list[float]]:
    """Replace the pacing seams with a clock that only moves when the worker sleeps."""
    state = {"now": 1000.0}
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        state["now"] += seconds

    with (
        patch.object(weather_worker, "_monotonic", lambda: state["now"]),
        patch.object(weather_worker, "_sleep", sleep),
    ):
        yield sleeps


# -- selection ----------------------------------------------------------------


def test_run_once_fetches_only_ready_meetups_without_a_row(session: Session, org: Organization):
    """WHY: Meetups without location, still pending, or already stored must never reach the provider."""
    ready = located(session, org, NOW - timedelta(days=2))
    stored = located(session, org, NOW - timedelta(days=3))
    session.add(
        MeetupWeather(
            meetup_id=stored.id,
            avg_temperature=1.0,
            avg_apparent_temperature=1.0,
            total_precipitation_mm=0.0,
            max_wind_kmh=1.0,
            rained=False,
            hourly={},
            source="old",
        )
    )
    located(session, org, NOW - timedelta(days=4), located=False)
    located(session, org, NOW - timedelta(hours=7))
    session.flush()
    client = FakeClient()

    summary = run(session, client)

    assert client.calls == [ready.date]
    assert summary == RunSummary(fetched=1, failed=0, skipped=0)
    assert set(stored_ids(session)) == {ready.id, stored.id}


def test_run_once_fetches_oldest_first_and_caps_a_run_at_50(session: Session, org: Organization, instant_clock):
    """WHY: Catch-up after downtime must not hammer the provider; the oldest backlog goes first."""
    dates = [NOW - timedelta(days=100) + timedelta(days=i) for i in range(52)]
    for date in reversed(dates):
        located(session, org, date)
    client = FakeClient()

    summary = run(session, client)

    assert [call.replace(tzinfo=UTC) for call in client.calls] == dates[:50]
    assert summary.fetched == 50
    assert len(stored_ids(session)) == 50


@pytest.mark.parametrize(
    ("offset", "selected"),
    [(timedelta(seconds=-1), True), (timedelta(0), True), (timedelta(seconds=1), False)],
)
def test_sql_selection_and_is_weather_ready_agree_on_the_eight_hour_edge(
    session: Session, org: Organization, offset: timedelta, selected: bool
):
    """WHY: The readiness rule lives in one place; the SQL filter and the pure function must not drift."""
    meetup = located(session, org, NOW - timedelta(hours=8) + offset)
    client = FakeClient()

    run(session, client)

    assert is_weather_ready(meetup, NOW) is selected
    assert (client.calls == [meetup.date]) is selected


# -- failures -----------------------------------------------------------------


def test_run_once_logs_failures_and_still_fetches_the_others(session: Session, org: Organization, instant_clock):
    """WHY: One bad meetup (provider down, no data, or a bug) must not stop the rest of the run."""
    outage = located(session, org, NOW - timedelta(days=5))
    no_data = located(session, org, NOW - timedelta(days=4))
    bug = located(session, org, NOW - timedelta(days=3))
    good = located(session, org, NOW - timedelta(days=2))
    client = FakeClient()
    client.failures = {
        outage.date: WeatherUnavailableError("down"),
        no_data.date: NoWeatherDataError("empty"),
        bug.date: RuntimeError("boom"),
    }

    with patch.object(weather_worker, "log") as log:
        summary = run(session, client)

    assert summary == RunSummary(fetched=1, failed=3, skipped=0)
    assert stored_ids(session) == [good.id]
    warnings = {call.kwargs["meetup_id"]: call.kwargs for call in log.warning.call_args_list}
    assert set(warnings) == {str(outage.id), str(no_data.id)}
    assert warnings[str(outage.id)]["error"] == "WeatherUnavailableError"
    assert warnings[str(no_data.id)]["error"] == "NoWeatherDataError"
    assert all(isinstance(kwargs["elapsed"], float) for kwargs in warnings.values())
    log.exception.assert_called_once()
    assert log.exception.call_args.kwargs["meetup_id"] == str(bug.id)


def test_run_once_skips_a_meetup_deleted_after_selection(session: Session, org: Organization, instant_clock):
    """WHY: A meetup removed between selection and fetch is skipped, not a crash."""
    gone = located(session, org, NOW - timedelta(days=3))
    kept = located(session, org, NOW - timedelta(days=2))
    client = FakeClient()
    real_get = Session.get

    def get_and_delete(self: Session, entity, ident, *args, **kwargs):
        if entity is Meetup and ident == gone.id:
            return None
        return real_get(self, entity, ident, *args, **kwargs)

    with patch.object(Session, "get", get_and_delete):
        summary = run(session, client)

    assert summary == RunSummary(fetched=1, failed=0, skipped=1)
    assert stored_ids(session) == [kept.id]


# -- duplicates ---------------------------------------------------------------


def test_run_once_twice_writes_one_row_and_never_refetches(session: Session, org: Organization):
    """WHY: Idempotence: a restarted worker must not refetch or duplicate rows."""
    meetup = located(session, org, NOW - timedelta(days=2))
    client = FakeClient()

    first = run(session, client)
    second = run(session, client)

    assert first.fetched == 1
    assert second == RunSummary(fetched=0, failed=0, skipped=0)
    assert client.calls == [meetup.date]
    assert stored_ids(session) == [meetup.id]


def test_run_once_survives_a_row_inserted_between_selection_and_fetch(session: Session, org: Organization):
    """WHY: Two overlapping runs (deploy while running) end with one row and no crash."""
    meetup = located(session, org, NOW - timedelta(days=2))

    class RacingClient(FakeClient):
        async def fetch_hourly(self, latitude: float, longitude: float, start: datetime) -> HourlyData:
            session.add(
                MeetupWeather(
                    meetup_id=meetup.id,
                    avg_temperature=0.0,
                    avg_apparent_temperature=0.0,
                    total_precipitation_mm=0.0,
                    max_wind_kmh=0.0,
                    rained=False,
                    hourly={},
                    source="other-run",
                )
            )
            session.flush()
            return await super().fetch_hourly(latitude, longitude, start)

    summary = run(session, RacingClient())

    assert summary == RunSummary(fetched=1, failed=0, skipped=0)
    rows = session.exec(select(MeetupWeather)).all()
    assert [(row.meetup_id, row.avg_temperature) for row in rows] == [(meetup.id, 15.0)]


# -- cooldown -----------------------------------------------------------------


def test_failing_old_meetups_do_not_starve_a_newer_ready_one(session: Session, org: Organization, instant_clock):
    """WHY: Meetups the provider never answers (dates it does not cover) must not fill the batch forever."""
    client = FakeClient()
    for i in range(50):
        old = located(session, org, NOW - timedelta(days=4000 + i))
        client.failures[old.date] = WeatherUnavailableError("HTTP 400")
    newer = located(session, org, NOW - timedelta(days=2))
    cooldown: dict[UUID, float] = {}

    first = run(session, client, cooldown=cooldown)
    second = run(session, client, cooldown=cooldown)

    assert first == RunSummary(fetched=0, failed=50, skipped=0)
    assert second == RunSummary(fetched=1, failed=0, skipped=0)
    assert stored_ids(session) == [newer.id]


def test_a_failed_meetup_is_retried_once_its_cooldown_expires(session: Session, org: Organization):
    """WHY: A transient outage must not blacklist a meetup for good."""
    meetup = located(session, org, NOW - timedelta(days=2))
    client = FakeClient()
    client.failures[meetup.date] = WeatherUnavailableError("down")
    cooldown: dict[UUID, float] = {}
    clock = {"now": 1000.0}

    with patch.object(weather_worker, "_monotonic", lambda: clock["now"]):
        run(session, client, cooldown=cooldown)
        assert cooldown == {meetup.id: 1000.0 + weather_worker._RETRY_COOLDOWN_SECONDS}  # pyright: ignore[reportPrivateUsage]
        client.failures.clear()

        clock["now"] += weather_worker._RETRY_COOLDOWN_SECONDS - 1  # pyright: ignore[reportPrivateUsage]
        assert run(session, client, cooldown=cooldown).fetched == 0

        clock["now"] += 1
        assert run(session, client, cooldown=cooldown).fetched == 1
    assert cooldown == {}


# -- pacing -------------------------------------------------------------------


def test_run_once_waits_one_second_between_provider_calls(session: Session, org: Organization, instant_clock):
    """WHY: The provider is called at most once per second, and the first call does not wait."""
    for days in (4, 3, 2):
        located(session, org, NOW - timedelta(days=days))

    run(session, FakeClient())

    assert instant_clock == [1.0, 1.0]


# -- stop flag ----------------------------------------------------------------


def test_request_stop_sets_the_flag_that_stop_requested_reads():
    """WHY: The SIGTERM/SIGINT handler only flips this flag; everything else polls it."""
    assert weather_worker.stop_requested() is False

    weather_worker.request_stop(15, None)

    assert weather_worker.stop_requested() is True


def test_run_once_stops_between_meetups_when_the_flag_is_set(session: Session, org: Organization, instant_clock):
    """WHY: SIGTERM finishes the current meetup and leaves the rest for the next start."""
    first = located(session, org, NOW - timedelta(days=4))
    located(session, org, NOW - timedelta(days=3))
    located(session, org, NOW - timedelta(days=2))

    class StoppingClient(FakeClient):
        async def fetch_hourly(self, latitude: float, longitude: float, start: datetime) -> HourlyData:
            weather_worker.request_stop(15, None)
            return await super().fetch_hourly(latitude, longitude, start)

    summary = run(session, StoppingClient())

    assert summary == RunSummary(fetched=1, failed=0, skipped=2)
    assert stored_ids(session) == [first.id]


def test_run_once_does_nothing_when_the_flag_is_already_set(session: Session, org: Organization):
    located(session, org, NOW - timedelta(days=2))
    client = FakeClient()
    weather_worker.STOP.set()

    summary = run(session, client)

    assert summary == RunSummary(fetched=0, failed=0, skipped=1)
    assert client.calls == []


def test_wait_for_next_run_returns_at_once_when_the_flag_is_set():
    """WHY: The 30 minute sleep must not delay shutdown."""
    weather_worker.STOP.set()

    with patch.object(weather_worker.STOP, "wait", wraps=weather_worker.STOP.wait) as wait:
        weather_worker.wait_for_next_run()

    wait.assert_called_once_with(1.0)


# -- main ---------------------------------------------------------------------


@pytest.fixture()
def main_patches() -> Iterator[MagicMock]:
    run_pass = AsyncMock(return_value=RunSummary(fetched=0, failed=0, skipped=0))
    with (
        patch.object(weather_worker, "_run_pass", run_pass),
        patch.object(weather_worker, "create_engine"),
        patch.object(weather_worker, "setup_logging"),
        patch.object(weather_worker.signal, "signal") as install,
    ):
        run_pass.install = install  # type: ignore[attr-defined]
        yield run_pass  # type: ignore[misc]


def test_main_once_runs_a_single_pass_and_returns_zero(main_patches: MagicMock):
    assert weather_worker.main(["--once"]) == 0

    assert main_patches.await_count == 1


def test_main_creates_an_engine_that_pings_pooled_connections(main_patches: MagicMock):
    """WHY: After a Postgres restart a stale pooled connection must be replaced, not crash the next pass."""
    with patch.object(weather_worker, "create_engine") as create_engine:
        assert weather_worker.main(["--once"]) == 0

    assert create_engine.call_args.kwargs == {"pool_pre_ping": True}


def test_main_installs_stop_handlers_and_loops_until_stopped(main_patches: MagicMock):
    """WHY: Without --once the worker keeps passing every 30 minutes until SIGTERM, then exits 0."""
    waits: list[int] = []

    def stop_after_two_waits() -> None:
        waits.append(1)
        if len(waits) == 2:
            weather_worker.request_stop(15, None)

    with patch.object(weather_worker, "wait_for_next_run", stop_after_two_waits):
        assert weather_worker.main([]) == 0

    assert main_patches.await_count == 2
    installed = {call.args[0] for call in main_patches.install.call_args_list}  # type: ignore[attr-defined]
    assert installed == {weather_worker.signal.SIGTERM, weather_worker.signal.SIGINT}


def test_main_keeps_looping_when_a_pass_crashes(main_patches: MagicMock):
    """WHY: A database blip must not kill the worker; it logs and waits for the next pass."""
    main_patches.side_effect = [RuntimeError("db down"), RunSummary(fetched=0, failed=0, skipped=0)]
    waits: list[int] = []

    def stop_after_two_waits() -> None:
        waits.append(1)
        if len(waits) == 2:
            weather_worker.request_stop(15, None)

    with patch.object(weather_worker, "wait_for_next_run", stop_after_two_waits):
        assert weather_worker.main([]) == 0

    assert main_patches.await_count == 2
