"""
Sync service - orchestrates the full guest list refresh for a specific meetup.

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
---------------
Guest table:
  INSERT ... ON CONFLICT (mazmo_user_id) DO NOTHING - identity data is immutable.

MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order) or payment fields (has_paid, paid_at,
  paid_by_id).
"""

import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, func, select

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.models.models import Guest, Meetup, MeetupRsvp
from app.schemas import MazmoRsvpEntry, MazmoUserEntry, SyncResponse
from app.services.mazmo import MazmoClient

log = structlog.get_logger(__name__)


class GuestSyncer:
    """
    Orchestrates a full guest list refresh from Mazmo for a specific meetup.

    Each method has a single responsibility and can be tested in isolation.
    Call `sync()` to run the full pipeline.
    """

    def __init__(self, session: Session, settings: Settings, meetup: Meetup) -> None:
        self._session = session
        self._settings = settings
        self._meetup = meetup

    # ── Public entry point ────────────────────────────────────────────────────

    async def sync(self) -> SyncResponse:
        """Run the full sync pipeline and return a summary."""
        rsvps, user_details = await self._fetch_from_mazmo()

        if not rsvps:
            log.warning("Mazmo returned zero RSVPs", meetup_id=str(self._meetup.id))
            return SyncResponse(
                inserted=0,
                skipped=0,
                total_in_db=self._count_rsvps(),
            )

        guests_to_insert = self._build_guests(rsvps, user_details)

        if not guests_to_insert:
            log.warning("All RSVPs lacked user detail - nothing inserted.")
            return SyncResponse(
                inserted=0,
                skipped=len(rsvps),
                total_in_db=self._count_rsvps(),
            )

        # Guests must be upserted (and their internal ids resolved) before
        # RSVPs can be built, since MeetupRsvp.guest_id is now the internal
        # UUID, not the raw mazmo_user_id.
        self._upsert_guests(guests_to_insert)
        guest_id_map = self._fetch_guest_id_map(list(rsvps.keys()))
        rsvps_to_upsert = self._build_rsvps(rsvps, user_details, guest_id_map)
        inserted = self._upsert_rsvps(rsvps_to_upsert)
        self._update_cancelled_rsvps(guest_id_map)

        total = self._count_rsvps()

        log.info(
            "Sync complete",
            meetup_id=str(self._meetup.id),
            inserted=inserted,
            total_in_db=total,
        )
        return SyncResponse(inserted=inserted, skipped=0, total_in_db=total)

    # ── Private methods ───────────────────────────────────────────────────────

    async def _fetch_from_mazmo(
        self,
    ) -> tuple[dict[MazmoUserId, MazmoRsvpEntry], dict[MazmoUserId, MazmoUserEntry]]:
        """
        Step 1 & 2: Fetch RSVPs and user details from the Mazmo API.
        Returns both as a tuple so they can be used together by the caller.
        """
        async with MazmoClient(self._settings) as client:
            rsvps = await client.fetch_rsvps(self._meetup.mazmo_meetup_url)
            user_details = await client.fetch_users(list(rsvps.keys()))
        log.info(
            "Fetched RSVPs from Mazmo",
            rsvp_count=len(rsvps),
            meetup_id=str(self._meetup.id),
        )
        return rsvps, user_details

    def _build_guests(
        self,
        rsvps: dict[MazmoUserId, MazmoRsvpEntry],
        user_details: dict[MazmoUserId, MazmoUserEntry],
    ) -> list[Guest]:
        """
        Build Guest model instances (identity only) from Mazmo data.
        Skips any RSVP whose user details couldn't be fetched.
        """
        guests: list[Guest] = []
        for user_id in rsvps:
            user = user_details.get(user_id)
            if user is None:
                log.warning("No user detail found for mazmo_user_id=%d - skipping", user_id)
                continue
            guests.append(
                Guest(
                    mazmo_user_id=user_id,
                    mazmo_handle=user.username,
                    displayname=user.displayname,
                )
            )
        return guests

    def _fetch_guest_id_map(self, mazmo_user_ids: list[MazmoUserId]) -> dict[MazmoUserId, uuid.UUID]:
        """
        Resolve mazmo_user_id -> internal guest.id for a batch of Mazmo IDs.

        Must run AFTER _upsert_guests, so it covers both guests that
        already existed and ones just inserted by that upsert.
        """
        rows = self._session.exec(
            select(Guest.mazmo_user_id, Guest.id).where(Guest.mazmo_user_id.in_(mazmo_user_ids))  # type: ignore[union-attr]
        ).all()
        # Guest.mazmo_user_id is nullable at the type level (manual guests have
        # none), but every row here matched the .in_() filter above, so it can
        # never actually be None - the guard just satisfies the type checker.
        return {MazmoUserId(mazmo_user_id): guest_id for mazmo_user_id, guest_id in rows if mazmo_user_id is not None}

    def _build_rsvps(
        self,
        rsvps: dict[MazmoUserId, MazmoRsvpEntry],
        user_details: dict[MazmoUserId, MazmoUserEntry],
        guest_id_map: dict[MazmoUserId, uuid.UUID],
    ) -> list[MeetupRsvp]:
        """
        Build MeetupRsvp model instances from Mazmo data.
        Skips any RSVP whose user details couldn't be fetched.
        """
        result: list[MeetupRsvp] = []
        for user_id, rsvp in rsvps.items():
            if user_id not in user_details:
                continue
            guest_id = guest_id_map.get(user_id)
            if guest_id is None:
                log.warning("No internal guest id found for mazmo_user_id=%d after upsert - skipping", user_id)
                continue
            result.append(
                MeetupRsvp(
                    meetup_id=self._meetup.id,
                    guest_id=guest_id,
                    rsvp_time=rsvp.joinedAt,
                    cancelled_rsvp=False,
                )
            )
        return result

    def _upsert_guests(self, guests: list[Guest]) -> int:
        """
        Insert new guests via Postgres ON CONFLICT DO NOTHING.
        Returns the number of rows actually inserted.
        """
        if not guests:
            return 0

        rows = [g.model_dump(exclude={"rsvps", "meetups"}) for g in guests]
        count_before = self._count_guests()

        stmt = pg_insert(Guest).values(rows).on_conflict_do_nothing(index_elements=["mazmo_user_id"])
        self._session.exec(stmt)  # type: ignore[arg-type]
        self._session.commit()

        return self._count_guests() - count_before

    def _upsert_rsvps(self, rsvps: list[MeetupRsvp]) -> int:
        """
        Upsert RSVPs via Postgres ON CONFLICT DO UPDATE.
        Updates rsvp_time and reactivates cancelled RSVPs.
        NEVER touches check-in or payment fields.
        """
        if not rsvps:
            return 0

        rows = [
            r.model_dump(
                exclude={
                    "guest",
                    "meetup",
                    "has_arrived",
                    "arrival_time",
                    "arrival_order",
                    "has_paid",
                    "paid_at",
                    "paid_by_id",
                }
            )
            for r in rsvps
        ]
        count_before = self._count_rsvps()

        stmt = pg_insert(MeetupRsvp).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["meetup_id", "guest_id"],
            set_={
                "rsvp_time": stmt.excluded.rsvp_time,
                "cancelled_rsvp": False,  # Re-RSVP reactivates
            },
        )
        self._session.exec(stmt)  # type: ignore[arg-type]
        self._session.commit()

        return self._count_rsvps() - count_before

    def _update_cancelled_rsvps(self, guest_id_map: dict[MazmoUserId, uuid.UUID]) -> None:
        """
        Flip `cancelled_rsvp` for RSVPs whose status changed FOR THIS MEETUP.
        - RSVPs whose guest is not in guest_id_map.values() (i.e. not in
          the latest Mazmo fetch) are marked as cancelled.
        Only writes rows where the status actually changed.

        NOTE (preserved from before this refactor): this also re-flags
        walk-in RSVPs as cancelled_rsvp=True if that guest never RSVPed
        on Mazmo, whether or not they have a Mazmo account. This was
        already true for Mazmo-linked walk-ins before this change; it now
        applies uniformly to manual (no-Mazmo) walk-ins too. cancelled_rsvp
        does not block check-in (has_arrived is separate and untouched by
        sync), so this is informational, not a functional regression.
        """
        current_guest_ids = set(guest_id_map.values())
        all_rsvps = self._session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id)).all()
        changed = 0
        for rsvp in all_rsvps:
            should_be_cancelled = rsvp.guest_id not in current_guest_ids
            if rsvp.cancelled_rsvp != should_be_cancelled:
                rsvp.cancelled_rsvp = should_be_cancelled
                self._session.add(rsvp)
                changed += 1

        if changed:
            self._session.commit()
            log.info(
                "Updated cancelled_rsvp status for %d RSVP(s) in meetup %s",
                changed,
                self._meetup.id,
            )

    def _count_guests(self) -> int:
        """Returns the current total number of guests in the database."""
        return self._session.exec(select(func.count()).select_from(Guest)).one()

    def _count_rsvps(self) -> int:
        """Returns the current total number of RSVPs for this meetup."""
        return self._session.exec(
            select(func.count()).select_from(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id)
        ).one()


# ── Module-level convenience function ─────────────────────────────────────────


async def sync_guests(session: Session, settings: Settings, meetup: Meetup) -> SyncResponse:
    """Convenience wrapper used by the router."""
    return await GuestSyncer(session, settings, meetup).sync()
