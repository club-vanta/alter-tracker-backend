"""Tests for the org retention statistics service."""

from datetime import UTC, date, datetime
from itertools import count

from sqlmodel import Session

from app.models.models import Guest, GuestType, Meetup, Organization, StatsComparisonKind, StatsFrequencyBucket
from app.services.stats.retention import (
    CohortRow,
    FrequencyRow,
    LapsedRegular,
    MeetupNewReturning,
    RetentionSummary,
    frequency_bucket,
    retention_stats,
)
from app.services.stats.scope import StatsPeriod
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp

ALL_TIME = StatsPeriod(None, None)
MARCH = StatsPeriod(date(2026, 3, 1), date(2026, 4, 1))
APRIL = StatsPeriod(date(2026, 4, 1), date(2026, 5, 1))
TODAY = date(2026, 10, 24)
MARCH_DATE = datetime(2026, 3, 5, 22, tzinfo=UTC)
_ids = count(3000)


def _meetup(session: Session, org: Organization, month: int, day: int = 5, year: int = 2026) -> Meetup:
    """A finalized meetup at 22:00 UTC (19:00 in Buenos Aires) on the given day."""
    n = next(_ids)
    return make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=datetime(year, month, day, 22, tzinfo=UTC),
        is_finalized=True,
    )


def _guest(session: Session) -> Guest:
    n = next(_ids)
    return make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=f"Guest {n}")


def _attend(session: Session, guest: Guest, *meetups: Meetup, **kwargs) -> None:
    for meetup in meetups:
        make_rsvp(session, meetup=meetup, guest=guest, has_arrived=True, **kwargs)


def _stats(session: Session, org: Organization, period: StatsPeriod, meetup_id=None):
    return retention_stats(session, org.id, period, meetup_id, today=TODAY)


# -- frequency_bucket --


def test_frequency_bucket_edges():
    assert [frequency_bucket(n) for n in [1, 2, 3, 5, 6, 40]] == [
        StatsFrequencyBucket.ONE,
        StatsFrequencyBucket.TWO,
        StatsFrequencyBucket.THREE_FIVE,
        StatsFrequencyBucket.THREE_FIVE,
        StatsFrequencyBucket.SIX_PLUS,
        StatsFrequencyBucket.SIX_PLUS,
    ]


# -- new and returning --


def test_retention_new_and_returning_in_period(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    a, b, c = _guest(session), _guest(session), _guest(session)
    _attend(session, a, january, march)
    _attend(session, b, march)
    _attend(session, c, march)

    stats = _stats(session, org, MARCH)

    assert stats.summary == RetentionSummary(
        attendees=3, new_guests=2, returning=1, new_share=0.6667, first_timer_return_rate=0.0
    )
    assert stats.meetups == [MeetupNewReturning(march.id, march.name, MARCH_DATE, new=2, returning=1)]


def test_retention_returning_does_not_need_the_previous_period(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    a = _guest(session)
    _attend(session, a, january, march)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.new_guests, stats.summary.returning) == (0, 1)
    assert stats.comparison == RetentionSummary(0, 0, 0, None, None)


def test_retention_staff_attendance_is_not_a_first_time(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    guest = _guest(session)
    _attend(session, guest, january, guest_type=GuestType.STAFF.value)
    _attend(session, guest, march)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.attendees, stats.summary.new_guests) == (1, 1)


def test_retention_no_show_and_cancelled_are_not_attendances(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    no_show, cancelled = _guest(session), _guest(session)
    make_rsvp(session, meetup=january, guest=no_show)
    make_rsvp(session, meetup=january, guest=cancelled, cancelled_rsvp=True)
    _attend(session, no_show, march)
    _attend(session, cancelled, march)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.attendees, stats.summary.new_guests) == (2, 2)


def test_retention_walkin_counts_as_first_time(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)
    walkin, never_checked_in = _guest(session), _guest(session)
    _attend(session, walkin, march, is_walkin=True)
    make_rsvp(session, meetup=march, guest=never_checked_in, is_walkin=True)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.attendees, stats.summary.new_guests, stats.summary.returning) == (1, 1, 0)
    assert stats.summary.new_share == 1.0


