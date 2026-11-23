#!/usr/bin/env python3
"""
Backfill cover_url and excerpt on meetups created before those columns existed.

For every meetup with cover_url NULL, fetches the Mazmo event and stores its
cover and excerpt. It never changes the meetup date. Each updated meetup is
committed on its own, so a failure (or Ctrl+C) keeps the ones already done.
A meetup that fails is logged and skipped; run the script again to retry it.
It is idempotent: meetups that already have a cover are not touched.

Usage:
    uv run python scripts/backfill_meetup_event_details.py

In production, inside the app container:
    docker exec -e PYTHONPATH=/app ec2-user-app-1 python scripts/backfill_meetup_event_details.py
"""

import asyncio
import sys
from typing import NamedTuple

import structlog
from sqlmodel import Session, col, create_engine, select

from app.core.config import Settings, get_settings
from app.core.database import UTC_SESSION
from app.models.models import Meetup
from app.services.mazmo import MazmoAPIError, MazmoClient, MazmoNetworkError

log = structlog.get_logger(__name__)


class BackfillResult(NamedTuple):
    updated: int
    failed: int


async def backfill(session: Session, settings: Settings) -> BackfillResult:
    """Fill cover_url and excerpt on meetups without a cover. Commits once per updated meetup."""
    meetups = session.exec(select(Meetup).where(col(Meetup.cover_url).is_(None))).all()
    updated = 0
    failed = 0

    async with MazmoClient(settings) as client:
        for meetup in meetups:
            meetup_id = str(meetup.id)
            try:
                details = await client.fetch_event_details(meetup.mazmo_meetup_url)
            except (MazmoNetworkError, MazmoAPIError) as exc:
                failed += 1
                log.error("Backfill failed for meetup", meetup_id=meetup_id, error=str(exc))
                continue
            meetup.cover_url = details.cover_url
            meetup.excerpt = details.excerpt
            session.add(meetup)
            session.commit()
            updated += 1
            log.info("Backfilled meetup", meetup_id=meetup_id)

    return BackfillResult(updated=updated, failed=failed)


def main() -> BackfillResult:
    settings = get_settings()
    engine = create_engine(settings.database_url, connect_args=UTC_SESSION)
    with Session(engine) as session:
        result = asyncio.run(backfill(session, settings))
    log.info("Backfill finished", updated=result.updated, failed=result.failed)
    return result


if __name__ == "__main__":
    from app.core.logging import setup_logging

    setup_logging(json_logs=False, log_level="INFO")

    main()
    sys.exit(0)  # Idempotent: failed > 0 is reported in the log, rerun to retry
