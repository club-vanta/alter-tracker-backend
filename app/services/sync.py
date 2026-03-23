"""
Sync service - orchestrates the full guest list refresh.

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
---------------
We use a raw SQLAlchemy Core INSERT ... ON CONFLICT (mazmo_user_id) DO NOTHING.
This is intentional:
  - DO NOTHING (not DO UPDATE) means existing rows are NEVER touched.
  - has_arrived / arrival_time / arrival_order are therefore immutable from
    the sync side - only the check-in endpoint may set them.
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


class GuestSyncer:
    """
    Orchestrates a full guest list refresh from Mazmo into the local database.

    Each method has a single responsibility and can be tested in isolation.
    Call `sync()` to run the full pipeline.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # ── Public entry point ────────────────────────────────────────────────────

    async def sync(self) -> SyncResponse:
        """Run the full sync pipeline and return a summary."""
        rsvps, user_details = await self._fetch_from_mazmo()

        if not rsvps:
            log.warning("Mazmo returned zero RSVPs - nothing to sync.")
            return SyncResponse(
                inserted=0,
                skipped=0,
                total_in_db=self._count_guests(),
            )

        guests_to_insert = self._build_guests(rsvps, user_details)

        if not guests_to_insert:
            log.warning("All RSVPs lacked user detail - nothing inserted.")
            return SyncResponse(
                inserted=0,
                skipped=len(rsvps),
                total_in_db=self._count_guests(),
            )

        inserted = self._upsert_guests(guests_to_insert)
        self._update_cancelled_rsvps(set(rsvps.keys()))

        attempted = len(guests_to_insert)
        skipped = attempted - inserted
        total = self._count_guests()

        log.info(
            "Sync complete - attempted=%d, inserted=%d, skipped=%d, total_in_db=%d",
            attempted,
            inserted,
            skipped,
            total,
        )
        return SyncResponse(inserted=inserted, skipped=skipped, total_in_db=total)

    # ── Private methods ───────────────────────────────────────────────────────

    async def _fetch_from_mazmo(
        self,
    ) -> tuple[dict[MazmoUserId, MazmoRsvpEntry], dict[MazmoUserId, MazmoUserEntry]]:
        """
        Step 1 & 2: Fetch RSVPs and user details from the Mazmo API.
        Returns both as a tuple so they can be used together by the caller.
        """
        async with MazmoClient(self._settings) as client:
            rsvps = await client.fetch_rsvps()
            user_details = await client.fetch_users(list(rsvps.keys()))
        log.info("Fetched %d RSVPs from Mazmo", len(rsvps))
        return rsvps, user_details

    def _build_guests(
        self,
        rsvps: dict[MazmoUserId, MazmoRsvpEntry],
        user_details: dict[MazmoUserId, MazmoUserEntry],
    ) -> list[Guest]:
        """
        Step 3: Build Guest model instances from Mazmo data.
        Skips any RSVP whose user details couldn't be fetched.
        """
        guests: list[Guest] = []
        for user_id, rsvp in rsvps.items():
            user = user_details.get(user_id)
            if user is None:
                log.warning("No user detail found for mazmo_user_id=%d - skipping", user_id)
                continue
            guests.append(
                Guest(
                    mazmo_user_id=user_id,
                    username=user.username,
                    displayname=user.displayname,
                    rsvp_time=rsvp.joinedAt,
                )
            )
        return guests

    def _upsert_guests(self, guests: list[Guest]) -> int:
        """
        Step 4: Insert new guests via Postgres ON CONFLICT DO NOTHING.
        Returns the number of rows actually inserted.
        """
        # Exclude check-in fields so DB server_defaults apply on INSERT.
        rows = [
            g.model_dump(
                exclude={
                    "has_arrived",
                    "arrival_time",
                    "arrival_order",
                    "cancelled_rsvp",
                }
            )
            for g in guests
        ]
        count_before = self._count_guests()

        stmt = (
            pg_insert(Guest).values(rows).on_conflict_do_nothing(index_elements=["mazmo_user_id"])
        )
        self._session.exec(stmt)  # type: ignore[arg-type]
        self._session.commit()

        return self._count_guests() - count_before

    def _update_cancelled_rsvps(self, current_ids: set[MazmoUserId]) -> None:
        """
        Step 5: Flip `cancelled_rsvp` for guests whose status changed.
        - Guests no longer in Mazmo's list are marked as cancelled.
        - Guests who re-RSVP'd are reactivated.
        Only writes rows where the status actually changed.
        """
        all_guests = self._session.exec(select(Guest)).all()
        changed = 0
        for guest in all_guests:
            should_be_cancelled = guest.mazmo_user_id not in current_ids
            if guest.cancelled_rsvp != should_be_cancelled:
                guest.cancelled_rsvp = should_be_cancelled
                self._session.add(guest)
                changed += 1

        if changed:
            self._session.commit()
            log.info("Updated cancelled_rsvp status for %d guest(s)", changed)

    def _count_guests(self) -> int:
        """Returns the current total number of guests in the database."""
        return self._session.exec(select(func.count()).select_from(Guest)).one()


# ── Module-level convenience function ─────────────────────────────────────────


async def sync_guests(session: Session, settings: Settings) -> SyncResponse:
    """Convenience wrapper used by the router."""
    return await GuestSyncer(session, settings).sync()
