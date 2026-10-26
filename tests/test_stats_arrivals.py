"""Tests for the org arrival statistics service."""

from datetime import UTC, date, datetime, timedelta
from itertools import count

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import GuestType, Meetup, Organization, OrgRole, StatsComparisonKind, StatsLeadTimeBucket
from app.services.stats.arrivals import (
    Arrival,
    ArrivalBucket,
    LeadTimeRow,
    arrival_curve,
    arrival_stats,
    lead_time_bucket,
    summarize_arrivals,
)
from app.services.stats.scope import STATS_TZ, StatsPeriod
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

ALL_TIME = StatsPeriod(None, None)
OFFSETS = [-10, 0, 5, 14, 15, 44, 90]
ELEVEN_PM = 23 * 60  # a 23:00 start, in minutes from local midnight


def _at(start_clock: int, offsets: list[int]) -> list[Arrival]:
    """Arrivals at these offsets from a start at start_clock minutes past local midnight."""
    return [Arrival(offset=offset, clock=start_clock + offset) for offset in offsets]


_ids = count(2000)


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


def _rsvp(session: Session, meetup: Meetup, rsvp_time: datetime | None = None, **kwargs):
    n = next(_ids)
    guest = make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=f"Guest {n}")
    return make_rsvp(
        session, meetup=meetup, guest=guest, rsvp_time=rsvp_time or meetup.date - timedelta(days=1), **kwargs
    )


def _arrived(session: Session, meetup: Meetup, minutes: int, **kwargs):
    return _rsvp(session, meetup, has_arrived=True, arrival_time=meetup.date + timedelta(minutes=minutes), **kwargs)


# -- arrival_curve / summarize_arrivals --


def test_arrival_curve_buckets_relative_minutes():
    curve = arrival_curve(OFFSETS)

    assert [(b.start_minute, b.arrivals) for b in curve] == [
        (-15, 1),
        (0, 3),
        (15, 1),
        (30, 1),
        (45, 0),
        (60, 0),
        (75, 0),
        (90, 1),
    ]
    assert all(b.local_time is None for b in curve)


def test_arrival_curve_empty():
    assert arrival_curve([]) == []


def test_arrival_curve_clock_buckets_floor_to_the_quarter_hour():
    start = datetime(2026, 4, 5, 22, 10, tzinfo=STATS_TZ)

    curve = arrival_curve([0, 4, 5], local_start=start)

    assert curve == [ArrivalBucket(-10, "22:00", 2), ArrivalBucket(5, "22:15", 1)]


def test_summarize_arrivals_mean_p90_peak_and_first_hour():
    summary = summarize_arrivals(_at(ELEVEN_PM, OFFSETS), arrival_curve(OFFSETS))

    assert summary.arrivals == 7
    assert summary.mean_minutes == 23  # 158 / 7 = 22.6
    assert summary.p90_minutes == 90
    assert summary.first_hour_share == 0.8571
    assert summary.peak == ArrivalBucket(0, None, 3)
    assert summary.mean_local_time == "23:23"
    assert summary.p90_local_time == "00:30"
    assert summary.peak_local_time == "23:00"


def test_summarize_arrivals_rounds_the_mean_half_up():
    summary = summarize_arrivals(_at(ELEVEN_PM, [0, 1]), arrival_curve([0, 1]))

    assert (summary.mean_minutes, summary.mean_local_time) == (1, "23:01")


def test_summarize_arrivals_peak_quarter_is_the_earliest_on_ties():
    summary = summarize_arrivals(_at(ELEVEN_PM, [20, 5]), arrival_curve([5, 20]))

    assert summary.peak_local_time == "23:00"


def test_summarize_arrivals_clock_wraps_before_and_after_midnight():
    summary = summarize_arrivals(_at(30, [-60, -60, 100]), arrival_curve([-60, -60, 100]))

    assert summary.peak_local_time == "23:30"
    assert summary.mean_local_time == "00:23"
    assert summary.p90_local_time == "02:10"


