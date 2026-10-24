"""Tests for the org attendance statistics service."""

from datetime import UTC, date, datetime
from itertools import count

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import GuestType, Meetup, Organization, OrgRole, StatsComparisonKind
from app.services.stats.attendance import (
    MeetupAttendanceRow,
    attendance_stats,
    meetup_attendance_rows,
    summarize_rows,
)
from app.services.stats.scope import StatsPeriod
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

ALL_TIME = StatsPeriod(None, None)
RSVP_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_ids = count(1000)


def _finalized_meetup(session: Session, org: Organization, when: datetime) -> Meetup:
    n = next(_ids)
    return make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=when,
        is_finalized=True,
    )


def _rsvp(session: Session, meetup: Meetup, **kwargs):
    n = next(_ids)
    guest = make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=f"Guest {n}")
    return make_rsvp(session, meetup=meetup, guest=guest, rsvp_time=RSVP_TIME, **kwargs)


def _row(**kwargs) -> MeetupAttendanceRow:
    base = dict(
        meetup_id=Meetup().id, name="m", date=RSVP_TIME, signed_up=0, cancelled=0, attended=0, walkin=0, no_show=0
    )
    return MeetupAttendanceRow(**{**base, **kwargs})  # type: ignore[arg-type]


# -- meetup_attendance_rows --


def test_meetup_attendance_rows_counts_each_status(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    for _ in range(3):
        _rsvp(session, meetup, has_arrived=True)
    _rsvp(session, meetup, cancelled_rsvp=True)
    for _ in range(2):
        _rsvp(session, meetup)
    _rsvp(session, meetup, has_arrived=True, is_walkin=True)
    _rsvp(session, meetup, is_walkin=True)

    [row] = meetup_attendance_rows(session, org.id, ALL_TIME)

    assert (row.signed_up, row.cancelled, row.attended, row.walkin, row.no_show) == (6, 1, 4, 1, 2)
    assert row.meetup_id == meetup.id
    assert row.name == meetup.name


def test_meetup_attendance_rows_counts_cancelled_but_checked_in_as_attended(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _rsvp(session, meetup, cancelled_rsvp=True, has_arrived=True)

    [row] = meetup_attendance_rows(session, org.id, ALL_TIME)

    assert (row.attended, row.cancelled) == (1, 0)


def test_meetup_attendance_rows_excludes_staff_and_vendor(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.STAFF.value)
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.VENDOR.value)
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.INVITED.value)

    [row] = meetup_attendance_rows(session, org.id, ALL_TIME)

    assert row.attended == 1


def test_meetup_attendance_rows_keeps_meetup_with_no_audience(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.STAFF.value)

    rows = meetup_attendance_rows(session, org.id, ALL_TIME)

    assert [(r.meetup_id, r.signed_up, r.cancelled, r.attended, r.walkin, r.no_show) for r in rows] == [
        (meetup.id, 0, 0, 0, 0, 0)
    ]


def test_meetup_attendance_rows_orders_by_date_ascending(session: Session):
    org = make_org(session)
    late = _finalized_meetup(session, org, datetime(2026, 4, 12, 22, tzinfo=UTC))
    early = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    rows = meetup_attendance_rows(session, org.id, ALL_TIME)

    assert [r.meetup_id for r in rows] == [early.id, late.id]


# -- summarize_rows --


def test_summarize_rows_rates_and_averages():
    totals = summarize_rows(
        [
            _row(signed_up=10, cancelled=2, attended=7, walkin=1, no_show=2),
            _row(signed_up=6, cancelled=0, attended=5, walkin=0, no_show=1),
        ]
    )

    assert (totals.events, totals.signed_up, totals.cancelled) == (2, 16, 2)
    assert (totals.attended, totals.walkin, totals.no_show) == (12, 1, 3)
    assert totals.cancellation_rate == 0.125
    assert totals.no_show_rate == 0.2143
    assert totals.avg_signed_up_per_event == 8.0
    assert totals.avg_attended_per_event == 6.0


def test_summarize_rows_returns_none_rates_when_empty():
    totals = summarize_rows([])

    assert (totals.events, totals.signed_up, totals.cancelled) == (0, 0, 0)
    assert (totals.attended, totals.walkin, totals.no_show) == (0, 0, 0)
    assert totals.cancellation_rate is None
    assert totals.no_show_rate is None
    assert totals.avg_signed_up_per_event is None
    assert totals.avg_attended_per_event is None


