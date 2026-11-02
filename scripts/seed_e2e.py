#!/usr/bin/env python3
"""
Seed (or reset) the data the velvet end-to-end smoke suite runs against.

Everything lives in one organization, "E2E Club", so it never mixes with other
local data:

- users: e2e-door (org STAFF) and e2e-owner (org ADMIN), approved, with the
  password from E2E_PASSWORD
- meetups: "E2E Door" (today, requires payment) for the check-in flow and
  "E2E Closing" (today) for the finalize flow, each with two RSVPs
- guests: two manual (no Mazmo) guests, "E2E Luna" and "E2E Sol"

Running it again resets that organization's meetups, RSVPs, bans and event log
to the same starting point, so each smoke test can start from a known state.
Users are never deleted (see CLAUDE.md, soft deletes): they are re-approved,
re-enabled and get their password reset instead.

Refuses to run against a database that is not on localhost.

Prints the seeded IDs as one JSON line on stdout, for Cypress's `db:seed` task.

Usage:
    E2E_PASSWORD=... uv run python scripts/seed_e2e.py
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from urllib.parse import urlparse

from sqlmodel import Session, col, create_engine, delete, select

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.models import (
    EventLog,
    Guest,
    Meetup,
    MeetupRsvp,
    Organization,
    OrganizationBan,
    OrgRole,
    PossibleRoles,
    Role,
    User,
    UserOrganization,
)

ORG_NAME = "E2E Club"
ORG_SLUG = "e2e-club"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

USERS: dict[str, OrgRole] = {"e2e-door": OrgRole.STAFF, "e2e-owner": OrgRole.ADMIN}
GUESTS: dict[str, str] = {"luna": "E2E Luna", "sol": "E2E Sol"}


class SeededMeetup(TypedDict):
    id: str
    name: str
    guest_ids: dict[str, str]


class SeedResult(TypedDict):
    org_id: str
    users: list[str]
    meetups: dict[str, SeededMeetup]


def _upsert_user(session: Session, username: str, password: str, role_id: int) -> User:
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        user = User(username=username, hashed_password="", role_id=role_id)
    user.hashed_password = get_password_hash(password)
    user.is_approved = True
    user.is_disabled = False
    user.disabled_at = None
    user.disabled_by_id = None
    user.disabled_reason = None
    user.role_id = role_id
    session.add(user)
    session.flush()
    return user


def _upsert_org(session: Session) -> Organization:
    org = session.exec(select(Organization).where(Organization.slug == ORG_SLUG)).first()
    if org is None:
        org = Organization(name=ORG_NAME, slug=ORG_SLUG)
        session.add(org)
        session.flush()
    return org


def _reset_org_activity(session: Session, org: Organization) -> None:
    """Deletes the org's meetups and everything that hangs off them."""
    meetup_ids = session.exec(select(Meetup.id).where(Meetup.org_id == org.id)).all()
    session.exec(delete(EventLog).where(col(EventLog.org_id) == org.id))
    if meetup_ids:
        session.exec(delete(EventLog).where(col(EventLog.meetup_id).in_(meetup_ids)))
        session.exec(delete(MeetupRsvp).where(col(MeetupRsvp.meetup_id).in_(meetup_ids)))
        session.exec(delete(Meetup).where(col(Meetup.id).in_(meetup_ids)))
    session.exec(delete(OrganizationBan).where(col(OrganizationBan.org_id) == org.id))
    session.flush()


def _upsert_guest(session: Session, key: str, displayname: str) -> Guest:
    handle = f"e2e.{key}"
    guest = session.exec(
        select(Guest).where(Guest.instagram_username == handle, col(Guest.mazmo_user_id).is_(None))
    ).first()
    if guest is None:
        guest = Guest(mazmo_user_id=None, mazmo_handle=None, displayname=displayname, instagram_username=handle)
    guest.displayname = displayname
    session.add(guest)
    session.flush()
    return guest


def seed_e2e(session: Session, password: str, now: datetime | None = None) -> SeedResult:
    """Creates or resets the E2E organization. Commits the session."""
    now = now or datetime.now(UTC)
    user_role = session.exec(select(Role).where(Role.name == PossibleRoles.USER)).one()
    if user_role.id is None:
        raise RuntimeError("USER role has no id - run migrations first (uv run alembic upgrade head)")

    org = _upsert_org(session)
    _reset_org_activity(session, org)

    for username, org_role in USERS.items():
        user = _upsert_user(session, username, password, user_role.id)
        membership = session.get(UserOrganization, (user.id, org.id))
        if membership is None:
            membership = UserOrganization(user_id=user.id, org_id=org.id, role=org_role)  # type: ignore[arg-type]
        membership.role = org_role
        session.add(membership)

    guests = {key: _upsert_guest(session, key, name) for key, name in GUESTS.items()}

    meetups: dict[str, SeededMeetup] = {}
    for key, name, requires_payment in (("door", "E2E Door", True), ("closing", "E2E Closing", False)):
        meetup = Meetup(
            org_id=org.id,
            name=name,
            mazmo_meetup_url=f"https://mazmo.net/+e2e/{key}",
            date=now,
            requires_payment=requires_payment,
        )
        session.add(meetup)
        session.flush()
        for guest in guests.values():
            session.add(MeetupRsvp(meetup_id=meetup.id, guest_id=guest.id, rsvp_time=now - timedelta(days=2)))
        meetups[key] = {
            "id": str(meetup.id),
            "name": name,
            "guest_ids": {key: str(guest.id) for key, guest in guests.items()},
        }

    session.commit()
    return {"org_id": str(org.id), "users": list(USERS), "meetups": meetups}


def ensure_local_database(database_url: str) -> None:
    """Exits unless the database is on this machine."""
    host = urlparse(database_url.replace("postgresql+psycopg", "postgresql")).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing to seed E2E data into a non-local database (host={host!r}).")


if __name__ == "__main__":
    settings = get_settings()
    ensure_local_database(settings.database_url)
    e2e_password = os.environ.get("E2E_PASSWORD")
    if not e2e_password:
        sys.exit("Set E2E_PASSWORD (15+ characters) to the password the smoke suite logs in with.")
    with Session(create_engine(settings.database_url)) as db_session:
        print(json.dumps(seed_e2e(db_session, e2e_password)))