def test_summarize_arrivals_empty():
    summary = summarize_arrivals([], [])

    assert summary.arrivals == 0
    assert summary.excluded_arrivals == 0
    assert summary.peak is None
    assert summary.peak_local_time is None
    assert summary.mean_minutes is None
    assert summary.p90_minutes is None
    assert summary.mean_local_time is None
    assert summary.p90_local_time is None
    assert summary.first_hour_share is None


def test_lead_time_bucket_edges():
    expected = {
        0: StatsLeadTimeBucket.SAME_DAY,
        1: StatsLeadTimeBucket.ONE_DAY,
        2: StatsLeadTimeBucket.TWO_THREE,
        3: StatsLeadTimeBucket.TWO_THREE,
        4: StatsLeadTimeBucket.FOUR_SEVEN,
        7: StatsLeadTimeBucket.FOUR_SEVEN,
        8: StatsLeadTimeBucket.EIGHT_FOURTEEN,
        14: StatsLeadTimeBucket.EIGHT_FOURTEEN,
        15: StatsLeadTimeBucket.FIFTEEN_PLUS,
        -2: StatsLeadTimeBucket.SAME_DAY,
    }

    assert {days: lead_time_bucket(days) for days in expected} == expected


# -- arrival_stats --


def test_arrival_stats_one_event_uses_clock_time_across_midnight(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 23, 0, tzinfo=STATS_TZ).astimezone(UTC))
    _arrived(session, meetup, 10)
    _arrived(session, meetup, 95)

    stats = arrival_stats(session, org.id, ALL_TIME, meetup.id)

    assert [(b.local_time, b.start_minute, b.arrivals) for b in stats.curve] == [
        ("23:00", 0, 1),
        ("23:15", 15, 0),
        ("23:30", 30, 0),
        ("23:45", 45, 0),
        ("00:00", 60, 0),
        ("00:15", 75, 0),
        ("00:30", 90, 1),
    ]
    assert stats.summary.mean_local_time == "23:53"
    assert stats.summary.p90_local_time == "00:35"
    assert stats.summary.peak_local_time == "23:00"


def test_arrival_stats_excludes_staff_and_unarrived(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, meetup, 5, guest_type=GuestType.STAFF.value)
    _rsvp(session, meetup)
    _arrived(session, meetup, 7, guest_type=GuestType.INVITED.value)

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.arrivals == 1
    assert stats.summary.mean_minutes == 7


def test_arrival_stats_counts_walkins_as_arrivals(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, meetup, 20, is_walkin=True)

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.arrivals == 1


def test_arrival_stats_without_arrivals_is_empty(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _rsvp(session, meetup)

    for meetup_id in (None, meetup.id):
        stats = arrival_stats(session, org.id, ALL_TIME, meetup_id)

        assert stats.curve == []
        assert stats.summary.arrivals == 0
        assert stats.summary.peak is None
        assert stats.summary.first_hour_share is None


def test_arrival_stats_excludes_arrivals_outside_the_offset_window(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    for minutes in (-121, -120, 719, 720, 900):
        _arrived(session, meetup, minutes)

    for meetup_id in (None, meetup.id):
        stats = arrival_stats(session, org.id, ALL_TIME, meetup_id)

        assert stats.summary.arrivals == 2
        assert stats.summary.excluded_arrivals == 3
        assert (stats.curve[0].start_minute, stats.curve[-1].start_minute) == (-120, 705)
        assert sum(b.arrivals for b in stats.curve) == 2
        assert stats.summary.mean_minutes == 300
        assert stats.summary.mean_local_time == "00:00"
        assert stats.summary.p90_minutes == 719
        assert stats.summary.first_hour_share == 0.5


def test_arrival_stats_excludes_outliers_from_the_comparison(session: Session):
    org = make_org(session)
    april = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, april, 10)
    march = _finalized_meetup(session, org, datetime(2026, 3, 25, 22, tzinfo=UTC))
    _arrived(session, march, 30)
    _arrived(session, march, 900)

    stats = arrival_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), None)

    assert stats.comparison is not None
    assert stats.comparison.arrivals == 1
    assert stats.comparison.excluded_arrivals == 1
    assert stats.comparison.mean_minutes == 30


