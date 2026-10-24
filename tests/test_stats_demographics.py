"""Tests for the org demographics statistics service."""

from datetime import UTC, datetime
from itertools import count

import pytest
from sqlalchemy import Integer, cast, literal, select
from sqlmodel import Session

from app.models.models import GuestMazmoProfile, GuestType, Meetup, Organization, StatsAgeBucket, StatsComparisonKind
from app.services.stats.demographics import age_bucket, age_bucket_expr, demographic_stats
from app.services.stats.scope import StatsPeriod
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp

ALL_TIME = StatsPeriod(None, None)
RSVP_TIME = datetime(2026, 1, 1, tzinfo=UTC)
PARITY_AGES = [None, 3, 17, 18, 24, 25, 29, 30, 34, 35, 39, 40, 49, 50, 99, 100, 126]
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


def _rsvp(
    session: Session,
    meetup: Meetup,
    *,
    profile: bool = False,
    gender: str | None = None,
    age: int | None = None,
    guest=None,
    **kwargs,
):
    n = next(_ids)
    guest = guest or make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=f"Guest {n}")
    if profile:
        session.add(GuestMazmoProfile(guest_id=guest.id, gender=gender, age=age))
        session.commit()
    return make_rsvp(session, meetup=meetup, guest=guest, rsvp_time=RSVP_TIME, **kwargs)


APRIL = datetime(2026, 4, 5, 22, tzinfo=UTC)


# -- age_bucket --


def test_age_bucket_edges():
    expected = {
        None: StatsAgeBucket.UNKNOWN,
        3: StatsAgeBucket.UNKNOWN,
        17: StatsAgeBucket.UNKNOWN,
        18: StatsAgeBucket.AGE_18_24,
        24: StatsAgeBucket.AGE_18_24,
        25: StatsAgeBucket.AGE_25_29,
        29: StatsAgeBucket.AGE_25_29,
        30: StatsAgeBucket.AGE_30_34,
        34: StatsAgeBucket.AGE_30_34,
        35: StatsAgeBucket.AGE_35_39,
        39: StatsAgeBucket.AGE_35_39,
        40: StatsAgeBucket.AGE_40_49,
        49: StatsAgeBucket.AGE_40_49,
        50: StatsAgeBucket.AGE_50_PLUS,
        99: StatsAgeBucket.AGE_50_PLUS,
        100: StatsAgeBucket.UNKNOWN,
        126: StatsAgeBucket.UNKNOWN,
    }
    assert {age: age_bucket(age) for age in PARITY_AGES} == expected


def test_age_bucket_sql_matches_python(session: Session):
    for age in PARITY_AGES:
        value = cast(literal(age), Integer)
        sql_bucket = session.execute(select(age_bucket_expr(value))).scalar_one()
        assert sql_bucket == age_bucket(age).value, age


# -- demographic_stats --


def test_demographics_groups_by_gender_with_unknown_last(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    for _ in range(3):
        _rsvp(session, meetup, profile=True, gender="MALE", has_arrived=True)
    for _ in range(2):
        _rsvp(session, meetup, profile=True, gender="FEMALE", has_arrived=True)
    _rsvp(session, meetup, has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="", has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="FEMALE")

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.attended, g.expected, g.no_show) for g in stats.gender] == [
        ("MALE", 3, 3, 0),
        ("FEMALE", 2, 3, 1),
        (None, 2, 2, 0),
    ]
    assert stats.gender[1].no_show_rate == 0.3333
    assert [g.attended_share for g in stats.gender] == [0.4286, 0.2857, 0.2857]
    assert [g.expected_share for g in stats.gender] == [0.375, 0.375, 0.25]
    assert stats.summary.attended == 7
    assert [(s.key, s.attended_share) for s in stats.summary.gender_shares] == [
        ("MALE", 0.4286),
        ("FEMALE", 0.2857),
        (None, 0.2857),
    ]


