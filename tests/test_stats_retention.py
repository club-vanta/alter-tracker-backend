"""Tests for the org retention statistics service."""

import uuid
from datetime import UTC, date, datetime
from itertools import count
from typing import Any

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import (
    Guest,
    GuestMazmoProfile,
    GuestType,
    Meetup,
    Organization,
    OrgRole,
    StatsComparisonKind,
    StatsFrequencyBucket,
)
from app.services.stats.retention import (
    NIGHT_AFTERWARDS,
    AfterwardsMeetup,
    CohortRow,
    FrequencyRow,
    LapsedRegular,
    MeetupNewReturning,
    NightGuest,
    RetentionSummary,
    frequency_bucket,
    retention_stats,
)
from app.services.stats.scope import STATS_TZ, StatsPeriod
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

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


def _attend(session: Session, guest: Guest, *meetups: Meetup, **kwargs: Any) -> None:
    for meetup in meetups:
        make_rsvp(session, meetup=meetup, guest=guest, has_arrived=True, **kwargs)


def _stats(session: Session, org: Organization, period: StatsPeriod, meetup_id: uuid.UUID | None = None):
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
        attendees=3, new_guests=2, came_back=0, returning=1, new_share=0.6667, first_timer_return_rate=0.0
    )
    assert stats.meetups == [MeetupNewReturning(march.id, march.name, MARCH_DATE, new=2, returning=1)]


def test_retention_returning_does_not_need_the_previous_period(session: Session):
    org = make_org(session)
    january, march = _meetup(session, org, 1), _meetup(session, org, 3)
    a = _guest(session)
    _attend(session, a, january, march)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.new_guests, stats.summary.returning) == (0, 1)
    assert stats.comparison == RetentionSummary(0, 0, 0, 0, None, None)


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


def test_retention_came_back_counts_returns_after_the_period(session: Session):
    org = make_org(session)
    march, may = _meetup(session, org, 3), _meetup(session, org, 5)
    b, c = _guest(session), _guest(session)
    _attend(session, b, march, may)
    _attend(session, c, march)

    stats = _stats(session, org, MARCH)

    assert stats.summary.came_back == 1


def test_retention_empty_period_has_null_shares(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)

    stats = _stats(session, org, MARCH)

    assert stats.summary == RetentionSummary(0, 0, 0, 0, None, None)
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


def test_retention_first_time_ignores_other_orgs_and_open_meetups(session: Session):
    org = make_org(session)
    other_org = make_org(session, name="Other Org", slug="other-org")
    foreign_january = _meetup(session, other_org, 1)
    open_february = make_meetup(
        session,
        org=org,
        name="Open",
        mazmo_meetup_url="https://mazmo.net/test/open",
        date=datetime(2026, 2, 5, 22, tzinfo=UTC),
        is_finalized=False,
    )
    march = _meetup(session, org, 3)
    guest = _guest(session)
    _attend(session, guest, foreign_january, open_february, march)

    stats = _stats(session, org, MARCH)

    assert stats.summary == RetentionSummary(
        attendees=1, new_guests=1, came_back=0, returning=0, new_share=1.0, first_timer_return_rate=0.0
    )
    assert stats.meetups == [MeetupNewReturning(march.id, march.name, MARCH_DATE, new=1, returning=0)]


def test_retention_same_start_time_counts_the_guest_once_as_new(session: Session):
    org = make_org(session)
    first, second = _meetup(session, org, 3), _meetup(session, org, 3)
    guest = _guest(session)
    _attend(session, guest, first, second)

    stats = _stats(session, org, MARCH)

    assert stats.summary == RetentionSummary(
        attendees=1, new_guests=1, came_back=1, returning=0, new_share=1.0, first_timer_return_rate=1.0
    )
    assert sorted((m.new, m.returning) for m in stats.meetups) == [(0, 1), (1, 0)]


def test_retention_invited_counts_as_audience_and_vendor_does_not(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)
    invited, vendor = _guest(session), _guest(session)
    _attend(session, invited, march, guest_type=GuestType.INVITED.value)
    _attend(session, vendor, march, guest_type=GuestType.VENDOR.value)

    stats = _stats(session, org, MARCH)

    assert (stats.summary.attendees, stats.summary.new_guests) == (1, 1)


# -- cohorts --