def test_arrival_stats_only_excluded_arrivals_leaves_the_summary_empty(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, meetup, 900)

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.curve == []
    assert stats.summary.arrivals == 0
    assert stats.summary.excluded_arrivals == 1
    assert stats.summary.peak is None


def test_arrival_stats_truncates_the_start_to_the_minute(session: Session):
    org = make_org(session)
    start = datetime(2026, 4, 5, 22, 50, 30, tzinfo=STATS_TZ).astimezone(UTC)
    meetup = _finalized_meetup(session, org, start)
    arrival = datetime(2026, 4, 5, 23, 0, 10, tzinfo=STATS_TZ).astimezone(UTC)
    _rsvp(session, meetup, has_arrived=True, arrival_time=arrival)

    stats = arrival_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.curve == [ArrivalBucket(10, "23:00", 1)]
    assert stats.summary.mean_minutes == 10
    assert stats.summary.mean_local_time == "23:00"


def test_arrival_stats_lead_time_counts_and_rates(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 20, 22, tzinfo=UTC))

    def made(days_before: int) -> datetime:
        return meetup.date - timedelta(days=days_before)

    _arrived(session, meetup, 0, rsvp_time=made(0))
    _arrived(session, meetup, 0, rsvp_time=meetup.date + timedelta(hours=1))  # after the start, not a walk-in
    _arrived(session, meetup, 0, rsvp_time=made(0), is_walkin=True)
    _arrived(session, meetup, 0, rsvp_time=made(1))
    _arrived(session, meetup, 0, rsvp_time=made(5))
    _rsvp(session, meetup, made(5))  # no-show
    _rsvp(session, meetup, made(5), cancelled_rsvp=True)
    _arrived(session, meetup, 0, rsvp_time=made(20))

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.lead_time == [
        LeadTimeRow(StatsLeadTimeBucket.SAME_DAY, 2, 0, 0, 0.0),
        LeadTimeRow(StatsLeadTimeBucket.ONE_DAY, 1, 0, 0, 0.0),
        LeadTimeRow(StatsLeadTimeBucket.TWO_THREE, 0, 0, 0, None),
        LeadTimeRow(StatsLeadTimeBucket.FOUR_SEVEN, 3, 1, 1, 0.5),
        LeadTimeRow(StatsLeadTimeBucket.EIGHT_FOURTEEN, 0, 0, 0, None),
        LeadTimeRow(StatsLeadTimeBucket.FIFTEEN_PLUS, 1, 0, 0, 0.0),
    ]


def test_arrival_stats_lead_time_uses_local_dates(session: Session):
    org = make_org(session)
    # 01:00 local on Apr 10 is still Apr 9 in UTC; an RSVP at 23:00 local on Apr 9 is one local day before.
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 10, 1, 0, tzinfo=STATS_TZ).astimezone(UTC))
    _arrived(session, meetup, 0, rsvp_time=datetime(2026, 4, 9, 23, 0, tzinfo=STATS_TZ).astimezone(UTC))

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert [r.signed_up for r in stats.lead_time] == [0, 1, 0, 0, 0, 0]


def test_arrival_stats_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    april = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, april, 10)
    march = _finalized_meetup(session, org, datetime(2026, 3, 25, 22, tzinfo=UTC))
    _arrived(session, march, 30)
    _arrived(session, march, 40)

    stats = arrival_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), None)

    assert stats.summary.arrivals == 1
    assert stats.summary.mean_local_time == "19:10"
    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison is not None
    assert stats.comparison.arrivals == 2
    assert stats.comparison.mean_minutes == 35
    assert stats.comparison.mean_local_time == "19:35"


def test_arrival_stats_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.comparison is None
    assert stats.comparison_kind is None


