"""
Tests for guest meetup attendance: the rule and totals in
app.services.attendance, and GET /organizations/{org_id}/guests/{guest_id}/meetups.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.models import Guest, GuestAttendanceStatus, Meetup, MeetupRsvp, Organization, OrgRole, User
from app.services.attendance import AttendanceCounts, attendance_status, attendance_status_expr, summarize_attendance
from tests.conftest import make_guest, make_meetup, make_org, make_org_member, make_rsvp

# -- attendance_status rule -------------------------------------------------------


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
def test_attendance_status_returns_walkin_when_walkin_checked_in(is_finalized: bool):
    """Verify a walk-in who was checked in is WALKIN, finalized or not."""
    result = attendance_status(_rsvp(has_arrived=True, is_walkin=True), _meetup(is_finalized=is_finalized))

    assert result == GuestAttendanceStatus.WALKIN


@pytest.mark.parametrize("is_finalized", [False, True])
def test_attendance_status_returns_attended_when_checked_in(is_finalized: bool):
    """Verify a signed-up guest who was checked in is ATTENDED, finalized or not."""
    result = attendance_status(_rsvp(has_arrived=True), _meetup(is_finalized=is_finalized))

    assert result == GuestAttendanceStatus.ATTENDED


def test_attendance_status_returns_attended_when_cancelled_but_checked_in():
    """
    Verify a check-in beats a cancelled RSVP.

    WHY: someone who withdrew on Mazmo but showed up anyway went.
    """
    result = attendance_status(_rsvp(has_arrived=True, cancelled_rsvp=True), _meetup(is_finalized=True))

    assert result == GuestAttendanceStatus.ATTENDED


@pytest.mark.parametrize("is_finalized", [False, True])
def test_attendance_status_returns_none_when_walkin_not_checked_in(is_finalized: bool):
    """
    Verify a walk-in row without a check-in has no outcome.

    WHY: add-walkin creates the row with has_arrived=False and check-in is a
    separate step (undo-checkin also returns a walk-in to that state). They
    never signed up, so counting them as a NO_SHOW would be a false positive
    in the exact metric this feature exists for.
    """
    result = attendance_status(_rsvp(is_walkin=True), _meetup(is_finalized=is_finalized))

    assert result is None


@pytest.mark.parametrize("is_finalized", [False, True])
def test_attendance_status_returns_cancelled_when_cancelled(is_finalized: bool):
    """
    Verify a cancelled RSVP without a check-in is CANCELLED, even once finalized.

    WHY: telling the org you won't come is not a no-show.
    """
    result = attendance_status(_rsvp(cancelled_rsvp=True), _meetup(is_finalized=is_finalized))

    assert result == GuestAttendanceStatus.CANCELLED


def test_attendance_status_returns_no_show_when_finalized_without_checkin():
    """Verify a signed-up guest never checked in at a finalized meetup is NO_SHOW."""
    result = attendance_status(_rsvp(), _meetup(is_finalized=True))

    assert result == GuestAttendanceStatus.NO_SHOW


def test_attendance_status_returns_upcoming_when_not_finalized():
    """
    Verify a signed-up guest not checked in at an open meetup is UPCOMING.

    WHY: until the meetup is finalized people may still arrive (the night of
    the event), and reopening a finalized meetup turns its no-shows back
    into pending ones.
    """
    result = attendance_status(_rsvp(), _meetup(is_finalized=False))

    assert result == GuestAttendanceStatus.UPCOMING


@pytest.mark.parametrize("has_arrived", [False, True])
@pytest.mark.parametrize("is_walkin", [False, True])
@pytest.mark.parametrize("cancelled_rsvp", [False, True])
@pytest.mark.parametrize("is_finalized", [False, True])
def test_attendance_status_expr_matches_python_rule(
    session: Session,
    org: Organization,
    has_arrived: bool,
    is_walkin: bool,
    cancelled_rsvp: bool,
    is_finalized: bool,
):
    """
    Verify the SQL CASE and attendance_status() agree on every flag combination.

    WHY: stats aggregate in SQL; the rule must not drift from the Python one.
    """
    meetup = make_meetup(session, org=org, is_finalized=is_finalized)
    rsvp = make_rsvp(
        session,
        meetup=meetup,
        guest=make_guest(session),
        has_arrived=has_arrived,
        is_walkin=is_walkin,
        cancelled_rsvp=cancelled_rsvp,
    )

    sql_value = session.exec(
        select(attendance_status_expr())
        .select_from(MeetupRsvp)
        .join(Meetup, MeetupRsvp.meetup_id == Meetup.id)  # type: ignore[arg-type]
        .where(MeetupRsvp.meetup_id == meetup.id)  # type: ignore[arg-type]
    ).one()

    expected = attendance_status(rsvp, meetup)
    assert sql_value == (expected.value if expected else None)


def test_summarize_attendance_folds_walkins_into_attended():
    """
    Verify walk-ins count as attended and walkin is a subset, not a bucket.

    WHY: the guest went either way; walkin only says how they got in.
    """
    statuses = [
        GuestAttendanceStatus.ATTENDED,
        GuestAttendanceStatus.WALKIN,
        GuestAttendanceStatus.NO_SHOW,
        GuestAttendanceStatus.NO_SHOW,
        GuestAttendanceStatus.CANCELLED,
    ]

    result = summarize_attendance(statuses)

    assert result == AttendanceCounts(attended=2, walkin=1, no_show=2, cancelled=1, upcoming=0)


# -- GET /organizations/{org_id}/guests/{guest_id}/meetups ------------------------

NOW = datetime.now(UTC).replace(microsecond=0)
EMPTY_SUMMARY = {"attended": 0, "walkin": 0, "no_show": 0, "cancelled": 0, "upcoming": 0}


def _url(org_id: uuid.UUID, guest_id: uuid.UUID) -> str:
    return f"/organizations/{org_id}/guests/{guest_id}/meetups"


def _as_utc(value: str) -> datetime:
    """Parse an API timestamp, reading a naive one as UTC like the DB stores it."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        rsvp_time=NOW - timedelta(days=25),
        has_arrived=True,
        arrival_time=NOW - timedelta(days=20),
        guest_type="VENDOR",
    )
    make_rsvp(session, meetup=meetups["no_show"], guest=guest)
    make_rsvp(
        session,
        meetup=meetups["walkin"],
        guest=guest,
        rsvp_time=NOW - timedelta(days=40),
        has_arrived=True,
        arrival_time=NOW - timedelta(days=40),
        is_walkin=True,
    )
    return meetups


