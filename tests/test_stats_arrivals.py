"""Tests for the org arrival statistics service."""

from datetime import UTC, date, datetime, timedelta
from itertools import count

from sqlmodel import Session

from app.models.models import GuestType, Meetup, Organization, StatsComparisonKind, StatsLeadTimeBucket
from app.services.stats.arrivals import (
    ArrivalBucket,
    LeadTimeRow,
    arrival_curve,
    arrival_stats,
    lead_time_bucket,
    summarize_arrivals,
)
from app.services.stats.scope import STATS_TZ, StatsPeriod
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp

ALL_TIME = StatsPeriod(None, None)
OFFSETS = [-10, 0, 5, 14, 15, 44, 90]
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


def test_summarize_arrivals_percentiles_and_first_hour():
    summary = summarize_arrivals(OFFSETS, arrival_curve(OFFSETS))

    assert summary.arrivals == 7
    assert summary.median_minutes == 14
    assert summary.p90_minutes == 90
    assert summary.first_hour_share == 0.8571
    assert summary.peak == ArrivalBucket(0, None, 3)
    assert summary.median_local_time is None
    assert summary.p90_local_time is None


def test_summarize_arrivals_local_times_from_start():
    start = datetime(2026, 4, 5, 23, 0, tzinfo=STATS_TZ)

    summary = summarize_arrivals(OFFSETS, arrival_curve(OFFSETS), local_start=start)

    assert summary.median_local_time == "23:14"
    assert summary.p90_local_time == "00:30"


def test_summarize_arrivals_empty():
    summary = summarize_arrivals([], [])

    assert summary.arrivals == 0
    assert summary.peak is None
    assert summary.median_minutes is None
    assert summary.p90_minutes is None
    assert summary.median_local_time is None
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
    assert stats.summary.median_local_time == "23:10"
    assert stats.summary.p90_local_time == "00:35"


def test_arrival_stats_excludes_staff_and_unarrived(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _arrived(session, meetup, 5, guest_type=GuestType.STAFF.value)
    _rsvp(session, meetup)
    _arrived(session, meetup, 7, guest_type=GuestType.INVITED.value)

    stats = arrival_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.arrivals == 1
    assert stats.summary.median_minutes == 7


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
    assert stats.summary.median_local_time is None
    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison is not None
    assert stats.comparison.arrivals == 2
    assert stats.comparison.median_minutes == 30
    assert stats.comparison.median_local_time is None


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
    assert stats.summary.median_local_time == "19:10"
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison is not None
    assert stats.comparison.arrivals == 2
    assert stats.comparison.median_local_time is None
