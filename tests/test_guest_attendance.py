"""
Tests for guest meetup attendance: the attendance_status rule and
GET /organizations/{org_id}/guests/{guest_id}/meetups.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import Guest, GuestAttendanceStatus, Meetup, MeetupRsvp, Organization, OrgRole, User
from app.services.attendance import attendance_status
from tests.conftest import make_guest, make_meetup, make_org, make_org_member, make_rsvp

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
    result = attendance_status(_rsvp(has_arrived=True, is_walkin=True), _meetup(is_finalized=is_finalized))
    assert result == GuestAttendanceStatus.WALKIN


@pytest.mark.parametrize("is_finalized", [False, True])
def test_arrived_is_attended(is_finalized: bool):
    result = attendance_status(_rsvp(has_arrived=True), _meetup(is_finalized=is_finalized))
    assert result == GuestAttendanceStatus.ATTENDED


def test_arrived_beats_cancelled():
    """
    WHY: someone who withdrew on Mazmo but showed up anyway went. The
    check-in is the stronger fact.
    """
    result = attendance_status(_rsvp(has_arrived=True, cancelled_rsvp=True), _meetup(is_finalized=True))
    assert result == GuestAttendanceStatus.ATTENDED


@pytest.mark.parametrize("is_finalized", [False, True])
def test_cancelled_is_cancelled_even_if_finalized(is_finalized: bool):
    """WHY: telling the org you won't come is not a no-show."""
    result = attendance_status(_rsvp(cancelled_rsvp=True), _meetup(is_finalized=is_finalized))
    assert result == GuestAttendanceStatus.CANCELLED


def test_finalized_not_arrived_is_no_show():
    result = attendance_status(_rsvp(), _meetup(is_finalized=True))
    assert result == GuestAttendanceStatus.NO_SHOW


def test_not_finalized_is_upcoming():
    """
    WHY: until the meetup is finalized, people may still arrive (the night
    of the event), and reopening a finalized meetup turns its no-shows back
    into pending ones.
    """
    result = attendance_status(_rsvp(), _meetup(is_finalized=False))
    assert result == GuestAttendanceStatus.UPCOMING


# ── GET /organizations/{org_id}/guests/{guest_id}/meetups ─────────────────────

NOW = datetime.now(UTC)


def _url(org_id: uuid.UUID, guest_id: uuid.UUID) -> str:
    return f"/organizations/{org_id}/guests/{guest_id}/meetups"


def _meetup_in(session: Session, org: Organization, name: str, days_ago: int, *, is_finalized: bool) -> Meetup:
    return make_meetup(
        session,
        org=org,
        name=name,
        mazmo_meetup_url=f"https://mazmo.net/test/{uuid.uuid4()}",
        date=NOW - timedelta(days=days_ago),
        is_finalized=is_finalized,
    )


@pytest.fixture()
def guest(session: Session) -> Guest:
    return make_guest(session)


@pytest.fixture()
def history(session: Session, org: Organization, guest: Guest) -> dict[str, Meetup]:
    """One meetup per status, created out of date order to prove the sort."""
    meetups = {
        "cancelled": _meetup_in(session, org, "Cancelled", 30, is_finalized=True),
        "upcoming": _meetup_in(session, org, "Upcoming", -5, is_finalized=False),
        "attended": _meetup_in(session, org, "Attended", 20, is_finalized=True),
        "no_show": _meetup_in(session, org, "No show", 10, is_finalized=True),
        "walkin": _meetup_in(session, org, "Walkin", 40, is_finalized=True),
    }
    make_rsvp(session, meetup=meetups["cancelled"], guest=guest, cancelled_rsvp=True)
    make_rsvp(session, meetup=meetups["upcoming"], guest=guest)
    make_rsvp(
        session,
        meetup=meetups["attended"],
        guest=guest,
        has_arrived=True,
        arrival_time=NOW - timedelta(days=20),
        guest_type="VENDOR",
    )
    make_rsvp(session, meetup=meetups["no_show"], guest=guest)
    make_rsvp(session, meetup=meetups["walkin"], guest=guest, has_arrived=True, is_walkin=True)
    return meetups


def test_lists_meetups_newest_first_with_status(
    client: TestClient, admin_headers: dict, org: Organization, guest: Guest, history: dict[str, Meetup]
):
    resp = client.get(_url(org.id, guest.id), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] == 5
    assert [(m["name"], m["status"]) for m in body["meetups"]] == [
        ("Upcoming", "UPCOMING"),
        ("No show", "NO_SHOW"),
        ("Attended", "ATTENDED"),
        ("Cancelled", "CANCELLED"),
        ("Walkin", "WALKIN"),
    ]
    attended = body["meetups"][2]
    assert attended["meetup_id"] == str(history["attended"].id)
    assert attended["guest_type"] == "VENDOR"
    assert attended["arrival_time"] is not None
    assert body["meetups"][0]["arrival_time"] is None


def test_summary_counts(client: TestClient, admin_headers: dict, org: Organization, guest: Guest, history: dict):
    """WHY: walk-ins count as attended, and walkin is a subset, not a sixth bucket."""
    summary = client.get(_url(org.id, guest.id), headers=admin_headers).json()["summary"]

    assert summary == {"attended": 2, "walkin": 1, "no_show": 1, "cancelled": 1, "upcoming": 1}


def test_other_org_rsvps_excluded(
    session: Session, client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    """WHY: guests are global but meetups are per org; another org's attendance must never leak."""
    other = make_org(session, name="Other Org", slug="other-org")
    make_rsvp(session, meetup=_meetup_in(session, other, "Elsewhere", 3, is_finalized=True), guest=guest)
    make_rsvp(session, meetup=_meetup_in(session, org, "Here", 3, is_finalized=True), guest=guest)

    body = client.get(_url(org.id, guest.id), headers=admin_headers).json()

    assert [m["name"] for m in body["meetups"]] == ["Here"]
    assert body["total"] == 1


def test_other_guests_rsvps_excluded(
    session: Session, client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    meetup = _meetup_in(session, org, "Shared", 3, is_finalized=True)
    make_rsvp(session, meetup=meetup, guest=make_guest(session, mazmo_user_id=2, mazmo_handle="other"))

    body = client.get(_url(org.id, guest.id), headers=admin_headers).json()

    assert body["total"] == 0


def test_guest_without_rsvps_returns_empty(client: TestClient, admin_headers: dict, org: Organization, guest: Guest):
    resp = client.get(_url(org.id, guest.id), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "total": 0,
        "summary": {"attended": 0, "walkin": 0, "no_show": 0, "cancelled": 0, "upcoming": 0},
        "meetups": [],
    }


def test_unknown_guest_404(client: TestClient, admin_headers: dict, org: Organization):
    resp = client.get(_url(org.id, uuid.uuid4()), headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "does not exist" in resp.json()["detail"]


def test_unknown_org_404(client: TestClient, admin_headers: dict, guest: Guest):
    resp = client.get(_url(uuid.uuid4(), guest.id), headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_non_member_403(client: TestClient, staff_headers: dict, org: Organization, guest: Guest):
    resp = client.get(_url(org.id, guest.id), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_staff_member_allowed(
    session: Session,
    client: TestClient,
    staff_headers: dict,
    staff_user: User,
    org: Organization,
    guest: Guest,
    history: dict,
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_url(org.id, guest.id), headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 5