def test_retention_first_timer_return_rate_looks_past_the_period(session: Session):
    org = make_org(session)
    march, may = _meetup(session, org, 3), _meetup(session, org, 5)
    b, c = _guest(session), _guest(session)
    _attend(session, b, march, may)
    _attend(session, c, march)

    stats = _stats(session, org, MARCH)

    assert stats.summary.first_timer_return_rate == 0.5


def test_retention_empty_period_has_null_shares(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)

    stats = _stats(session, org, MARCH)

    assert stats.summary == RetentionSummary(0, 0, 0, None, None)
    assert stats.meetups == [MeetupNewReturning(march.id, march.name, MARCH_DATE, new=0, returning=0)]
    assert stats.cohorts == []
    assert [(row.bucket, row.guests) for row in stats.frequency] == [(bucket, 0) for bucket in StatsFrequencyBucket]


def test_retention_meetups_in_date_order(session: Session):
    org = make_org(session)
    late, early = _meetup(session, org, 3, day=20), _meetup(session, org, 3, day=6)
    a = _guest(session)
    _attend(session, a, early, late)

    stats = _stats(session, org, MARCH)

    assert [(m.meetup_id, m.new, m.returning) for m in stats.meetups] == [(early.id, 1, 0), (late.id, 0, 1)]
    assert stats.meetups[0].date.tzinfo is not None


# -- cohorts --


def test_retention_cohorts_by_month(session: Session):
    org = make_org(session)
    january, february, march = _meetup(session, org, 1), _meetup(session, org, 2), _meetup(session, org, 3)
    guests = [_guest(session) for _ in range(4)]
    for guest in guests:
        _attend(session, guest, january)
    _attend(session, guests[0], february, march)
    _attend(session, guests[1], february)

    stats = retention_stats(session, org.id, ALL_TIME, None, today=date(2026, 3, 15))

    assert stats.cohorts == [CohortRow("2026-01", 4, [0.5, 0.25])]


def test_retention_cohort_uses_the_buenos_aires_month(session: Session):
    org = make_org(session)
    # 2026-02-01 01:00 UTC is 2026-01-31 22:00 in Buenos Aires.
    n = next(_ids)
    january_night = make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=datetime(2026, 2, 1, 1, tzinfo=UTC),
        is_finalized=True,
    )
    _attend(session, _guest(session), january_night)

    stats = retention_stats(session, org.id, ALL_TIME, None, today=date(2026, 1, 31))

    assert stats.cohorts == [CohortRow("2026-01", 1, [])]


def test_retention_cohorts_only_for_first_times_in_the_period(session: Session):
    org = make_org(session)
    january, march, april = _meetup(session, org, 1), _meetup(session, org, 3), _meetup(session, org, 4)
    old, new = _guest(session), _guest(session)
    _attend(session, old, january, march)
    _attend(session, new, march, april)

    stats = retention_stats(session, org.id, MARCH, None, today=date(2026, 5, 2))

    assert stats.cohorts == [CohortRow("2026-03", 1, [1.0, 0.0])]


def test_retention_cohorts_stop_after_twelve_months(session: Session):
    org = make_org(session)
    first, year_later, too_late = (
        _meetup(session, org, 1, year=2025),
        _meetup(session, org, 1, year=2026),
        _meetup(session, org, 2, year=2026),
    )
    _attend(session, _guest(session), first, year_later, too_late)

    stats = retention_stats(session, org.id, ALL_TIME, None, today=date(2026, 6, 1))

    assert stats.cohorts == [CohortRow("2025-01", 1, [0.0] * 11 + [1.0])]


# -- frequency --


def test_retention_frequency_buckets(session: Session):
    org = make_org(session)
    meetups = [_meetup(session, org, month) for month in range(1, 7)]
    for times in [1, 2, 4, 6]:
        _attend(session, _guest(session), *meetups[:times])

    stats = _stats(session, org, ALL_TIME)

    assert stats.frequency == [
        FrequencyRow(StatsFrequencyBucket.ONE, 1),
        FrequencyRow(StatsFrequencyBucket.TWO, 1),
        FrequencyRow(StatsFrequencyBucket.THREE_FIVE, 1),
        FrequencyRow(StatsFrequencyBucket.SIX_PLUS, 1),
    ]


