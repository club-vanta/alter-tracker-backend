"""Tests for the org no-show statistics service."""

from datetime import UTC, date, datetime
from itertools import count

import pytest
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
    StatsAgeBucket,
    StatsComparisonKind,
    StatsVisitHistory,
)
from app.services.stats import no_shows
from app.services.stats.arrivals import arrival_stats
from app.services.stats.attendance import attendance_stats
from app.services.stats.no_shows import NoShowSummary, no_show_stats
from app.services.stats.scope import StatsPeriod
from tests.conftest import make_guest, make_meetup, make_org, make_org_member, make_rsvp

ALL_TIME = StatsPeriod(None, None)
APRIL = StatsPeriod(date(2026, 4, 1), date(2026, 5, 1))
RSVP_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_ids = count(5000)


def _finalized_meetup(session: Session, org: Organization, day: int, month: int = 4) -> Meetup:
    n = next(_ids)
    return make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=datetime(2026, month, day, 23, tzinfo=UTC),
        is_finalized=True,
    )


def _guest(session: Session, name: str | None = None, *, gender: str | None = None, age: int | None = None) -> Guest:
    n = next(_ids)
    guest = make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=name or f"Guest {n}")
    if gender is not None or age is not None:
        session.add(GuestMazmoProfile(guest_id=guest.id, gender=gender, age=age))
        session.commit()
    return guest


def _came(session: Session, meetup: Meetup, guest: Guest | None = None, **kwargs):
    kwargs.setdefault("rsvp_time", RSVP_TIME)
    return make_rsvp(session, meetup=meetup, guest=guest or _guest(session), has_arrived=True, **kwargs)


def _missed(session: Session, meetup: Meetup, guest: Guest | None = None, **kwargs):
    kwargs.setdefault("rsvp_time", RSVP_TIME)
    return make_rsvp(session, meetup=meetup, guest=guest or _guest(session), **kwargs)


# -- summary and per meetup --


def test_no_shows_count_only_the_expected_audience(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, 5)
    _came(session, meetup)
    _came(session, meetup, guest_type=GuestType.INVITED.value)
    _missed(session, meetup)
    _missed(session, meetup, cancelled_rsvp=True)
    _came(session, meetup, is_walkin=True)
    _missed(session, meetup, guest_type=GuestType.STAFF.value)

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert stats.summary == NoShowSummary(expected=3, no_show=1, no_show_rate=0.3333, repeat_guests=0)
    assert [(m.meetup_id, m.expected, m.no_show, m.no_show_rate) for m in stats.meetups] == [(meetup.id, 3, 1, 0.3333)]


def test_no_shows_totals_match_attendance_stats(session: Session):
    org = make_org(session)
    for day in (5, 12):
        meetup = _finalized_meetup(session, org, day)
        _came(session, meetup)
        _missed(session, meetup)
        _missed(session, meetup, cancelled_rsvp=True)

    stats = no_show_stats(session, org.id, ALL_TIME, None)
    totals = attendance_stats(session, org.id, ALL_TIME, None).totals

    assert stats.summary.expected == totals.signed_up - totals.cancelled
    assert stats.summary.no_show == totals.no_show
    assert stats.summary.no_show_rate == totals.no_show_rate


def test_no_shows_meetup_without_rsvps_has_no_rate(session: Session):
    org = make_org(session)
    _finalized_meetup(session, org, 5)

    [row] = no_show_stats(session, org.id, ALL_TIME, None).meetups

    assert (row.expected, row.no_show, row.no_show_rate) == (0, 0, None)


# -- gender, age and lead time --


def test_no_shows_gender_groups_by_expected_with_unknown_last(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, 5)
    for _ in range(2):
        _missed(session, meetup, _guest(session, gender="FEMALE"))
    _came(session, meetup, _guest(session, gender="FEMALE"))
    _came(session, meetup, _guest(session, gender="MALE"))
    _missed(session, meetup)
    _came(session, meetup, _guest(session, gender="WOMAN_CIS"), is_walkin=True)  # never expected

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.expected, g.no_show, g.no_show_rate) for g in stats.gender] == [
        ("FEMALE", 3, 2, 0.6667),
        ("MALE", 1, 0, 0.0),
        (None, 1, 1, 1.0),
    ]