def test_arrival_stats_single_meetup_compares_with_period(session: Session):
    org = make_org(session)
    first = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, first, 10)
    second = _finalized_meetup(session, org, datetime(2026, 4, 8, 22, tzinfo=UTC))
    _arrived(session, second, 50)

    stats = arrival_stats(session, org.id, StatsPeriod(date(2026, 4, 1), date(2026, 4, 11)), first.id)

    assert stats.summary.arrivals == 1
    assert stats.summary.mean_local_time == "19:10"
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison is not None
    assert stats.comparison.arrivals == 2
    assert stats.comparison.mean_local_time == "19:30"


def test_arrival_stats_clock_puts_a_meetup_after_midnight_on_the_night_before(session: Session):
    org = make_org(session)
    late = _finalized_meetup(session, org, datetime(2026, 4, 5, 23, 0, tzinfo=STATS_TZ).astimezone(UTC))
    _arrived(session, late, 10)  # 23:10
    after_midnight = _finalized_meetup(session, org, datetime(2026, 4, 13, 0, 30, tzinfo=STATS_TZ).astimezone(UTC))
    _arrived(session, after_midnight, 10)  # 00:40

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.mean_local_time == "23:55"
    assert stats.summary.p90_local_time == "00:40"


def test_arrival_stats_aggregate_clock_counts_from_each_meetups_own_date(session: Session):
    org = make_org(session)
    # 23:50 and 00:10 of the same night average to midnight, not to noon.
    for day, minutes in ((5, 50), (12, 70)):
        meetup = _finalized_meetup(session, org, datetime(2026, 4, day, 23, 0, tzinfo=STATS_TZ).astimezone(UTC))
        _arrived(session, meetup, minutes)

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.mean_local_time == "00:00"
    assert stats.summary.p90_local_time == "00:10"


# -- GET /organizations/{org_id}/stats/arrivals --


def _arrivals_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/arrivals"


def _lead_time(**one_day: int | float | None) -> list[dict]:
    """Six lead-time rows, all zero except ONE_DAY with the given fields."""
    rows = [
        {"bucket": bucket.value, "signed_up": 0, "cancelled": 0, "no_show": 0, "no_show_rate": None}
        for bucket in StatsLeadTimeBucket
    ]
    rows[1].update(one_day)
    return rows