def test_retention_all_time_cohorts_are_ordered_by_month(session: Session):
    org = make_org(session)
    january, february = _meetup(session, org, 1), _meetup(session, org, 2)
    first, second = _guest(session), _guest(session)
    _attend(session, second, february)
    _attend(session, first, january, february)

    stats = retention_stats(session, org.id, ALL_TIME, None, today=date(2026, 3, 15))

    assert stats.cohorts == [CohortRow("2026-01", 1, [1.0, 0.0]), CohortRow("2026-02", 1, [0.0])]


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
        attendees=3, new_guests=2, came_back=1, returning=1, new_share=0.6667, first_timer_return_rate=0.5
    )
    assert (stats.meetups, stats.cohorts, stats.frequency, stats.lapsed) == ([], [], [], [])
    assert stats.lapsed_total is None
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison == RetentionSummary(
        attendees=3, new_guests=3, came_back=2, returning=0, new_share=1.0, first_timer_return_rate=0.6667
    )


def test_retention_one_event_ignores_period_bounds(session: Session):
    org = make_org(session)
    january = _meetup(session, org, 1)
    _attend(session, _guest(session), january)

    stats = _stats(session, org, MARCH, january.id)

    assert stats.summary.attendees == 1
    assert stats.comparison == RetentionSummary(0, 0, 0, 0, None, None)


def test_retention_one_event_night_guests(session: Session):
    """
    Every attendee of the night with which visit it was for them.

    WHY: the one-event chart draws one dot per person, grouped by visit.
    """
    org = make_org(session)
    january, february, march, may = (_meetup(session, org, m) for m in (1, 2, 3, 5))
    regular, newcomer, back_after_gap = _guest(session), _guest(session), _guest(session)
    _attend(session, regular, january, february, march, may)
    _attend(session, newcomer, march)
    _attend(session, back_after_gap, january, march)
    # Staff work is not an attendance.
    _attend(session, _guest(session), march, guest_type=GuestType.STAFF.value)

    night = _stats(session, org, ALL_TIME, march.id).night

    assert night is not None
    assert night.guests == [
        NightGuest(regular.id, regular.displayname, None, visit=3, days_since_previous=28, came_back=True),
        NightGuest(
            back_after_gap.id, back_after_gap.displayname, None, visit=2, days_since_previous=59, came_back=False
        ),
        NightGuest(newcomer.id, newcomer.displayname, None, visit=1, days_since_previous=None, came_back=False),
    ]


def test_retention_one_event_night_guests_have_their_mazmo_avatar(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)
    with_photo, without = _guest(session), _guest(session)
    session.add(GuestMazmoProfile(guest_id=with_photo.id, avatar_url="https://cdn.mazmo.net/a.jpg"))
    session.flush()
    _attend(session, with_photo, march)
    _attend(session, without, march)

    night = _stats(session, org, ALL_TIME, march.id).night

    assert night is not None
    assert {g.guest_id: g.avatar_url for g in night.guests} == {
        with_photo.id: "https://cdn.mazmo.net/a.jpg",
        without.id: None,
    }


def test_retention_one_event_afterwards_is_cumulative(session: Session):
    """
    How many of the night had come back by each later meetup.

    WHY: someone who returns at the 1st later meetup still counts as back at the 2nd.
    """
    org = make_org(session)
    march, april, may, june = (_meetup(session, org, m) for m in (3, 4, 5, 6))
    early, late, never, regular = _guest(session), _guest(session), _guest(session), _guest(session)
    _attend(session, regular, _meetup(session, org, 1), march, june)
    _attend(session, early, march, april)
    _attend(session, late, march, may)
    _attend(session, never, march)
    # Not part of the night, so not counted.
    _attend(session, _guest(session), april)

    night = _stats(session, org, ALL_TIME, march.id).night

    assert night is not None
    assert night.afterwards == [
        AfterwardsMeetup(april.id, april.name, datetime(2026, 4, 5, 22, tzinfo=UTC), back=1, back_new=1),
        AfterwardsMeetup(may.id, may.name, datetime(2026, 5, 5, 22, tzinfo=UTC), back=2, back_new=2),
        AfterwardsMeetup(june.id, june.name, datetime(2026, 6, 5, 22, tzinfo=UTC), back=3, back_new=2),
    ]