def test_no_shows_age_lists_every_bucket(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, 5)
    _missed(session, meetup, _guest(session, age=22))
    _came(session, meetup, _guest(session, age=23))

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.expected, g.no_show_rate) for g in stats.age] == [
        (bucket, 2, 0.5) if bucket == StatsAgeBucket.AGE_18_24 else (bucket, 0, None) for bucket in StatsAgeBucket
    ]


def test_no_shows_lead_time_matches_the_arrivals_tab(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, 5)
    _came(session, meetup)
    _missed(session, meetup)

    assert (
        no_show_stats(session, org.id, ALL_TIME, None).lead_time
        == arrival_stats(session, org.id, ALL_TIME, None).lead_time
    )


# -- visit history --


def test_no_shows_history_splits_first_timers_from_returning_guests(session: Session):
    org = make_org(session)
    march = _finalized_meetup(session, org, 20, month=3)
    april = _finalized_meetup(session, org, 5)
    may = _finalized_meetup(session, org, 3, month=5)

    regular = _guest(session)
    _came(session, march, regular)
    _missed(session, april, regular)  # came before: returning

    newcomer = _guest(session)
    _came(session, april, newcomer)  # first attendance is this one: first time

    flaky = _guest(session)
    _missed(session, march, flaky)  # an earlier no-show is not a visit
    _missed(session, april, flaky)

    later = _guest(session)
    _missed(session, april, later)
    _came(session, may, later)  # only came afterwards: first time

    stats = no_show_stats(session, org.id, APRIL, None)

    assert [(g.key, g.expected, g.no_show, g.no_show_rate) for g in stats.history] == [
        (StatsVisitHistory.FIRST_TIME, 3, 2, 0.6667),
        (StatsVisitHistory.RETURNING, 1, 1, 1.0),
    ]


def test_no_shows_history_ignores_other_orgs_and_unfinalized_meetups(session: Session):
    org = make_org(session)
    other_org = make_org(session, name="Other org", slug="other-org")
    guest = _guest(session)
    _came(session, _finalized_meetup(session, other_org, 1))
    _came(session, make_meetup(session, org=org, date=datetime(2026, 4, 2, 23, tzinfo=UTC), is_finalized=False), guest)
    _missed(session, _finalized_meetup(session, org, 5), guest)

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.expected) for g in stats.history] == [
        (StatsVisitHistory.FIRST_TIME, 1),
        (StatsVisitHistory.RETURNING, 0),
    ]


# -- guests --


def test_no_shows_guests_lists_repeat_no_shows_of_the_period(session: Session):
    org = make_org(session)
    first, second, third = (_finalized_meetup(session, org, day) for day in (5, 12, 19))
    outside = _finalized_meetup(session, org, 20, month=3)

    often = _guest(session, "Often")
    for meetup in (first, second, third):
        _missed(session, meetup, often)

    twice = _guest(session, "Twice")
    _missed(session, first, twice)
    _came(session, second, twice)
    _missed(session, third, twice)

    once = _guest(session, "Once")
    _missed(session, first, once)
    _missed(session, outside, once)  # outside the period: not counted

    excused = _guest(session, "Excused")
    _missed(session, first, excused)
    _missed(session, second, excused, cancelled_rsvp=True)

    stats = no_show_stats(session, org.id, APRIL, None)

    assert [(g.displayname, g.expected, g.attended, g.no_show, g.last_no_show) for g in stats.guests] == [
        ("Often", 3, 0, 3, third.date.replace(tzinfo=UTC)),
        ("Twice", 3, 1, 2, third.date.replace(tzinfo=UTC)),
    ]
    assert stats.guests_total == 2
    assert stats.summary.repeat_guests == 2


def test_no_shows_guests_ties_go_to_the_latest_no_show(session: Session):
    org = make_org(session)
    first, second, third = (_finalized_meetup(session, org, day) for day in (5, 12, 19))
    earlier = _guest(session, "Earlier")
    for meetup in (first, second):
        _missed(session, meetup, earlier)
    later = _guest(session, "Later")
    for meetup in (first, third):
        _missed(session, meetup, later)

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert [g.displayname for g in stats.guests] == ["Later", "Earlier"]


