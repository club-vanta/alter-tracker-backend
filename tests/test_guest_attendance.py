"""
Tests for guest meetup attendance: the attendance_status rule and
GET /organizations/{org_id}/guests/{guest_id}/meetups.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.models import GuestAttendanceStatus, Meetup, MeetupRsvp
from app.services.attendance import attendance_status

# ── attendance_status rule ────────────────────────────────────────────────────


def _rsvp(*, has_arrived: bool = False, cancelled_rsvp: bool = False, is_walkin: bool = False) -> MeetupRsvp:
    return MeetupRsvp(
        meetup_id=uuid.uuid4(),
        guest_id=uuid.uuid4(),
        rsvp_time=datetime.now(UTC),
        has_arrived=has_arrived,
        cancelled_rsvp=cancelled_rsvp,
        is_walkin=is_walkin,
    )


def _meetup(*, is_finalized: bool = False) -> Meetup:
    return Meetup(
        org_id=uuid.uuid4(),
        mazmo_meetup_url="https://mazmo.net/x/y-1",
        name="M",
        date=datetime.now(UTC),
        is_finalized=is_finalized,
    )


@pytest.mark.parametrize("is_finalized", [False, True])
def test_walkin_arrived_is_walkin(is_finalized: bool):
    status = attendance_status(_rsvp(has_arrived=True, is_walkin=True), _meetup(is_finalized=is_finalized))
    assert status == GuestAttendanceStatus.WALKIN


@pytest.mark.parametrize("is_finalized", [False, True])
def test_arrived_is_attended(is_finalized: bool):
    status = attendance_status(_rsvp(has_arrived=True), _meetup(is_finalized=is_finalized))
    assert status == GuestAttendanceStatus.ATTENDED


def test_arrived_beats_cancelled():
    """
    WHY: someone who withdrew on Mazmo but showed up anyway went. The
    check-in is the stronger fact.
    """
    status = attendance_status(_rsvp(has_arrived=True, cancelled_rsvp=True), _meetup(is_finalized=True))
    assert status == GuestAttendanceStatus.ATTENDED


@pytest.mark.parametrize("is_finalized", [False, True])
def test_cancelled_is_cancelled_even_if_finalized(is_finalized: bool):
    """WHY: telling the org you won't come is not a no-show."""
    status = attendance_status(_rsvp(cancelled_rsvp=True), _meetup(is_finalized=is_finalized))
    assert status == GuestAttendanceStatus.CANCELLED


def test_finalized_not_arrived_is_no_show():
    status = attendance_status(_rsvp(), _meetup(is_finalized=True))
    assert status == GuestAttendanceStatus.NO_SHOW


def test_not_finalized_is_upcoming():
    """
    WHY: until the meetup is finalized, people may still arrive (the night
    of the event), and reopening a finalized meetup turns its no-shows back
    into pending ones.
    """
    status = attendance_status(_rsvp(), _meetup(is_finalized=False))
    assert status == GuestAttendanceStatus.UPCOMING
