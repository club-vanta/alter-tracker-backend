"""
Sync service - orchestrates the full guest list refresh.

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
───────────────
We use a raw SQLAlchemy Core INSERT ... ON CONFLICT (mazmo_user_id) DO NOTHING.
This is intentional:
  - DO NOTHING (not DO UPDATE) means existing rows are NEVER touched.
  - has_arrived / arrival_time / arrival_order are therefore immutable from
    the sync side - only the check-in endpoint may set them.
  - The `inserted` count comes from the rowcount of the INSERT statement;
    rows that were skipped due to conflict report rowcount=0 per PG spec.
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, func, select

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.models.models import Guest
from app.schemas.schemas import MazmoRsvpEntry, MazmoUserEntry, SyncResponse
from app.services.mazmo import MazmoClient

log = logging.getLogger(__name__)


async def sync_guests(session: Session, settings: Settings) -> SyncResponse:
    """
    1. Fetch RSVPs + user details from Mazmo.
    2. Upsert into the `guests` table (DO NOTHING on conflict).
    3. Return counts of inserted vs skipped rows.
    """
    async with MazmoClient(settings) as client:
        rsvps: dict[MazmoUserId, MazmoRsvpEntry] = await client.fetch_rsvps()
        user_details: dict[MazmoUserId, MazmoUserEntry] = await client.fetch_users(
            list(rsvps.keys())
        )

    if not rsvps:
        log.warning("Mazmo returned zero RSVPs - nothing to sync.")
        total = session.exec(select(func.count()).select_from(Guest)).one()
        return SyncResponse(inserted=0, skipped=0, total_in_db=total)

    # Build Guest instances — using the model directly keeps the field list
    # in sync automatically. model_dump() produces the dict pg_insert needs.
    guests_to_insert: list[Guest] = []
    for user_id, rsvp in rsvps.items():
        user = user_details.get(user_id)
        if user is None:
            log.warning("No user detail found for mazmo_user_id=%d - skipping", user_id)
            continue

        guests_to_insert.append(
            Guest(
                mazmo_user_id=user_id,
                username=user.username,
                displayname=user.displayname,
                rsvp_time=rsvp.joinedAt,
            )
        )

    if not guests_to_insert:
        log.warning("All RSVPs lacked user detail - nothing inserted.")
        total = session.exec(select(func.count()).select_from(Guest)).one()
        return SyncResponse(inserted=0, skipped=len(rsvps), total_in_db=total)

    # ── Postgres upsert ───────────────────────────────────────────────────────
    # Exclude unset check-in fields so the DB server_defaults apply on INSERT.
    rows = [
        g.model_dump(exclude={"has_arrived", "arrival_time", "arrival_order"})
        for g in guests_to_insert
    ]
    count_before: int = session.exec(select(func.count()).select_from(Guest)).one()

    stmt = pg_insert(Guest).values(rows).on_conflict_do_nothing(index_elements=["mazmo_user_id"])
    session.exec(stmt)  # type: ignore[arg-type]
    session.commit()

    total: int = session.exec(select(func.count()).select_from(Guest)).one()
    inserted: int = total - count_before
    attempted: int = len(guests_to_insert)
    skipped: int = attempted - inserted

    log.info(
        "Sync complete - attempted=%d, inserted=%d, skipped=%d, total_in_db=%d",
        attempted,
        inserted,
        skipped,
        total,
    )
    return SyncResponse(inserted=inserted, skipped=skipped, total_in_db=total)