def test_retention_one_event_afterwards_takes_the_next_finalized_meetups(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)
    later = [_meetup(session, org, 4, day) for day in range(1, NIGHT_AFTERWARDS + 2)]
    make_meetup(
        session,
        org=org,
        name="Open",
        mazmo_meetup_url="https://mazmo.net/test/open",
        date=datetime(2026, 3, 20, 22, tzinfo=UTC),
        is_finalized=False,
    )
    _meetup(session, make_org(session, name="Other", slug="other"), 3, 20)
    _meetup(session, org, 2)
    _attend(session, _guest(session), march)

    night = _stats(session, org, ALL_TIME, march.id).night

    assert night is not None
    assert [m.meetup_id for m in night.afterwards] == [m.id for m in later[:NIGHT_AFTERWARDS]]


def test_retention_one_event_with_nothing_after(session: Session):
    org = make_org(session)
    march = _meetup(session, org, 3)
    _attend(session, _guest(session), march)

    night = _stats(session, org, ALL_TIME, march.id).night

    assert night is not None
    assert night.afterwards == []


def test_retention_aggregate_has_no_night(session: Session):
    org = make_org(session)
    _attend(session, _guest(session), _meetup(session, org, 3))

    assert _stats(session, org, MARCH).night is None


def test_retention_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    february, march = _meetup(session, org, 2), _meetup(session, org, 3)
    a, b = _guest(session), _guest(session)
    _attend(session, a, february, march)
    _attend(session, b, march)

    stats = _stats(session, org, StatsPeriod(date(2026, 3, 1), date(2026, 3, 29)))

    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison == RetentionSummary(
        attendees=1, new_guests=1, came_back=1, returning=0, new_share=1.0, first_timer_return_rate=1.0
    )


def test_retention_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    _attend(session, _guest(session), _meetup(session, org, 3))

    stats = _stats(session, org, ALL_TIME)

    assert stats.comparison_kind is None
    assert stats.comparison is None


# -- endpoint --


def _retention_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/retention"


def _empty_frequency() -> list[dict]:
    return [{"bucket": bucket.value, "guests": 0} for bucket in StatsFrequencyBucket]