def test_list_guest_meetups_returns_meetups_newest_first_with_status(
    client: TestClient, admin_headers: dict, org: Organization, guest: Guest, history: dict[str, Meetup]
):
    """Verify each meetup comes back newest first with its status and RSVP data."""
    # Act
    resp = client.get(_url(org.id, guest.id), headers=admin_headers)

    # Assert
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
    assert _as_utc(attended["date"]) == NOW - timedelta(days=20)
    assert _as_utc(attended["rsvp_time"]) == NOW - timedelta(days=25)
    assert _as_utc(attended["arrival_time"]) == NOW - timedelta(days=20)
    assert _as_utc(body["meetups"][4]["rsvp_time"]) == NOW - timedelta(days=40)
    assert body["meetups"][0]["arrival_time"] is None
    assert body["meetups"][0]["guest_type"] == "NORMAL"


def test_list_guest_meetups_returns_summary_counts(
    client: TestClient, admin_headers: dict, org: Organization, guest: Guest, history: dict[str, Meetup]
):
    """Verify the summary folds walk-ins into attended and counts every other status."""
    # Act
    summary = client.get(_url(org.id, guest.id), headers=admin_headers).json()["summary"]

    # Assert
    assert summary == {"attended": 2, "walkin": 1, "no_show": 1, "cancelled": 1, "upcoming": 1}


