"""
Sync service - orchestrates the full guest list refresh for a specific
meetup (sync_guests), and the Mazmo data refresh for every linked guest
(refresh_mazmo_guests).

Kept separate from the router so the logic can be unit-tested or called from
a scheduled job in the future without going through HTTP.

Upsert strategy
---------------
Guest table:
  INSERT ... ON CONFLICT (mazmo_user_id) DO UPDATE SET displayname = ...
  WHERE guests.displayname IS DISTINCT FROM EXCLUDED.displayname
  RETURNING id, displayname, (xmax = 0) AS was_insert - atomic against
  concurrent syncs of different meetups sharing the same guest. Only
  displayname is ever updated; mazmo_handle and other identity fields
  never change via sync. Every row that appears in RETURNING (new
  insert or real displayname change) gets a GuestDisplaynameHistory row.
  Rows where was_insert is False (an existing guest's value changed)
  also get an EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None) - a
  brand new guest's first value is not a "change" worth an audit event.

MeetupRsvp table:
  INSERT ... ON CONFLICT (meetup_id, guest_id) DO UPDATE - updates rsvp_time and
  reactivates cancelled RSVPs. NEVER touches check-in fields (has_arrived,
  arrival_time, arrival_order), payment fields (has_paid, paid_at,
  paid_by_id), or guest_type.

GuestMazmoProfile table:
  INSERT ... ON CONFLICT (guest_id) DO UPDATE - unconditional, unlike the
  Guest table's displayname upsert: every guest in the sync batch gets
  this table refreshed (avatar_url, age, gender, pronoun,
  mazmo_suspended, mazmo_banned, synced_at) on every sync, whether or
  not their displayname changed - these fields can change independently
  of displayname. Runs after _fetch_guest_id_map(), since it needs the
  resolved internal guest_id for every guest in the batch, not just the
  ones _upsert_guests()'s RETURNING clause reported as changed/inserted.

Accounts Mazmo anonymized (is_anonymized(): deleted, or banned with
a hashed name) come back with a hashed displayname and cleared profile
fields. For them the Guest upsert only inserts (and restores a name an
older sync already replaced with the hash), and the GuestMazmoProfile
upsert only updates the account flags and synced_at.

Both upserts live in module-level functions (upsert_guest_displaynames,
upsert_mazmo_profiles) so refresh_mazmo_guests reuses them as is.
"""

import uuid
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime

import structlog
from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, func, select

