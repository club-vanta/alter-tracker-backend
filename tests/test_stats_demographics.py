"""Tests for the org demographics statistics service."""

from datetime import UTC, datetime
from itertools import count

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import Integer, cast, literal, select
from sqlmodel import Session

from app.models.models import (
    GuestMazmoProfile,
    GuestType,
    Meetup,
    Organization,
    OrgRole,
    StatsAgeBucket,
    StatsComparisonKind,
)
from app.services.stats.attendance import attendance_stats
from app.services.stats.demographics import (
    GroupCounts,
    age_bucket,
    age_bucket_expr,
    demographic_counts,
    demographic_stats,
)
from app.services.stats.scope import finalized_meetup_conditions
from app.services.stats.scope import StatsPeriod
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

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

    assert [(g.key, g.attended) for g in stats.gender] == [("MALE", 3), ("FEMALE", 2), (None, 2)]
    assert [g.attended_share for g in stats.gender] == [0.4286, 0.2857, 0.2857]
    assert stats.summary.attended == 7
    assert {g.key: g.attended for g in stats.age}[StatsAgeBucket.UNKNOWN.value] == 7


def test_demographics_blank_gender_is_unknown_and_undefined_is_its_own_group(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="   ", has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="UNDEFINED", has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [(g.key, g.attended) for g in stats.gender] == [("UNDEFINED", 1), (None, 1)]


def test_demographics_drops_gender_groups_nobody_attended(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="FEMALE", cancelled_rsvp=True)
    _rsvp(session, meetup, profile=True, gender="WOMAN_TRANS")  # expected, but a no-show
    _rsvp(session, meetup, profile=True, gender="WOMAN_CIS", is_walkin=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [g.key for g in stats.gender] == ["MALE"]


def test_demographics_age_buckets_and_median(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    for age in [22, 27, 31, 31, 45, 126, 3, None]:
        _rsvp(session, meetup, profile=True, age=age, has_arrived=True)

    stats = demographic_stats(session, org.id, ALL_TIME, None)

    assert [g.key for g in stats.age] == [b.value for b in StatsAgeBucket]
    assert [g.attended for g in stats.age] == [1, 1, 2, 0, 1, 0, 3]
    assert stats.summary.median_age == 31.0
    assert stats.summary.known_age_share == 0.625
    assert [g.attended_share for g in stats.age] == [0.125, 0.125, 0.25, 0.0, 0.125, 0.0, 0.375]


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
    assert all((g.attended, g.attended_share) == (0, None) for g in stats.age)
    assert stats.summary.attended == 0
    assert stats.summary.median_age is None
    assert stats.summary.known_age_share is None


# -- demographic_counts --


def test_demographic_counts_walkin_counts_attended_but_not_expected(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", has_arrived=True, is_walkin=True)
    _rsvp(session, meetup, profile=True, gender="MALE", cancelled_rsvp=True)

    counts = demographic_counts(session, *finalized_meetup_conditions(org.id, ALL_TIME))

    assert counts.by_gender == {"MALE": GroupCounts(attended=1, expected=0, no_show=0)}


def test_demographic_counts_lists_every_age_bucket(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, age=31)
    _rsvp(session, meetup, profile=True, age=33, has_arrived=True)

    counts = demographic_counts(session, *finalized_meetup_conditions(org.id, ALL_TIME))

    assert counts.by_age == {
        bucket: GroupCounts(1, 2, 1) if bucket == StatsAgeBucket.AGE_30_34 else GroupCounts()
        for bucket in StatsAgeBucket
    }


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


def test_demographics_totals_match_attendance_stats(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", age=25, has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="FEMALE", age=31, has_arrived=True, is_walkin=True)
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.INVITED)
    _rsvp(session, meetup, profile=True, gender="MALE", age=40, cancelled_rsvp=True, has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="FEMALE", age=22)
    _rsvp(session, meetup, cancelled_rsvp=True)
    _rsvp(session, meetup, has_arrived=True, guest_type=GuestType.STAFF)
    _rsvp(session, meetup)  # no-show without a profile

    demographics = demographic_stats(session, org.id, ALL_TIME, None)
    counts = demographic_counts(session, *finalized_meetup_conditions(org.id, ALL_TIME))
    attendance = attendance_stats(session, org.id, ALL_TIME, None).totals

    for groups in (demographics.gender, demographics.age):
        assert sum(g.attended for g in groups) == attendance.attended == 4
    for by_key in (counts.by_gender.values(), counts.by_age.values()):
        assert sum(c.attended for c in by_key) == attendance.attended
        assert sum(c.expected for c in by_key) == attendance.signed_up - attendance.cancelled == 5
        assert sum(c.no_show for c in by_key) == attendance.no_show == 2
    assert demographics.summary.attended == attendance.attended


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


def test_demographics_single_meetup_without_attendance_has_zero_comparison(session: Session):
    org = make_org(session)
    meetup = _finalized_meetup(session, org, APRIL)

    stats = demographic_stats(session, org.id, ALL_TIME, meetup.id)

    assert stats.summary.attended == 0
    assert stats.comparison is not None
    assert (stats.comparison.attended, stats.comparison.median_age) == (0, None)


# -- GET /organizations/{org_id}/stats/demographics --


def _demographics_url(org: Organization) -> str:
    return f"/organizations/{org.id}/stats/demographics"


def _empty_summary() -> dict:
    return {
        "attended": 0,
        "median_age": None,
        "known_age_share": None,
    }


def test_demographic_stats_returns_401_without_auth(client: TestClient, org: Organization):
    resp = client.get(_demographics_url(org))

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_demographic_stats_returns_403_for_staff_member(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_demographics_url(org), headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_demographic_stats_returns_403_before_checking_the_period_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

    resp = client.get(_demographics_url(org), params={"from": "2026-05-01", "to": "2026-05-01"}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_demographic_stats_returns_403_before_checking_the_meetup_for_staff(
    client: TestClient, session: Session, org: Organization, staff_user, staff_headers: dict
):
    make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_demographics_url(org), params={"meetup_id": str(meetup.id)}, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_demographic_stats_returns_200_for_org_admin(client: TestClient, session: Session, org: Organization):
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", age=30, has_arrived=True)
    org_admin = make_user(session, username="org_admin")
    make_org_member(session, org=org, user=org_admin, role=OrgRole.ADMIN)
    headers = get_auth_headers(client, "org_admin", "a-very-secure-passphrase")

    resp = client.get(_demographics_url(org), headers=headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["summary"]["attended"] == 1


def test_demographic_stats_returns_200_with_exact_aggregate_body(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = _finalized_meetup(session, org, APRIL)
    _rsvp(session, meetup, profile=True, gender="MALE", age=22, has_arrived=True)
    _rsvp(session, meetup, profile=True, gender="MALE", age=31)
    _rsvp(session, meetup, has_arrived=True, is_walkin=True)

    resp = client.get(_demographics_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "gender": [
            {"key": "MALE", "attended": 1, "attended_share": 0.5},
            {"key": None, "attended": 1, "attended_share": 0.5},
        ],
        "age": [
            {"key": bucket.value, "attended": attended, "attended_share": attended_share}
            for bucket, attended, attended_share in [
                (StatsAgeBucket.AGE_18_24, 1, 0.5),
                (StatsAgeBucket.AGE_25_29, 0, 0.0),
                (StatsAgeBucket.AGE_30_34, 0, 0.0),
                (StatsAgeBucket.AGE_35_39, 0, 0.0),
                (StatsAgeBucket.AGE_40_49, 0, 0.0),
                (StatsAgeBucket.AGE_50_PLUS, 0, 0.0),
                (StatsAgeBucket.UNKNOWN, 1, 0.5),
            ]
        ],
        "summary": {
            "attended": 2,
            "median_age": 22.0,
            "known_age_share": 0.5,
        },
        "comparison_kind": None,
        "comparison": None,
    }


def test_demographic_stats_returns_200_for_one_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    selected = _finalized_meetup(session, org, APRIL)
    _rsvp(session, selected, profile=True, gender="FEMALE", age=40, has_arrived=True)
    other = _finalized_meetup(session, org, datetime(2026, 4, 8, 22, tzinfo=UTC))
    _rsvp(session, other, profile=True, gender="MALE", age=20, has_arrived=True)

    resp = client.get(
        _demographics_url(org),
        params={"from": "2026-04-01", "to": "2026-04-11", "meetup_id": str(selected.id)},
        headers=admin_headers,
    )

    body = resp.json()
    assert resp.status_code == status.HTTP_200_OK
    assert [(g["key"], g["attended"]) for g in body["gender"]] == [("FEMALE", 1)]
    assert body["summary"]["median_age"] == 40.0
    assert body["comparison_kind"] == "PERIOD_AVERAGE"
    assert body["comparison"]["attended"] == 2
    assert body["comparison"]["median_age"] == 30.0


def test_demographic_stats_returns_404_for_unfinalized_meetup(
    client: TestClient, session: Session, org: Organization, admin_headers: dict
):
    meetup = make_meetup(session, org=org, is_finalized=False)

    resp = client.get(_demographics_url(org), params={"meetup_id": str(meetup.id)}, headers=admin_headers)

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_demographic_stats_returns_422_when_from_not_before_to(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_demographics_url(org), params={"from": "2026-05-01", "to": "2026-04-01"}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_demographic_stats_returns_empty_body_for_empty_period(
    client: TestClient, org: Organization, admin_headers: dict
):
    resp = client.get(_demographics_url(org), headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "gender": [],
        "age": [{"key": bucket.value, "attended": 0, "attended_share": None} for bucket in StatsAgeBucket],
        "summary": _empty_summary(),
        "comparison_kind": None,
        "comparison": None,
    }
