"""Tests for app.services.visits: how many earlier meetups of the org a guest attended."""

import uuid
from datetime import UTC, datetime, timedelta
from itertools import count

from sqlmodel import Session

from app.models.models import Guest, Meetup, Organization
from app.services.visits import prior_visits_by_guest
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp

TONIGHT = datetime(2026, 11, 22, 22, 0, tzinfo=UTC)
_urls = count()


def _meetup(session: Session, org: Organization, *, days: int, is_finalized: bool = True) -> Meetup:
    """A meetup `days` from tonight (negative is earlier), with a unique URL."""
    return make_meetup(
        session,
        org=org,
        mazmo_meetup_url=f"https://mazmo.net/test-community/meetup-{next(_urls)}",
        date=TONIGHT + timedelta(days=days),
        is_finalized=is_finalized,
    )


def _setup(session: Session) -> tuple[Organization, Meetup, Guest]:
    org = make_org(session)
    tonight = _meetup(session, org, days=0, is_finalized=False)
    guest = make_guest(session)
    make_rsvp(session, meetup=tonight, guest=guest)
    return org, tonight, guest


def test_guest_with_no_history_has_zero(session: Session) -> None:
    _, tonight, guest = _setup(session)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_only_rsvp_or_no_show_before_is_still_zero(session: Session) -> None:
    org, tonight, guest = _setup(session)
    make_rsvp(session, meetup=_meetup(session, org, days=-30), guest=guest)
    make_rsvp(session, meetup=_meetup(session, org, days=-60), guest=guest, cancelled_rsvp=True)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_attended_earlier_finalized_meetups_counts_them(session: Session) -> None:
    org, tonight, guest = _setup(session)
    make_rsvp(session, meetup=_meetup(session, org, days=-30), guest=guest, has_arrived=True)
    make_rsvp(session, meetup=_meetup(session, org, days=-60), guest=guest, has_arrived=True, is_walkin=True)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 2}


def test_not_finalized_or_later_meetups_do_not_count(session: Session) -> None:
    org, tonight, guest = _setup(session)
    earlier_open = _meetup(session, org, days=-30, is_finalized=False)
    make_rsvp(session, meetup=earlier_open, guest=guest, has_arrived=True)
    make_rsvp(session, meetup=_meetup(session, org, days=30), guest=guest, has_arrived=True)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_current_meetup_does_not_count_itself(session: Session) -> None:
    org = make_org(session)
    tonight = _meetup(session, org, days=0, is_finalized=True)
    guest = make_guest(session)
    make_rsvp(session, meetup=tonight, guest=guest, has_arrived=True)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_other_org_meetups_do_not_count(session: Session) -> None:
    _, tonight, guest = _setup(session)
    other = make_org(session, name="Other Org", slug="other-org")
    make_rsvp(session, meetup=_meetup(session, other, days=-30), guest=guest, has_arrived=True)

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_staff_attendance_does_not_count(session: Session) -> None:
    org, tonight, guest = _setup(session)
    make_rsvp(session, meetup=_meetup(session, org, days=-30), guest=guest, has_arrived=True, guest_type="STAFF")

    assert prior_visits_by_guest(session, tonight, [guest.id]) == {guest.id: 0}


def test_counts_each_guest_separately(session: Session) -> None:
    org, tonight, newcomer = _setup(session)
    regular = make_guest(session, mazmo_user_id=2, mazmo_handle="regular")
    make_rsvp(session, meetup=_meetup(session, org, days=-30), guest=regular, has_arrived=True)

    assert prior_visits_by_guest(session, tonight, [newcomer.id, regular.id]) == {newcomer.id: 0, regular.id: 1}


def test_empty_ids_returns_empty(session: Session) -> None:
    _, tonight, _ = _setup(session)

    assert prior_visits_by_guest(session, tonight, []) == {}


def test_unknown_guest_id_maps_to_zero(session: Session) -> None:
    _, tonight, _ = _setup(session)
    stranger = uuid.uuid4()

    assert prior_visits_by_guest(session, tonight, [stranger]) == {stranger: 0}