from app.core.config import Settings
from app.domain_types import MazmoUserId
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
)
from app.schemas import GuestMazmoRefreshResponse, MazmoRsvpEntry, MazmoUserEntry, SyncResponse
from app.schemas.mazmo import is_anonymized
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
        self._upsert_guests(guests_to_insert, _anonymized_ids(user_details))
        guest_id_map = self._fetch_guest_id_map(list(rsvps.keys()))
        self._upsert_mazmo_profiles(guest_id_map, user_details)
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

    def _upsert_guests(self, guests: list[Guest], anonymized_ids: AbstractSet[MazmoUserId] = frozenset()) -> None:
        """See upsert_guest_displaynames."""
        upsert_guest_displaynames(self._session, guests, anonymized_ids)

    def _upsert_mazmo_profiles(
        self,
        guest_id_map: dict[MazmoUserId, uuid.UUID],
        user_details: dict[MazmoUserId, MazmoUserEntry],
    ) -> None:
        """
        See upsert_mazmo_profiles. Must run after _fetch_guest_id_map(),
        not right after _upsert_guests(): it needs the internal guest_id of
        every guest in the batch, not just the ones _upsert_guests reported
        as changed/inserted - see the caller in sync().
        """
        upsert_mazmo_profiles(self._session, guest_id_map, user_details)

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

        Walk-in RSVPs are skipped: staff added them at the door, so their
        absence from Mazmo's list says nothing about whether they cancelled.
        add-walkin refuses to create a walk-in over an existing RSVP row, so
        a walk-in row never started out as a Mazmo RSVP.
        """
        current_guest_ids = set(guest_id_map.values())
        all_rsvps = self._session.exec(
            select(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id).where(MeetupRsvp.is_walkin == False)  # noqa: E712
        ).all()
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

    def _count_rsvps(self) -> int:
        """Returns the current total number of RSVPs for this meetup."""
        return self._session.exec(
            select(func.count()).select_from(MeetupRsvp).where(MeetupRsvp.meetup_id == self._meetup.id)
        ).one()


# ── Shared upserts (used by the meetup sync and the all-guests refresh) ───────


def upsert_guest_displaynames(
    session: Session,
    guests: list[Guest],
    anonymized_ids: AbstractSet[MazmoUserId] = frozenset(),
) -> None:
    """
    Upsert guests via Postgres ON CONFLICT DO UPDATE, atomic against
    concurrent syncs of different meetups sharing the same guest
    (Guest is a global, not per-org, table).

    Only displayname is ever updated on conflict - mazmo_handle and
    other identity fields are immutable via sync. Guests whose
    displayname is unchanged are excluded by the WHERE clause and
    never appear in RETURNING, so they get no history/event row.

    Every row that DOES appear in RETURNING gets a
    GuestDisplaynameHistory row with source=SYNC. Only rows where
    was_insert is False (the guest already existed) additionally
    get an EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None) - a
    brand new guest's first displayname is not a "change".

    Guests in anonymized_ids (accounts Mazmo anonymized) are only
    inserted, never updated: Mazmo replaces their displayname with a
    hash, which must not overwrite the real name. Guests that already
    got that hash stored get their last real name back - see
    _restore_names_hidden_by_hash.
    """
    if not guests:
        return

    live = [g for g in guests if g.mazmo_user_id not in anonymized_ids]
    anonymized = [g for g in guests if g.mazmo_user_id in anonymized_ids]

    # Referencing the underlying sa.Table (Guest.__table__) instead of
    # Guest.displayname/Guest.id directly: SQLModel's class-level field
    # annotations describe the Pydantic instance type (str, uuid.UUID)
    # for basedpyright's benefit, not the SQLAlchemy Column - using them
    # directly in a Core where=/returning() clause type-checks as "str
    # has no attribute is_distinct_from". Guest.__table__ itself has no
    # static stub either, hence the one type: ignore below.
    table = Guest.__table__  # type: ignore[attr-defined]

    if live:
        stmt = pg_insert(Guest).values([g.model_dump(exclude={"rsvps", "meetups"}) for g in live])
        stmt = stmt.on_conflict_do_update(
            index_elements=["mazmo_user_id"],
            set_={"displayname": stmt.excluded.displayname},
            where=table.c.displayname.is_distinct_from(stmt.excluded.displayname),
        ).returning(
            table.c.id,
            table.c.displayname,
            literal_column("(xmax = 0)").label("was_insert"),
        )
        for guest_id, displayname, was_insert in session.exec(stmt).all():  # type: ignore[arg-type]
            _record_displayname(
                session,
                guest_id,
                displayname,
                event_reason=None if was_insert else f"Displayname changed to '{displayname[:400]}' via Mazmo sync",
            )

    if anonymized:
        stmt = (
            pg_insert(Guest)
            .values([g.model_dump(exclude={"rsvps", "meetups"}) for g in anonymized])
            .on_conflict_do_nothing(index_elements=["mazmo_user_id"])
            .returning(table.c.id, table.c.displayname)
        )
        for guest_id, displayname in session.exec(stmt).all():  # type: ignore[arg-type]
            _record_displayname(session, guest_id, displayname, event_reason=None)
        _restore_names_hidden_by_hash(session, anonymized)

    session.commit()


def _record_displayname(session: Session, guest_id: uuid.UUID, displayname: str, event_reason: str | None) -> None:
    """Add a SYNC history row, plus a GUEST_DISPLAYNAME_CHANGED event when event_reason is given."""
    now = datetime.now(UTC)
    session.add(
        GuestDisplaynameHistory(
            guest_id=guest_id,
            displayname=displayname,
            source=GuestDisplaynameSource.SYNC,
            actor_id=None,
            recorded_at=now,
        )
    )
    if event_reason is not None:
        session.add(
            EventLog(
                event_type=EventType.GUEST_DISPLAYNAME_CHANGED,
                org_id=None,
                actor_id=None,
                guest_id=guest_id,
                timestamp=now,
                reason=event_reason,
            )
        )


def _restore_names_hidden_by_hash(session: Session, anonymized: list[Guest]) -> None:
    """
    Give back the last real displayname to anonymized-account guests
    whose stored displayname is still Mazmo's hash.

    Syncs that ran before anonymized accounts were detected stored that hash like
    any rename. The guest's history still has the name it replaced, so the
    newest history value that differs from the hash is restored, recorded
    as a regular SYNC history row and audit event. Guests with no other
    value in their history are left as they are.
    """
    hash_by_mazmo_id = {g.mazmo_user_id: g.displayname for g in anonymized}
    stored = session.exec(
        select(Guest).where(Guest.mazmo_user_id.in_(list(hash_by_mazmo_id)))  # type: ignore[union-attr]
    ).all()
    for guest in stored:
        if guest.displayname != hash_by_mazmo_id[guest.mazmo_user_id]:
            continue
        previous = session.exec(
            select(GuestDisplaynameHistory.displayname)
            .where(GuestDisplaynameHistory.guest_id == guest.id)
            .where(GuestDisplaynameHistory.displayname != guest.displayname)
            .order_by(col(GuestDisplaynameHistory.recorded_at).desc(), col(GuestDisplaynameHistory.id).desc())
            .limit(1)
        ).first()
        if previous is None:
            continue
        guest.displayname = previous
        session.add(guest)
        _record_displayname(
            session,
            guest.id,
            previous,
            event_reason=f"Displayname restored to '{previous[:400]}': Mazmo anonymized the account",
        )
        log.info("Restored displayname of anonymized Mazmo account", guest_id=str(guest.id))


def upsert_mazmo_profiles(
    session: Session,
    guest_id_map: dict[MazmoUserId, uuid.UUID],
    user_details: dict[MazmoUserId, MazmoUserEntry],
) -> None:
    """
    Unconditionally upsert GuestMazmoProfile for every guest in
    guest_id_map that has an entry in user_details - unlike
    upsert_guest_displaynames, there is no "only if changed" gate:
    mazmo_suspended/age/gender/pronoun/avatar_url can all change
    independently of displayname.

    For an account Mazmo anonymized (deleted, or banned with a hashed
    name) only the account flags and synced_at are updated: Mazmo clears
    avatar/age/gender/pronoun, and the last known values are more useful
    at the door than blanks.
    """
    now = datetime.now(UTC)
    live: list[GuestMazmoProfile] = []
    anonymized: list[GuestMazmoProfile] = []
    for mazmo_user_id, guest_id in guest_id_map.items():
        user = user_details.get(mazmo_user_id)
        if user is None:
            continue
        profile = GuestMazmoProfile(
            guest_id=guest_id,
            avatar_url=user.avatar.default if user.avatar else None,
            age=user.age,
            gender=user.gender[:32] if user.gender else None,
            pronoun=user.pronoun[:32] if user.pronoun else None,
            mazmo_suspended=user.suspended,
            mazmo_banned=user.banned,
            mazmo_deleted=user.deleted,
            synced_at=now,
        )
        (anonymized if is_anonymized(user) else live).append(profile)

    for profiles, updated_columns in (
        (
            live,
            (
                "avatar_url",
                "age",
                "gender",
                "pronoun",
                "mazmo_suspended",
                "mazmo_banned",
                "mazmo_deleted",
                "synced_at",
            ),
        ),
        (anonymized, ("mazmo_suspended", "mazmo_banned", "mazmo_deleted", "synced_at")),
    ):
        if not profiles:
            continue
        stmt = pg_insert(GuestMazmoProfile).values([p.model_dump(exclude={"guest"}) for p in profiles])
        stmt = stmt.on_conflict_do_update(
            index_elements=["guest_id"],
            set_={column: stmt.excluded[column] for column in updated_columns},
        )
        session.exec(stmt)  # type: ignore[arg-type]
    session.commit()


def _anonymized_ids(user_details: dict[MazmoUserId, MazmoUserEntry]) -> set[MazmoUserId]:
    return {mazmo_user_id for mazmo_user_id, user in user_details.items() if is_anonymized(user)}


# ── Module-level convenience functions ────────────────────────────────────────


async def sync_guests(session: Session, settings: Settings, meetup: Meetup) -> SyncResponse:
    """Convenience wrapper used by the router."""
    return await GuestSyncer(session, settings, meetup).sync()


async def refresh_mazmo_guests(session: Session, settings: Settings) -> GuestMazmoRefreshResponse:
    """
    Refresh displayname and GuestMazmoProfile for every Mazmo-linked guest.

    Covers guests synced before GuestMazmoProfile existed and guests who
    haven't RSVPed to a recently synced meetup. Never touches RSVPs.
    Guests that Mazmo's /users endpoint leaves out of its response are
    counted as not_found and left as they are.
    """
    rows = session.exec(
        select(Guest.id, Guest.mazmo_user_id, Guest.mazmo_handle).where(Guest.mazmo_user_id.is_not(None))  # type: ignore[union-attr]
    ).all()
    guest_id_map: dict[MazmoUserId, uuid.UUID] = {
        MazmoUserId(mazmo_user_id): guest_id for guest_id, mazmo_user_id, _ in rows if mazmo_user_id is not None
    }
    handles = {MazmoUserId(mazmo_user_id): handle for _, mazmo_user_id, handle in rows if mazmo_user_id is not None}

    async with MazmoClient(settings) as client:
        user_details = await client.fetch_users(list(guest_id_map.keys()))

    # mazmo_handle comes from our own row, not Mazmo's response: the
    # upsert never updates it on conflict, and every guest here exists.
    upsert_guest_displaynames(
        session,
        [
            Guest(mazmo_user_id=mazmo_user_id, mazmo_handle=handles[mazmo_user_id], displayname=user.displayname)
            for mazmo_user_id, user in user_details.items()
            if mazmo_user_id in guest_id_map
        ],
        _anonymized_ids(user_details),
    )
    upsert_mazmo_profiles(session, guest_id_map, user_details)

    refreshed = sum(1 for mazmo_user_id in guest_id_map if mazmo_user_id in user_details)
    return GuestMazmoRefreshResponse(
        total=len(guest_id_map),
        refreshed=refreshed,
        not_found=len(guest_id_map) - refreshed,
    )