def test_summarize_rows_returns_none_no_show_rate_when_everyone_cancelled():
    totals = summarize_rows([_row(signed_up=3, cancelled=3)])

    assert totals.cancellation_rate == 1.0
    assert totals.no_show_rate is None


# -- attendance_stats --


def test_attendance_stats_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    april = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _finalized_meetup(session, org, datetime(2026, 3, 25, 22, tzinfo=UTC))

    stats = attendance_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), None)

    assert [r.meetup_id for r in stats.meetups] == [april.id]
    assert stats.totals.events == 1
    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison is not None
    assert stats.comparison.events == 1


def test_attendance_stats_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    stats = attendance_stats(session, org.id, ALL_TIME, None)

    assert stats.comparison is None
    assert stats.comparison_kind is None


def test_attendance_stats_single_meetup_compares_with_period(session: Session):
    org = make_org(session)
    first = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _finalized_meetup(session, org, datetime(2026, 4, 8, 22, tzinfo=UTC))

    stats = attendance_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), first.id)

    assert [r.meetup_id for r in stats.meetups] == [first.id]
    assert stats.totals.events == 1
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison is not None
    assert stats.comparison.events == 2


def test_attendance_stats_single_meetup_ignores_period_bounds(session: Session):
    org = make_org(session)
    outside = _finalized_meetup(session, org, datetime(2026, 1, 5, 22, tzinfo=UTC))

    stats = attendance_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), outside.id)

    assert [r.meetup_id for r in stats.meetups] == [outside.id]
    assert stats.comparison is not None
    assert stats.comparison.events == 0


# -- GET /organizations/{org_id}/stats/attendance --


def _stats_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/attendance"


def test_attendance_stats_returns_401_without_auth(client: TestClient, org: Organization):
    resp = client.get(_stats_url(org))

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_attendance_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_stats_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_attendance_stats_returns_403_for_admin_of_other_org(client: TestClient, session: Session, org: Organization):
    other_org = make_org(session, name="Other Org", slug="other-org")
    other_admin = make_user(session, username="other_admin")
    make_org_member(session, org=other_org, user=other_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "other_admin", "a-very-secure-passphrase")

    resp = client.get(_stats_url(org), headers=headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_attendance_stats_returns_200_with_exact_totals(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    for _ in range(3):
        _rsvp(session, meetup, has_arrived=True)
    _rsvp(session, meetup, cancelled_rsvp=True)
    for _ in range(2):
        _rsvp(session, meetup)
    _rsvp(session, meetup, has_arrived=True, is_walkin=True)
    _rsvp(session, meetup, is_walkin=True)

    resp = client.get(_stats_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["meetups"] == [
        {
            "meetup_id": str(meetup.id),
            "name": meetup.name,
            "date": "2026-04-05T22:00:00",
            "signed_up": 6,
            "cancelled": 1,
            "attended": 4,
            "walkin": 1,
            "no_show": 2,
        }
    ]
    assert body["totals"] == {
        "events": 1,
        "signed_up": 6,
        "cancelled": 1,
        "attended": 4,
        "walkin": 1,
        "no_show": 2,
        "cancellation_rate": 0.1667,
        "no_show_rate": 0.4,
        "avg_signed_up_per_event": 6.0,
        "avg_attended_per_event": 4.0,
    }
    assert body["comparison_kind"] is None
    assert body["comparison"] is None


def test_attendance_stats_returns_200_for_site_admin(
    client: TestClient, session: Session, org: Organization, site_admin_headers: dict
):
    _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    resp = client.get(_stats_url(org), headers=site_admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["totals"]["events"] == 1


def test_attendance_stats_returns_422_when_from_not_before_to(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_stats_url(org), params={"from": "2026-05-01", "to": "2026-05-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["detail"] == (
        "Cannot compute stats: 'from' (2026-05-01) must be before 'to' (2026-05-01). Pick a later 'to' date."
    )


def test_attendance_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_stats_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == (
        f"Cannot compute stats: meetup {meetup.id} is not a finalized meetup of organization {org.id}. "
        "List the org's meetups to find a finalized one."
    )


def test_attendance_stats_returns_404_for_meetup_of_other_org(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    other_org = make_org(session, name="Other Org", slug="other-org")
    foreign = _finalized_meetup(session, other_org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    resp = client.get(_stats_url(org), params={"meetup_id": str(foreign.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_attendance_stats_returns_empty_totals_for_empty_period(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_stats_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["meetups"] == []
    assert body["totals"]["events"] == 0
    assert body["totals"]["no_show_rate"] is None