def test_no_shows_guests_are_cut_to_the_limit_with_the_full_count(session: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(no_shows, "GUEST_LIMIT", 1)
    org = make_org(session)
    meetups = [_finalized_meetup(session, org, day) for day in (5, 12)]
    for _ in range(3):
        guest = _guest(session)
        for meetup in meetups:
            _missed(session, meetup, guest)

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert len(stats.guests) == 1
    assert stats.guests_total == 3
    assert stats.summary.repeat_guests == 3


# -- one meetup --


def test_no_shows_one_meetup_lists_its_no_shows_over_all_time(session: Session):
    org = make_org(session)
    old = _finalized_meetup(session, org, 10, month=1)
    target = _finalized_meetup(session, org, 5)
    other = _finalized_meetup(session, org, 12)

    habitual = _guest(session, "Habitual")
    _missed(session, old, habitual)
    _missed(session, target, habitual)
    first_slip = _guest(session, "First slip")
    _came(session, old, first_slip)
    _missed(session, target, first_slip)
    _came(session, target)
    _missed(session, other)  # missed another meetup, not this one

    stats = no_show_stats(session, org.id, APRIL, target.id)

    assert [(g.displayname, g.expected, g.attended, g.no_show) for g in stats.guests] == [
        ("Habitual", 2, 0, 2),
        ("First slip", 2, 1, 1),
    ]
    assert stats.guests_total == 2
    assert stats.summary == NoShowSummary(expected=3, no_show=2, no_show_rate=0.6667, repeat_guests=1)
    assert stats.meetups == []
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison == NoShowSummary(expected=4, no_show=3, no_show_rate=0.75, repeat_guests=0)


def test_no_shows_one_meetup_breakdowns_cover_only_that_meetup(session: Session):
    org = make_org(session)
    target = _finalized_meetup(session, org, 5)
    other = _finalized_meetup(session, org, 12)
    _missed(session, target, _guest(session, gender="MALE"))
    _missed(session, other, _guest(session, gender="FEMALE"))

    stats = no_show_stats(session, org.id, APRIL, target.id)

    assert [(g.key, g.expected) for g in stats.gender] == [("MALE", 1)]
    assert sum(g.expected for g in stats.age) == 1
    assert sum(g.expected for g in stats.history) == 1
    assert sum(r.signed_up for r in stats.lead_time) == 1


# -- comparison --


def test_no_shows_aggregate_compares_with_previous_period(session: Session):
    org = make_org(session)
    march = [_finalized_meetup(session, org, day, month=3) for day in (10, 20)]
    repeat = _guest(session)
    for meetup in march:
        _missed(session, meetup, repeat)
    _came(session, march[0])
    _missed(session, _finalized_meetup(session, org, 5))

    stats = no_show_stats(session, org.id, APRIL, None)

    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison == NoShowSummary(expected=3, no_show=2, no_show_rate=0.6667, repeat_guests=1)


def test_no_shows_all_time_has_no_comparison(session: Session):
    org = make_org(session)
    _finalized_meetup(session, org, 5)

    stats = no_show_stats(session, org.id, ALL_TIME, None)

    assert stats.comparison_kind is None
    assert stats.comparison is None


# -- GET /organizations/{org_id}/stats/no-shows --


def _no_shows_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/no-shows"


def _lead_time(**same_day: int | float | None) -> list[dict]:
    """Every lead-time bucket empty except SAME_DAY, which takes the given fields."""
    empty = {"signed_up": 0, "cancelled": 0, "no_show": 0, "no_show_rate": None}
    buckets = ["SAME_DAY", "ONE_DAY", "TWO_THREE", "FOUR_SEVEN", "EIGHT_FOURTEEN", "FIFTEEN_PLUS"]
    return [{"bucket": b, **empty, **(same_day if b == "SAME_DAY" else {})} for b in buckets]


def _age(**buckets: tuple[int, int, float | None]) -> list[dict]:
    return [
        {
            "key": b.value,
            **dict(zip(("expected", "no_show", "no_show_rate"), buckets.get(b.value, (0, 0, None)), strict=True)),
        }
        for b in StatsAgeBucket
    ]


def test_no_show_stats_returns_401_without_auth(client: TestClient, org: Organization):
    resp = client.get(_no_shows_url(org))

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_no_show_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_no_shows_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_no_show_stats_returns_200_with_exact_aggregate_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    first = _finalized_meetup(session, org, 5)
    second = _finalized_meetup(session, org, 12)
    guest = _guest(session, "Luna", gender="FEMALE", age=30)
    for meetup in (first, second):
        make_rsvp(session, meetup=meetup, guest=guest, rsvp_time=meetup.date)
    _came(session, second, _guest(session, gender="FEMALE", age=31), rsvp_time=second.date)

    resp = client.get(_no_shows_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "summary": {"expected": 3, "no_show": 2, "no_show_rate": 0.6667, "repeat_guests": 1},
        "meetups": [
            {
                "meetup_id": str(first.id),
                "name": first.name,
                "date": "2026-04-05T23:00:00Z",
                "expected": 1,
                "no_show": 1,
                "no_show_rate": 1.0,
            },
            {
                "meetup_id": str(second.id),
                "name": second.name,
                "date": "2026-04-12T23:00:00Z",
                "expected": 2,
                "no_show": 1,
                "no_show_rate": 0.5,
            },
        ],
        "gender": [{"key": "FEMALE", "expected": 3, "no_show": 2, "no_show_rate": 0.6667}],
        "age": _age(AGE_30_34=(3, 2, 0.6667)),
        "history": [
            {"key": "FIRST_TIME", "expected": 3, "no_show": 2, "no_show_rate": 0.6667},
            {"key": "RETURNING", "expected": 0, "no_show": 0, "no_show_rate": None},
        ],
        "lead_time": _lead_time(signed_up=3, no_show=2, no_show_rate=0.6667),
        "guests": [
            {
                "guest_id": str(guest.id),
                "displayname": "Luna",
                "expected": 2,
                "attended": 0,
                "no_show": 2,
                "last_no_show": "2026-04-12T23:00:00Z",
            }
        ],
        "guests_total": 1,
        "comparison_kind": None,
        "comparison": None,
    }


def test_no_show_stats_returns_200_for_one_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    target = _finalized_meetup(session, org, 5)
    _missed(session, target, _guest(session, "Tomi"))

    resp = client.get(
        _no_shows_url(org),
        params={"from": "2026-04-01", "to": "2026-05-01", "meetup_id": str(target.id)},
        headers=admin_headers,
    )

    body = resp.json()
    assert resp.status_code == status.HTTP_200_OK
    assert [(g["displayname"], g["no_show"]) for g in body["guests"]] == [("Tomi", 1)]
    assert body["meetups"] == []
    assert body["comparison_kind"] == "PERIOD_AVERAGE"
    assert body["comparison"] == {"expected": 1, "no_show": 1, "no_show_rate": 1.0, "repeat_guests": 0}


def test_no_show_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_no_shows_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_no_show_stats_returns_422_when_from_not_before_to(client: TestClient, org: Organization, admin_headers: dict):
    resp = client.get(_no_shows_url(org), params={"from": "2026-05-01", "to": "2026-04-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_no_show_stats_returns_empty_body_for_empty_period(client: TestClient, org: Organization, admin_headers: dict):
    resp = client.get(_no_shows_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "summary": {"expected": 0, "no_show": 0, "no_show_rate": None, "repeat_guests": 0},
        "meetups": [],
        "gender": [],
        "age": _age(),
        "history": [
            {"key": "FIRST_TIME", "expected": 0, "no_show": 0, "no_show_rate": None},
            {"key": "RETURNING", "expected": 0, "no_show": 0, "no_show_rate": None},
        ],
        "lead_time": _lead_time(),
        "guests": [],
        "guests_total": 0,
        "comparison_kind": None,
        "comparison": None,
    }