def test_demographics_age_buckets_and_median(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    for age in [22, 27, 31, 31, 45, 126, None]:
        _rsvp(session, meetup, profile=True, age=age, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [g.key for g in stats.age] == [b.value for b in StatsAgeBucket]
    assert [g.attended for g in stats.age] == [1, 1, 2, 0, 1, 0, 2]
    assert stats.summary.median_age == 31.0
    assert stats.summary.known_age_share == 0.7143
    assert [s.attended_share for s in stats.summary.age_shares] == [0.1429, 0.1429, 0.2857, 0.0, 0.1429, 0.0, 0.2857]


def test_demographics_median_rounds_to_one_decimal(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    for age in [20, 21, 22, 30]:
        _rsvp(session, meetup, profile=True, age=age, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert stats.summary.median_age == 21.5


def test_demographics_counts_each_attendance(session: Session):
    org = make_org(session)
    first = _finalized_meetup(session, org, APRIL)
    second = _finalized_meetup(session, org, datetime(2026, 4, 12, 22, tzinfo=UTC))
    guest = make_guest(session, mazmo_user_id=1, mazmo_handle="repeat", displayname="Repeat")
    session.add(GuestMazmoProfile(guest_id=guest.id, gender="MALE", age=30))
    session.commit()
    make_rsvp(session, meetup=first, guest=guest, rsvp_time=RSVP_TIME, has_arrived=True)
    make_rsvp(session, meetup=second, guest=guest, rsvp_time=RSVP_TIME, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.attended) for g in stats.gender] == [("MALE", 2)]
    assert stats.summary.attended == 2


def test_demographics_only_staff_is_empty(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", age=30, has_arrived=True, guest_type=GuestType.STAFF.value)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert stats.gender == []
    assert len(stats.age) == 7
    assert all((g.attended, g.expected, g.no_show) == (0, 0, 0) for g in stats.age)
    assert all(g.no_show_rate is None and g.attended_share is None and g.expected_share is None for g in stats.age)
    assert stats.summary.attended == 0
    assert stats.summary.median_age is None
    assert stats.summary.known_age_share is None
    assert all(s.attended_share is None for s in stats.summary.age_shares)


def test_demographics_walkin_counts_attended_but_not_expected(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", has_arrived=True, is_walkin=True)
    _rsvp(session, meetup, profile=True, gender="MALE", cancelled_rsvp=True)

    [group] = demographic_stats(session, org.id, ALL_TIME, None).gender

    assert (group.attended, group.expected, group.no_show) == (1, 0, 0)
    assert group.no_show_rate is None


# -- comparison --


def test_demographics_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    previous = _finalized_meetup(session, org, datetime(2026, 3, 20, 22, tzinfo=UTC))
    current = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    _rsvp(session, previous, profile=True, gender="MALE", age=40, has_arrived=True)
    _rsvp(session, current, profile=True, gender="FEMALE", age=20, has_arrived=True)
    _rsvp(session, current, profile=True, gender="FEMALE", age=30, has_arrived=True)

    stats = demographic_stats(
        session, org.id, StatsPeriod(datetime(2026, 4, 1).date(), datetime(2026, 5, 1).date()), None
    )

    assert stats.summary.attended == 2
    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison is not None
    assert (stats.comparison.attended, stats.comparison.median_age) == (1, 40.0)
    assert [(s.key, s.attended_share) for s in stats.comparison.gender_shares] == [("MALE", 1.0)]


def test_demographics_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert stats.comparison_kind is None
    assert stats.comparison is None


def test_demographics_single_meetup_compares_with_period(session: Session):
    org = make_org(session)
    target = _finalized_meetup(session, org, datetime(2026, 4, 5, 22, tzinfo=UTC))
    other = _finalized_meetup(session, org, datetime(2026, 4, 12, 22, tzinfo=UTC))
    _rsvp(session, target, profile=True, gender="MALE", age=20, has_arrived=True)
    _rsvp(session, other, profile=True, gender="FEMALE", age=30, has_arrived=True)
    _rsvp(session, other, profile=True, gender="FEMALE", age=40, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, target.id)

    assert [(g.key, g.attended) for g in stats.gender] == [("MALE", 1)]
    assert stats.summary.median_age == 20.0
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison is not None
    assert (stats.comparison.attended, stats.comparison.median_age) == (3, 30.0)


@pytest.mark.parametrize("meetup_scope", [False, True])
def test_demographics_empty_org_has_zero_comparison(session: Session, meetup_scope: bool):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)

    stats = demographic_stats(session, org.id, ALL_TIME, meetup.id if meetup_scope else None)

    assert stats.summary.attended == 0
    if meetup_scope:
        assert stats.comparison is not None
        assert stats.comparison.median_age is None