def test_arrival_stats_returns_401_without_auth(client: TestClient, org: Organization):
    resp = client.get(_arrivals_url(org))

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_arrival_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_arrivals_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_arrival_stats_returns_403_before_checking_the_period_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_arrivals_url(org), params={"from": "2026-05-01", "to": "2026-05-01"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_arrival_stats_returns_403_before_checking_the_meetup_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_arrivals_url(org), params={"meetup_id": str(meetup.id)}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_arrival_stats_returns_200_for_org_admin(client: TestClient, session: Session, org: Organization):
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, meetup, 5)
    org_admin = make_user(session, username="org_admin")
    make_org_member(session, org=org, user=org_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "org_admin", "a-very-secure-passphrase")

    resp = client.get(_arrivals_url(org), headers=headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["summary"]["arrivals"] == 1


def test_arrival_stats_returns_200_with_exact_aggregate_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    for minutes in (-10, 5, 20):
        _arrived(session, meetup, minutes)
    _arrived(session, meetup, 30, is_walkin=True)
    _rsvp(session, meetup)
    _rsvp(session, meetup, cancelled_rsvp=True)

    resp = client.get(_arrivals_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "curve": [
            {"start_minute": -15, "local_time": None, "arrivals": 1},
            {"start_minute": 0, "local_time": None, "arrivals": 1},
            {"start_minute": 15, "local_time": None, "arrivals": 1},
            {"start_minute": 30, "local_time": None, "arrivals": 1},
        ],
        "summary": {
            "arrivals": 4,
            "excluded_arrivals": 0,
            "peak": {"start_minute": -15, "local_time": None, "arrivals": 1},
            "peak_local_time": "18:45",
            "mean_minutes": 11,
            "p90_minutes": 30,
            "mean_local_time": "19:11",
            "p90_local_time": "19:30",
            "first_hour_share": 1.0,
        },
        "lead_time": _lead_time(signed_up=5, cancelled=1, no_show=1, no_show_rate=0.25),
        "comparison_kind": None,
        "comparison": None,
    }


def test_arrival_stats_returns_200_for_one_meetup_in_clock_time(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    selected = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, selected, 10)
    other = _finalized_meetup(session, org, datetime(2026, 4, 8, 22, tzinfo=UTC))
    _arrived(session, other, 50)

    resp = client.get(
        _arrivals_url(org),
        params={"from": "2026-04-01", "to": "2026-04-11", "meetup_id": str(selected.id)},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "curve": [{"start_minute": 0, "local_time": "19:00", "arrivals": 1}],
        "summary": {
            "arrivals": 1,
            "excluded_arrivals": 0,
            "peak": {"start_minute": 0, "local_time": "19:00", "arrivals": 1},
            "peak_local_time": "19:00",
            "mean_minutes": 10,
            "p90_minutes": 10,
            "mean_local_time": "19:10",
            "p90_local_time": "19:10",
            "first_hour_share": 1.0,
        },
        "lead_time": _lead_time(signed_up=1, no_show_rate=0.0),
        "comparison_kind": "PERIOD_AVERAGE",
        "comparison": {
            "arrivals": 2,
            "excluded_arrivals": 0,
            "peak": {"start_minute": 0, "local_time": None, "arrivals": 1},
            "peak_local_time": "19:00",
            "mean_minutes": 30,
            "p90_minutes": 50,
            "mean_local_time": "19:30",
            "p90_local_time": "19:50",
            "first_hour_share": 1.0,
        },
    }


def test_arrival_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_arrivals_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_arrival_stats_returns_422_when_from_not_before_to(client: TestClient, org: Organization, admin_headers: dict):
    resp = client.get(_arrivals_url(org), params={"from": "2026-05-01", "to": "2026-04-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_arrival_stats_returns_empty_body_for_empty_period(client: TestClient, org: Organization, admin_headers: dict):
    resp = client.get(_arrivals_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "curve": [],
        "summary": {
            "arrivals": 0,
            "excluded_arrivals": 0,
            "peak": None,
            "peak_local_time": None,
            "mean_minutes": None,
            "p90_minutes": None,
            "mean_local_time": None,
            "p90_local_time": None,
            "first_hour_share": None,
        },
        "lead_time": _lead_time(),
        "comparison_kind": None,
        "comparison": None,
    }


def test_arrival_stats_returns_200_with_exact_previous_period_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    april = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, april, 10)
    march = _finalized_meetup(session, org, datetime(2026, 3, 25, 22, tzinfo=UTC))
    _arrived(session, march, 30)
    _arrived(session, march, 900)

    resp = client.get(_arrivals_url(org), params={"from": "2026-04-01", "to": "2026-04-11"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "curve": [{"start_minute": 0, "local_time": None, "arrivals": 1}],
        "summary": {
            "arrivals": 1,
            "excluded_arrivals": 0,
            "peak": {"start_minute": 0, "local_time": None, "arrivals": 1},
            "peak_local_time": "19:00",
            "mean_minutes": 10,
            "p90_minutes": 10,
            "mean_local_time": "19:10",
            "p90_local_time": "19:10",
            "first_hour_share": 1.0,
        },
        "lead_time": _lead_time(signed_up=1, no_show_rate=0.0),
        "comparison_kind": "PREVIOUS_PERIOD",
        "comparison": {
            "arrivals": 1,
            "excluded_arrivals": 1,
            "peak": {"start_minute": 30, "local_time": None, "arrivals": 1},
            "peak_local_time": "19:30",
            "mean_minutes": 30,
            "p90_minutes": 30,
            "mean_local_time": "19:30",
            "p90_local_time": "19:30",
            "first_hour_share": 1.0,
        },
    }