def test_list_guest_meetups_excludes_walkin_never_checked_in(
    session: Session, client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    """
    Verify a walk-in added at the door but never checked in is left out.

    WHY: they never signed up and never got in; listing them as a no-show
    would flag someone who did nothing wrong.
    """
    # Arrange
    make_rsvp(session, meetup=_meetup_in(session, org, "Door", 3, is_finalized=True), guest=guest, is_walkin=True)

    # Act
    body = client.get(_url(org.id, guest.id), headers=admin_headers).json()

    # Assert
    assert body == {"total": 0, "summary": EMPTY_SUMMARY, "meetups": []}


def test_list_guest_meetups_excludes_other_orgs(
    session: Session, client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    """
    Verify RSVPs of the same guest in another organization never appear.

    WHY: guests are global but meetups are per org; one org must not see
    another org's attendance.
    """
    # Arrange
    other = make_org(session, name="Other Org", slug="other-org")
    make_rsvp(session, meetup=_meetup_in(session, other, "Elsewhere", 3, is_finalized=True), guest=guest)
    make_rsvp(session, meetup=_meetup_in(session, org, "Here", 3, is_finalized=True), guest=guest)

    # Act
    body = client.get(_url(org.id, guest.id), headers=admin_headers).json()

    # Assert
    assert [m["name"] for m in body["meetups"]] == ["Here"]
    assert body["total"] == 1
    assert body["summary"]["no_show"] == 1


def test_list_guest_meetups_excludes_other_guests(
    session: Session, client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    """Verify another guest's RSVP to the same org's meetup never appears."""
    # Arrange
    meetup = _meetup_in(session, org, "Shared", 3, is_finalized=True)
    make_rsvp(session, meetup=meetup, guest=make_guest(session, mazmo_user_id=2, mazmo_handle="other"))

    # Act
    body = client.get(_url(org.id, guest.id), headers=admin_headers).json()

    # Assert
    assert body["total"] == 0


def test_list_guest_meetups_returns_empty_when_no_rsvps(
    client: TestClient, admin_headers: dict, org: Organization, guest: Guest
):
    """Verify a known guest with no RSVPs in the org gets 200 and an empty list, not 404."""
    # Act
    resp = client.get(_url(org.id, guest.id), headers=admin_headers)

    # Assert
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"total": 0, "summary": EMPTY_SUMMARY, "meetups": []}


def test_list_guest_meetups_returns_404_for_unknown_guest(client: TestClient, admin_headers: dict, org: Organization):
    """Verify an unknown guest id returns 404 with an actionable message."""
    # Arrange
    guest_id = uuid.uuid4()

    # Act
    resp = client.get(_url(org.id, guest_id), headers=admin_headers)

    # Assert
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"].startswith(f"Cannot list meetups: guest id={guest_id} does not exist")


def test_list_guest_meetups_returns_404_for_unknown_org(client: TestClient, admin_headers: dict, guest: Guest):
    """Verify an unknown organization returns 404 (a site admin bypasses membership)."""
    # Arrange
    org_id = uuid.uuid4()

    # Act
    resp = client.get(_url(org_id, guest.id), headers=admin_headers)

    # Assert
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == f"Organization {org_id} not found."


def test_list_guest_meetups_returns_401_without_auth(client: TestClient, org: Organization, guest: Guest):
    """Verify the endpoint requires authentication."""
    # Act
    resp = client.get(_url(org.id, guest.id))

    # Assert
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_guest_meetups_returns_403_for_non_member(
    client: TestClient, staff_headers: dict, org: Organization, guest: Guest
):
    """Verify an approved user who is not a member of the org gets 403."""
    # Act
    resp = client.get(_url(org.id, guest.id), headers=staff_headers)

    # Assert
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_list_guest_meetups_returns_200_for_staff_member(
    session: Session,
    client: TestClient,
    staff_headers: dict,
    staff_user: User,
    org: Organization,
    guest: Guest,
    history: dict[str, Meetup],
):
    """
    Verify a plain STAFF member can read it, not only org admins.

    WHY: the door staff open guest profiles from the meetup page.
    """
    # Arrange
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    # Act
    resp = client.get(_url(org.id, guest.id), headers=staff_headers)

    # Assert
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 5
