"""Tests for the shared period and scope filters used by org statistics."""

from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session

from app.services.stats.scope import (
    InvalidStatsPeriodError,
    StatsPeriod,
    finalized_meetups_in,
)
from tests.conftest import make_meetup, make_org

# -- StatsPeriod --


def test_stats_period_rejects_start_not_before_end():
    with pytest.raises(InvalidStatsPeriodError):
        StatsPeriod(date(2026, 5, 1), date(2026, 5, 1))


def test_stats_period_utc_bounds_use_buenos_aires_midnight():
    period = StatsPeriod(date(2026, 1, 1), date(2026, 2, 1))

    assert period.utc_bounds() == (
        datetime(2026, 1, 1, 3, tzinfo=UTC),
        datetime(2026, 2, 1, 3, tzinfo=UTC),
    )


def test_stats_period_utc_bounds_are_none_for_open_ends():
    assert StatsPeriod(None, None).utc_bounds() == (None, None)


def test_stats_period_previous_has_same_length():
    period = StatsPeriod(date(2026, 4, 1), date(2026, 4, 11))

    assert period.previous() == StatsPeriod(date(2026, 3, 22), date(2026, 4, 1))


def test_stats_period_previous_is_none_without_both_bounds():
    assert StatsPeriod(None, date(2026, 1, 1)).previous() is None
    assert StatsPeriod(date(2026, 1, 1), None).previous() is None


# -- finalized_meetups_in --


def test_finalized_meetups_in_uses_local_date_for_late_events(session: Session):
    """
    WHY: 23:30 on Dec 31 in Buenos Aires is 02:30 UTC on Jan 1, but the event
    belongs to December for period filters.
    """
    org = make_org(session)
    meetup = make_meetup(
        session,
        org=org,
        date=datetime(2027, 1, 1, 2, 30, tzinfo=UTC),
        is_finalized=True,
    )

    december = session.exec(finalized_meetups_in(org.id, StatsPeriod(date(2026, 12, 1), date(2027, 1, 1)))).all()
    january = session.exec(finalized_meetups_in(org.id, StatsPeriod(date(2027, 1, 1), date(2027, 2, 1)))).all()

    assert [m.id for m in december] == [meetup.id]
    assert january == []


def test_finalized_meetups_in_skips_open_meetups_and_other_orgs(session: Session):
    org = make_org(session, slug="mine")
    other = make_org(session, name="Other", slug="other")
    when = datetime(2026, 6, 15, 12, tzinfo=UTC)
    mine = make_meetup(
        session,
        org=org,
        date=when,
        is_finalized=True,
        mazmo_meetup_url="https://mazmo.net/c/mine",
    )
    make_meetup(session, org=org, date=when, mazmo_meetup_url="https://mazmo.net/c/open")
    make_meetup(
        session,
        org=other,
        date=when,
        is_finalized=True,
        mazmo_meetup_url="https://mazmo.net/c/theirs",
    )

    found = session.exec(finalized_meetups_in(org.id, StatsPeriod(None, None))).all()

    assert [m.id for m in found] == [mine.id]
