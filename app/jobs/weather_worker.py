"""
Weather worker: fetches the weather of finished meetups, as its own process.

    python -m app.jobs.weather_worker [--once]

Each pass selects, in SQL, the weather-ready meetups (see weather_ready_clause) that have no
meetup_weather row, oldest first, at most 50, and fetches them one by one at 1 request per second.
Without --once the worker passes immediately (catch-up) and then every 30 minutes.

Safety
------
- One session per meetup, so a transaction is short and a failure rolls back only that meetup.
- A provider failure is logged and the pass continues; any other exception is logged with its
  traceback and the pass continues too. One bad row never kills the worker.
- The worker never refetches an existing row (the selection excludes it). Overlapping workers
  are safe: the upsert is ON CONFLICT, so the primary key leaves one row.
- SIGTERM/SIGINT set a flag checked between meetups; the current meetup finishes, then exit 0.
"""

import argparse
import asyncio
import signal
import time
from collections.abc import Callable, Collection, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from types import FrameType
from uuid import UUID

import structlog
from sqlalchemy import Engine
from sqlmodel import Session, col, create_engine, select

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.models.models import Meetup, MeetupWeather
from app.services.weather import (
    OpenMeteoClient,
    WeatherUnavailableError,
    fetch_and_store_weather,
    weather_ready_clause,
)

log = structlog.get_logger(__name__)

BATCH_SIZE = 50
_MIN_INTERVAL_SECONDS = 1.0
_PASS_INTERVAL_SECONDS = 30 * 60
_STOP_POLL_SECONDS = 1.0
_RETRY_COOLDOWN_SECONDS = 6 * 60 * 60

# Seams for tests: pacing reads the clock and sleeps only through these.
_sleep = asyncio.sleep
_monotonic = time.monotonic

# Set by the signal handler, polled between meetups and while idle.
STOP = Event()

type SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class RunSummary:
    """Outcome of one pass: stored rows, failed meetups, and selected meetups left unprocessed."""

    fetched: int
    failed: int
    skipped: int


# -- Stop flag ----------------------------------------------------------------


def request_stop(signum: int, frame: FrameType | None) -> None:
    """Signal handler: ask the worker to stop once the current meetup is done."""
    STOP.set()


def stop_requested() -> bool:
    return STOP.is_set()


def wait_for_next_run() -> None:
    """Sleep 30 minutes in 1 second slices, returning early once a stop is requested."""
    for _ in range(int(_PASS_INTERVAL_SECONDS / _STOP_POLL_SECONDS)):
        if STOP.wait(_STOP_POLL_SECONDS):
            return


# -- One pass -----------------------------------------------------------------


def _select_pending(session_factory: SessionFactory, now: datetime, cooling: Collection[UUID]) -> list[UUID]:
    """Ids of ready meetups without a weather row, oldest first, capped at BATCH_SIZE, minus those cooling down."""
    has_row = select(MeetupWeather.meetup_id).where(col(MeetupWeather.meetup_id) == col(Meetup.id)).exists()
    statement = select(Meetup.id).where(weather_ready_clause(now), ~has_row)
    if cooling:
        statement = statement.where(col(Meetup.id).not_in(cooling))
    statement = statement.order_by(col(Meetup.date), col(Meetup.id)).limit(BATCH_SIZE)
    with session_factory() as session:
        return list(session.exec(statement).all())


async def _wait_for_turn(last_call: float | None) -> None:
    """Sleep until MIN_INTERVAL passed since the previous provider call."""
    if last_call is not None:
        wait = _MIN_INTERVAL_SECONDS - (_monotonic() - last_call)
        if wait > 0:
            await _sleep(wait)


async def run_once(
    session_factory: SessionFactory,
    client: OpenMeteoClient,
    now: datetime,
    *,
    cooldown: dict[UUID, float] | None = None,
) -> RunSummary:
    """Fetch and store the weather of every pending ready meetup, one session each.

    `cooldown` maps a meetup id to the _monotonic time before which it is not retried. Every failure
    is recorded in it, so meetups the provider can never answer (dates it does not cover) cannot fill
    the oldest-first batch and starve newer ones. Pass the same dict on every pass; it lives in memory,
    so a restart retries everything once.
    """
    cooldown = {} if cooldown is None else cooldown
    for meetup_id in [m for m, retry_after in cooldown.items() if retry_after <= _monotonic()]:
        del cooldown[meetup_id]
    pending = _select_pending(session_factory, now, cooldown.keys())
    fetched = failed = 0
    last_call: float | None = None
    for meetup_id in pending:
        if stop_requested():
            break
        # Pace before opening the session so no transaction idles during the sleep.
        await _wait_for_turn(last_call)
        last_call = _monotonic()
        with session_factory() as session:
            meetup = session.get(Meetup, meetup_id)
            if meetup is None:
                continue  # deleted since the selection, counted as skipped
            try:
                await fetch_and_store_weather(session, meetup, client)
            except WeatherUnavailableError as exc:
                failed += 1
                cooldown[meetup_id] = _monotonic() + _RETRY_COOLDOWN_SECONDS
                log.warning(
                    "Weather fetch failed",
                    meetup_id=str(meetup_id),
                    error=type(exc).__name__,
                    elapsed=_monotonic() - last_call,
                )
            except Exception:
                failed += 1
                cooldown[meetup_id] = _monotonic() + _RETRY_COOLDOWN_SECONDS
                log.exception("Unexpected error fetching weather", meetup_id=str(meetup_id))
            else:
                fetched += 1
    return RunSummary(fetched, failed, skipped=len(pending) - fetched - failed)


async def _run_pass(engine: Engine, cooldown: dict[UUID, float]) -> RunSummary:
    async with OpenMeteoClient(get_settings()) as client:
        return await run_once(lambda: Session(engine), client, datetime.now(UTC), cooldown=cooldown)


# -- Entry point --------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the weather of finished meetups.")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(json_logs=settings.json_logs, log_level=settings.log_level)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    engine = create_engine(settings.database_url)
    cooldown: dict[UUID, float] = {}
    try:
        while not stop_requested():
            try:
                summary = asyncio.run(_run_pass(engine, cooldown))
            except Exception:
                log.exception("Weather pass crashed")  # e.g. database down: wait and try again
            else:
                log.info(
                    "Weather pass finished", fetched=summary.fetched, failed=summary.failed, skipped=summary.skipped
                )
            if args.once:
                break
            wait_for_next_run()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