def test_retention_frequency_counts_only_the_period(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    _attend(session, _guest(session), january, march)

    stats = _stats(session, org, MARCH)

    assert [(row.bucket, row.guests) for row in stats.frequency][:2] == [
        (StatsFrequencyBucket.ONE, 1),
        (StatsFrequencyBucket.TWO, 0),
    ]


# -- lapsed regulars --


def test_retention_lapsed_regulars(session: Session):
    org = make_org(session)
    before = [_meetup(session, org, month) for month in (1, 2, 3)]
    april = _meetup(session, org, 4)
    d, e, f = _guest(session), _guest(session), _guest(session)
    _attend(session, d, *before)
    _attend(session, e, *before, april)
    _attend(session, f, *before[:2])

    stats = _stats(session, org, APRIL)

    assert stats.lapsed == [
        LapsedRegular(d.id, d.displayname, attended_before=3, last_attended=datetime(2026, 3, 5, 22, tzinfo=UTC))
    ]
    assert stats.lapsed_total == 1

    all_time = _stats(session, org, ALL_TIME)

    assert all_time.lapsed == []
    assert all_time.lapsed_total is None


def test_retention_lapsed_regulars_sorted_by_attendances_then_last_date(session: Session):
    org = make_org(session)
    before = [_meetup(session, org, month) for month in (1, 2, 3)]
    _meetup(session, org, 4)
    most, recent, older = _guest(session), _guest(session), _guest(session)
    _attend(session, most, _meetup(session, org, 12, year=2025), *before)
    _attend(session, recent, *before)
    _attend(session, older, _meetup(session, org, 11, year=2025), *before[:2])

    stats = _stats(session, org, APRIL)

    assert [(r.guest_id, r.attended_before) for r in stats.lapsed] == [(most.id, 4), (recent.id, 3), (older.id, 3)]
    assert stats.lapsed[2].last_attended == datetime(2026, 2, 5, 22, tzinfo=UTC)
    assert stats.lapsed_total == 3


def test_retention_lapsed_ignores_attendances_after_the_period(session: Session):
    org = make_org(session)
    before = [_meetup(session, org, month) for month in (1, 2, 3)]
    may = _meetup(session, org, 5)
    d = _guest(session)
    _attend(session, d, *before, may)

    stats = _stats(session, org, APRIL)

    assert [r.guest_id for r in stats.lapsed] == [d.id]


def test_retention_lapsed_none_is_zero_total(session: Session):
    org = make_org(session)

    stats = _stats(session, org, APRIL)

    assert stats.lapsed == []
    assert stats.lapsed_total == 0


# -- one event and comparison --


def test_retention_one_event(session: Session):
    org = make_org(session)
    january, march, may = _meetup(session, org, 1), _meetup(session, org, 3), _meetup(session, org, 5)
    a, b, c = _guest(session), _guest(session), _guest(session)
    _attend(session, a, january, march)
    _attend(session, b, march, may)
    _attend(session, c, march)

    stats = _stats(session, org, ALL_TIME, march.id)

    assert stats.summary == RetentionSummary(
        attendees=3, new_guests=2, returning=1, new_share=0.6667, first_timer_return_rate=0.5
    )
    assert (stats.meetups, stats.cohorts, stats.frequency, stats.lapsed) == ([], [], [], [])
    assert stats.lapsed_total is None
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison == RetentionSummary(
        attendees=3, new_guests=3, returning=0, new_share=1.0, first_timer_return_rate=0.6667
    )


def test_retention_one_event_ignores_period_bounds(session: Session):
    org = make_org(session)
    january = _meetup(session, org, 1)
    _attend(session, _guest(session), january)

    stats = _stats(session, org, MARCH, january.id)

    assert stats.summary.attendees == 1
    assert stats.comparison == RetentionSummary(0, 0, 0, None, None)


def test_retention_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    february, march = _meetup(session, org, 2), _meetup(session, org, 3)
    a, b = _guest(session), _guest(session)
    _attend(session, a, february, march)
    _attend(session, b, march)

    stats = _stats(session, org, StatsPeriod(date(2026, 3, 1), date(2026, 3, 29)))

    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison == RetentionSummary(
        attendees=1, new_guests=1, returning=0, new_share=1.0, first_timer_return_rate=1.0
    )


def test_retention_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    _attend(session, _guest(session), _meetup(session, org, 3))

    stats = _stats(session, org, ALL_TIME)

    assert stats.comparison_kind is None
    assert stats.comparison is None