def test_retention_stats_returns_401_without_auth(client: TestClient, org: Organization):
    resp = client.get(_retention_url(org))

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_retention_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_retention_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_retention_stats_returns_403_before_checking_the_period_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_retention_url(org), params={"from": "2026-05-01", "to": "2026-05-01"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_retention_stats_returns_403_before_checking_the_meetup_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_retention_url(org), params={"meetup_id": str(meetup.id)}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_retention_stats_returns_403_for_admin_of_other_org(client: TestClient, session: Session, org: Organization):
    other_org = make_org(session, name="Other Org", slug="other-org")
    other_admin = make_user(session, username="other_admin")
    make_org_member(session, org=other_org, user=other_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "other_admin", "a-very-secure-passphrase")

    resp = client.get(_retention_url(org), headers=headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_retention_stats_returns_200_for_org_admin(client: TestClient, session: Session, org: Organization):
    _attend(session, _guest(session), _meetup(session, org, 3))
    org_admin = make_user(session, username="org_admin")
    make_org_member(session, org=org, user=org_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "org_admin", "a-very-secure-passphrase")

    resp = client.get(_retention_url(org), headers=headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["summary"]["attendees"] == 1


def test_retention_stats_returns_200_with_exact_aggregate_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    # Real current dates: the endpoint uses today in Buenos Aires, so the cohort
    # list length depends on the month the meetups are in relative to it.
    today = datetime.now(STATS_TZ).date()
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    last_month = _meetup(session, org, month, year=year)
    previous = _meetup(session, org, 1, year=2020)
    returning, newcomer = _guest(session), _guest(session)
    _attend(session, returning, previous, last_month)
    _attend(session, newcomer, last_month)
    first_of_month = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)

    resp = client.get(
        _retention_url(org),
        params={"from": first_of_month.isoformat(), "to": next_month.isoformat()},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "summary": {
            "attendees": 2,
            "new_guests": 1,
            "came_back": 0,
            "returning": 1,
            "new_share": 0.5,
            "first_timer_return_rate": 0.0,
        },
        "meetups": [
            {
                "meetup_id": str(last_month.id),
                "name": last_month.name,
                "date": last_month.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "new": 1,
                "returning": 1,
            }
        ],
        "cohorts": [{"month": f"{year:04d}-{month:02d}", "size": 1, "retention": [0.0]}],
        "frequency": [
            {"bucket": "ONE", "guests": 2},
            {"bucket": "TWO", "guests": 0},
            {"bucket": "THREE_FIVE", "guests": 0},
            {"bucket": "SIX_PLUS", "guests": 0},
        ],
        "lapsed": [],
        "lapsed_total": 0,
        "comparison_kind": "PREVIOUS_PERIOD",
        "comparison": {
            "attendees": 0,
            "new_guests": 0,
            "came_back": 0,
            "returning": 0,
            "new_share": None,
            "first_timer_return_rate": None,
        },
        "night": None,
    }


def test_retention_stats_returns_200_for_one_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    january, march, may = _meetup(session, org, 1), _meetup(session, org, 3), _meetup(session, org, 5)
    a, b = _guest(session), _guest(session)
    _attend(session, a, january, march)
    _attend(session, b, march, may)

    resp = client.get(
        _retention_url(org),
        params={"from": "2026-03-01", "to": "2026-04-01", "meetup_id": str(march.id)},
        headers=admin_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "summary": {
            "attendees": 2,
            "new_guests": 1,
            "came_back": 1,
            "returning": 1,
            "new_share": 0.5,
            "first_timer_return_rate": 1.0,
        },
        "meetups": [],
        "cohorts": [],
        "frequency": [],
        "lapsed": [],
        "lapsed_total": None,
        "comparison_kind": "PERIOD_AVERAGE",
        "comparison": {
            "attendees": 2,
            "new_guests": 1,
            "came_back": 1,
            "returning": 1,
            "new_share": 0.5,
            "first_timer_return_rate": 1.0,
        },
        "night": {
            "guests": [
                {
                    "guest_id": str(a.id),
                    "displayname": a.displayname,
                    "avatar_url": None,
                    "visit": 2,
                    "days_since_previous": 59,
                    "came_back": False,
                },
                {
                    "guest_id": str(b.id),
                    "displayname": b.displayname,
                    "avatar_url": None,
                    "visit": 1,
                    "days_since_previous": None,
                    "came_back": True,
                },
            ],
            "afterwards": [
                {
                    "meetup_id": str(may.id),
                    "name": may.name,
                    "date": "2026-05-05T22:00:00Z",
                    "back": 1,
                    "back_new": 1,
                }
            ],
        },
    }


def test_retention_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_retention_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_retention_stats_returns_404_for_meetup_of_other_org(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    other_org = make_org(session, name="Other Org", slug="other-org")
    foreign = _meetup(session, other_org, 3)

    resp = client.get(_retention_url(org), params={"meetup_id": str(foreign.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_retention_stats_returns_422_when_from_not_before_to(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_retention_url(org), params={"from": "2026-05-01", "to": "2026-04-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_retention_stats_returns_empty_body_for_empty_period(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_retention_url(org), params={"from": "2026-04-01", "to": "2026-05-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "summary": {
            "attendees": 0,
            "new_guests": 0,
            "came_back": 0,
            "returning": 0,
            "new_share": None,
            "first_timer_return_rate": None,
        },
        "meetups": [],
        "cohorts": [],
        "frequency": _empty_frequency(),
        "lapsed": [],
        "lapsed_total": 0,
        "comparison_kind": "PREVIOUS_PERIOD",
        "comparison": {
            "attendees": 0,
            "new_guests": 0,
            "came_back": 0,
            "returning": 0,
            "new_share": None,
            "first_timer_return_rate": None,
        },
        "night": None,
    }


def test_retention_stats_serializes_a_lapsed_regular(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    before = [_meetup(session, org, month) for month in (1, 2, 3)]
    _meetup(session, org, 4)
    lapsed = _guest(session)
    _attend(session, lapsed, *before)

    resp = client.get(_retention_url(org), params={"from": "2026-04-01", "to": "2026-05-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["lapsed_total"] == 1
    assert resp.json()["lapsed"] == [
        {
            "guest_id": str(lapsed.id),
            "displayname": lapsed.displayname,
            "attended_before": 3,
            "last_attended": before[-1].date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    ]
