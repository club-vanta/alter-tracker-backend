"""Tests for scripts/seed_e2e.py, the data the velvet smoke suite runs against."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from app.core.security import verify_password
from app.models.models import EventLog, EventType, Meetup, MeetupRsvp, Organization, User, UserOrganization
from scripts.seed_e2e import ensure_local_database, seed_e2e

PASSWORD = "e2e-insecure-password-123"
NOW = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)


def _org_meetups(session: Session, org_id: str) -> list[Meetup]:
    return list(session.exec(select(Meetup).where(Meetup.org_id == uuid.UUID(org_id))).all())


def test_seeds_the_org_users_meetups_and_rsvps(session: Session):
    result = seed_e2e(session, PASSWORD, now=NOW)

    org = session.exec(select(Organization).where(Organization.slug == "e2e-club")).one()
    assert result["org_id"] == str(org.id)
    assert sorted(m.name for m in _org_meetups(session, str(org.id))) == ["E2E Closing", "E2E Door"]
    door = session.get(Meetup, uuid.UUID(result["meetups"]["door"]["id"]))
    assert door is not None and door.requires_payment is True
    rsvps = session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == door.id)).all()
    assert len(rsvps) == 2
    assert not any(r.has_arrived for r in rsvps)

    roles = {
        m.user.username: m.role
        for m in session.exec(select(UserOrganization).where(UserOrganization.org_id == org.id)).all()
    }
    assert roles == {"e2e-door": "STAFF", "e2e-owner": "ADMIN"}


def test_running_it_again_resets_the_org_activity(session: Session):
    first = seed_e2e(session, PASSWORD, now=NOW)
    org_id = uuid.UUID(first["org_id"])
    door_id = uuid.UUID(first["meetups"]["door"]["id"])
    guest_id = uuid.UUID(first["meetups"]["door"]["guest_ids"]["luna"])
    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == door_id, MeetupRsvp.guest_id == guest_id)
    ).one()
    rsvp.has_arrived = True
    session.add(rsvp)
    session.add(EventLog(event_type=EventType.CHECK_IN, org_id=org_id, meetup_id=door_id, guest_id=guest_id))
    door_user = session.exec(select(User).where(User.username == "e2e-door")).one()
    door_user.is_disabled = True
    session.add(door_user)
    session.commit()

    second = seed_e2e(session, PASSWORD, now=NOW)

    assert second["org_id"] == first["org_id"]
    assert second["meetups"]["door"]["guest_ids"] == first["meetups"]["door"]["guest_ids"]
    assert len(_org_meetups(session, second["org_id"])) == 2
    assert session.get(Meetup, door_id) is None
    assert session.exec(select(EventLog).where(EventLog.org_id == org_id)).all() == []
    new_door_id = uuid.UUID(second["meetups"]["door"]["id"])
    rsvps = session.exec(select(MeetupRsvp).where(MeetupRsvp.meetup_id == new_door_id)).all()
    assert not any(r.has_arrived for r in rsvps)
    session.refresh(door_user)
    assert door_user.is_disabled is False
    assert verify_password(PASSWORD, door_user.hashed_password)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:pass@db.example.com:5432/alter_event_tracker",
        "postgresql+psycopg://user:pass@10.0.0.5/alter_event_tracker",
    ],
)
def test_refuses_a_database_that_is_not_local(url: str):
    with pytest.raises(SystemExit):
        ensure_local_database(url)


def test_accepts_a_local_database():
    ensure_local_database("postgresql+psycopg://user:pass@localhost:5432/alter_event_tracker")
