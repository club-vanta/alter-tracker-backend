"""Tests for the org door operations statistics service."""

from datetime import UTC, date, datetime, timedelta
from itertools import count

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import (
    EventLog,
    EventType,
    GuestType,
    Meetup,
    MeetupRsvp,
    Organization,
    OrgRole,
    StatsComparisonKind,
    User,
)
from app.services.payment import PaymentStanding, payment_standing
from app.services.stats.operations import (
    GUEST_TYPE_ORDER,
    BanRow,
    BusiestSlot,
    GuestTypeRow,
    OperationsPaymentStats,
    OperationsSummary,
    StaffCheckins,
    operations_stats,
)
from app.services.stats.scope import StatsPeriod
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user

ALL_TIME = StatsPeriod(None, None)
MARCH = StatsPeriod(date(2026, 3, 1), date(2026, 4, 1))
# 20:00 in Buenos Aires (UTC-3).
MARCH_6 = datetime(2026, 3, 6, 23, 0, tzinfo=UTC)
MARCH_13 = datetime(2026, 3, 13, 23, 0, tzinfo=UTC)
FEBRUARY_6 = datetime(2026, 2, 6, 23, 0, tzinfo=UTC)
_ids = count(4000)


def _meetup(session: Session, org: Organization, when: datetime, *, requires_payment: bool = False) -> Meetup:
    n = next(_ids)
    return make_meetup(
        session,
        org=org,
        name=f"Meetup {n}",
        mazmo_meetup_url=f"https://mazmo.net/test/meetup-{n}",
        date=when,
        requires_payment=requires_payment,
        is_finalized=True,
    )


def _rsvp(
    session: Session,
    meetup: Meetup,
    *,
    minutes: int | None = None,
    checked_in_by: User | None = None,
    displayname: str | None = None,
    **kwargs,
) -> MeetupRsvp:
    """An RSVP; minutes after the start makes it an arrival at that time."""
    n = next(_ids)
    guest = make_guest(session, mazmo_user_id=n, mazmo_handle=f"guest{n}", displayname=displayname or f"Guest {n}")
    if minutes is not None:
        kwargs.update(has_arrived=True, arrival_time=meetup.date + timedelta(minutes=minutes))
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, rsvp_time=meetup.date - timedelta(days=1), **kwargs)
    if checked_in_by is not None:
        rsvp.checked_in_by_id = checked_in_by.id
        session.add(rsvp)
        session.flush()
    return rsvp


def _ban(
    session: Session,
    org: Organization,
    when: datetime,
    *,
    displayname: str,
    reason: str | None = None,
    actor: User | None = None,
    event_type: EventType = EventType.BAN,
) -> EventLog:
    n = next(_ids)
    guest = make_guest(session, mazmo_user_id=n, mazmo_handle=f"banned{n}", displayname=displayname)
    event = EventLog(
        event_type=event_type,
        timestamp=when,
        org_id=org.id,
        guest_id=guest.id,
        actor_id=actor.id if actor is not None else None,
        reason=reason,
    )
    session.add(event)
    session.flush()
    return event


# -- payment_standing --


def test_payment_standing_only_normal_guests_pay():
    assert payment_standing(GuestType.NORMAL, True) == PaymentStanding.PAID
    assert payment_standing(GuestType.NORMAL, False) == PaymentStanding.PENDING
    for guest_type in (GuestType.INVITED, GuestType.STAFF, GuestType.VENDOR):
        assert payment_standing(guest_type, True) == PaymentStanding.EXEMPT
        assert payment_standing(guest_type, False) == PaymentStanding.EXEMPT


# -- Guest types --


def test_guest_types_include_every_type_in_order(session: Session, org: Organization):
    staff_user = make_user(session, username="door_staff")
    meetup = _meetup(session, org, MARCH_6)
    _rsvp(session, meetup, minutes=0, guest_type=GuestType.NORMAL)
    _rsvp(session, meetup, minutes=5, guest_type=GuestType.NORMAL, is_walkin=True)
    _rsvp(session, meetup, minutes=10, guest_type=GuestType.INVITED)
    _rsvp(session, meetup, minutes=15, guest_type=GuestType.STAFF, checked_in_by=staff_user)
    _rsvp(session, meetup, guest_type=GuestType.VENDOR)
    _rsvp(session, meetup, guest_type=GuestType.NORMAL, cancelled_rsvp=True)

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.guest_types == [
        GuestTypeRow(GuestType.NORMAL, 2, 0.5),
        GuestTypeRow(GuestType.INVITED, 1, 0.25),
        GuestTypeRow(GuestType.STAFF, 1, 0.25),
        GuestTypeRow(GuestType.VENDOR, 0, 0.0),
    ]
    assert stats.summary.attended == 4
    assert stats.staff == [
        StaffCheckins(user_id=staff_user.id, username="door_staff", checkins=1),
        StaffCheckins(user_id=None, username=None, checkins=3),
    ]


def test_empty_scope(session: Session, org: Organization):
    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.guest_types == [GuestTypeRow(t, 0, None) for t in GUEST_TYPE_ORDER]
    assert stats.payments is None
    assert stats.staff == []
    assert stats.busiest is None
    assert stats.ban_list == []
    assert stats.summary == OperationsSummary(attended=0, paid_share=None, avg_peak_checkins=None, bans=0)


# -- Payments --


def test_payments_count_paying_meetups_only(session: Session, org: Organization):
    first = _meetup(session, org, MARCH_6, requires_payment=True)
    _rsvp(session, first, minutes=0, has_paid=True)
    _rsvp(session, first, minutes=1)
    _rsvp(session, first, minutes=2, guest_type=GuestType.INVITED)
    _rsvp(session, first, has_paid=True, cancelled_rsvp=True)
    _rsvp(session, first, cancelled_rsvp=True)
    _rsvp(session, first, has_paid=True)  # paid no-show: not attended, not counted
    second = _meetup(session, org, MARCH_13, requires_payment=True)
    _rsvp(session, second, minutes=0, has_paid=True)
    _rsvp(session, second, minutes=1, guest_type=GuestType.STAFF, has_paid=True)
    free = _meetup(session, org, MARCH_6 + timedelta(days=1))
    _rsvp(session, free, minutes=0)
    _rsvp(session, free, has_paid=True, cancelled_rsvp=True)

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.payments == OperationsPaymentStats(
        paying_events=2, paid=2, pending=1, exempt=2, cancelled_but_paid=1, paid_share=0.6667
    )
    assert stats.summary.paid_share == 0.6667
    assert stats.summary.attended == 6


def test_payments_none_without_a_paying_meetup(session: Session, org: Organization):
    free = _meetup(session, org, MARCH_6)
    _rsvp(session, free, minutes=0, has_paid=True)

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.payments is None
    assert stats.summary.paid_share is None


def test_payments_match_the_door_card_for_one_meetup(client: TestClient, session: Session, org: Organization):
    member = make_user(session, username="parity_member")
    make_org_member(session, org=org, user=member, role=OrgRole.STAFF)
    headers = get_auth_headers(client, "parity_member", "a-very-secure-passphrase")
    meetup = _meetup(session, org, MARCH_6, requires_payment=True)
    _rsvp(session, meetup, minutes=0, has_paid=True)
    _rsvp(session, meetup, minutes=1, has_paid=True)
    _rsvp(session, meetup, minutes=2)
    _rsvp(session, meetup, minutes=3, guest_type=GuestType.VENDOR)
    _rsvp(session, meetup, minutes=4, guest_type=GuestType.INVITED, has_paid=True)
    _rsvp(session, meetup, has_paid=True, cancelled_rsvp=True)
    _rsvp(session, meetup)  # NORMAL no-show: the door card counts it as pending, the tab does not

    card = client.get(f"/organizations/{org.id}/meetups/{meetup.id}/stats", headers=headers).json()
    payments = operations_stats(session, org.id, MARCH, meetup.id).payments

    assert payments is not None
    # The door card splits every non-cancelled RSVP, so it also counts the no-show as pending.
    assert card["payment"] == {
        "paid_count": payments.paid,
        "unpaid_count": payments.pending + 1,
        "exempt_from_payment_count": payments.exempt,
    }
    assert card["cancellations"]["cancelled_but_paid_count"] == payments.cancelled_but_paid
    assert (payments.paid, payments.pending, payments.exempt, payments.cancelled_but_paid) == (2, 1, 2, 1)


# -- Check-ins per staff user --


def test_staff_checkins_sorted_with_unrecorded_last(session: Session, org: Organization):
    alice = make_user(session, username="alice")
    bob = make_user(session, username="bob")
    carol = make_user(session, username="carol")
    meetup = _meetup(session, org, MARCH_6)
    for user, times in ((bob, 2), (alice, 2), (carol, 3)):
        for _ in range(times):
            _rsvp(session, meetup, minutes=0, checked_in_by=user)
    for _ in range(4):
        _rsvp(session, meetup, minutes=0)
    _rsvp(session, meetup, checked_in_by=carol)  # never arrived: not a check-in

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.staff == [
        StaffCheckins(user_id=carol.id, username="carol", checkins=3),
        StaffCheckins(user_id=alice.id, username="alice", checkins=2),
        StaffCheckins(user_id=bob.id, username="bob", checkins=2),
        StaffCheckins(user_id=None, username=None, checkins=4),
    ]


# -- Busiest 15 minutes --


def test_busiest_slot_and_average_peak(session: Session, org: Organization):
    first = _meetup(session, org, MARCH_6)
    for minutes in (5, 10, 20):  # 20:00 x2, 20:15 x1
        _rsvp(session, first, minutes=minutes)
    second = _meetup(session, org, MARCH_13)
    for minutes in (31, 40, 44, 60, 65, 70):  # 20:30 x3, 21:00 x3
        _rsvp(session, second, minutes=minutes, guest_type=GuestType.STAFF)

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.busiest == BusiestSlot(meetup_id=second.id, meetup_name=second.name, local_time="20:30", checkins=3)
    assert stats.summary.avg_peak_checkins == 2.5


def test_busiest_slot_ties_go_to_the_earliest_meetup(session: Session, org: Organization):
    later = _meetup(session, org, MARCH_13)
    for minutes in (0, 1):
        _rsvp(session, later, minutes=minutes)
    earlier = _meetup(session, org, MARCH_6)
    for minutes in (50, 55):  # 20:45
        _rsvp(session, earlier, minutes=minutes)

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.busiest == BusiestSlot(meetup_id=earlier.id, meetup_name=earlier.name, local_time="20:45", checkins=2)
    assert stats.summary.avg_peak_checkins == 2.0


# -- Bans --


def test_bans_in_period_newest_first(session: Session, org: Organization):
    admin = make_user(session, username="ban_admin")
    other_org = make_org(session, name="Other Org", slug="other-org")
    # 2026-03-01 00:30 local is inside March; 2026-03-01 02:30 UTC is 2026-02-28 23:30 local.
    first = datetime(2026, 3, 1, 3, 30, tzinfo=UTC)
    second = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    _ban(session, org, first, displayname="No Reason", reason=None, actor=None)
    _ban(session, org, second, displayname="Rude Guest", reason="Rude at the door", actor=admin)
    _ban(session, org, datetime(2026, 3, 1, 2, 30, tzinfo=UTC), displayname="February Ban")
    _ban(session, org, second, displayname="Unbanned", event_type=EventType.UNBAN)
    _ban(session, other_org, second, displayname="Other Org Ban")

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.summary.bans == 2
    assert [(row.displayname, row.reason, row.timestamp, row.actor_username) for row in stats.ban_list] == [
        ("Rude Guest", "Rude at the door", second, "ban_admin"),
        ("No Reason", None, first, None),
    ]
    assert all(isinstance(row, BanRow) for row in stats.ban_list)


def test_ban_period_upper_bound_is_local_midnight(session: Session, org: Organization):
    # 23:30 local on the last day (March 31) is 02:30 UTC on April 1; 00:00 local April 1 is 03:00 UTC.
    _ban(session, org, datetime(2026, 4, 1, 2, 30, tzinfo=UTC), displayname="Last Day")
    _ban(session, org, datetime(2026, 4, 1, 3, 0, tzinfo=UTC), displayname="Next Day")

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.summary.bans == 1
    assert [row.displayname for row in stats.ban_list] == ["Last Day"]


def test_ban_list_keeps_the_newest_twenty(session: Session, org: Organization):
    start = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    for day in range(21):
        _ban(session, org, start + timedelta(days=day), displayname=f"Banned {day}")

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.summary.bans == 21
    assert len(stats.ban_list) == 20
    assert stats.ban_list[0].displayname == "Banned 20"
    assert stats.ban_list[-1].displayname == "Banned 1"


# -- One event and comparisons --


def test_one_event_scope_and_period_comparison(session: Session, org: Organization):
    meetup = _meetup(session, org, MARCH_6, requires_payment=True)
    _rsvp(session, meetup, minutes=0, has_paid=True)
    _rsvp(session, meetup, minutes=5)
    other = _meetup(session, org, MARCH_13)
    for minutes in (0, 1, 2):
        _rsvp(session, other, minutes=minutes)
    _ban(session, org, MARCH_6 + timedelta(hours=11, minutes=59), displayname="During")
    _ban(session, org, MARCH_6 + timedelta(hours=12), displayname="After")
    _ban(session, org, MARCH_6 - timedelta(minutes=1), displayname="Before")

    stats = operations_stats(session, org.id, MARCH, meetup.id)

    assert stats.summary == OperationsSummary(attended=2, paid_share=0.5, avg_peak_checkins=2.0, bans=1)
    assert [row.displayname for row in stats.ban_list] == ["During"]
    assert stats.busiest == BusiestSlot(meetup_id=meetup.id, meetup_name=meetup.name, local_time="20:00", checkins=2)
    assert stats.comparison_kind == StatsComparisonKind.PERIOD_AVERAGE
    assert stats.comparison == OperationsSummary(attended=5, paid_share=0.5, avg_peak_checkins=2.5, bans=3)


def test_aggregate_compares_with_previous_period(session: Session, org: Organization):
    current = _meetup(session, org, MARCH_6)
    _rsvp(session, current, minutes=0)
    previous = _meetup(session, org, FEBRUARY_6, requires_payment=True)
    _rsvp(session, previous, minutes=0, has_paid=True)
    _rsvp(session, previous, minutes=20)
    _ban(session, org, FEBRUARY_6, displayname="February")

    stats = operations_stats(session, org.id, MARCH, None)

    assert stats.summary == OperationsSummary(attended=1, paid_share=None, avg_peak_checkins=1.0, bans=0)
    assert stats.comparison_kind == StatsComparisonKind.PREVIOUS_PERIOD
    assert stats.comparison == OperationsSummary(attended=2, paid_share=0.5, avg_peak_checkins=1.0, bans=1)


def test_all_time_has_no_comparison(session: Session, org: Organization):
    stats = operations_stats(session, org.id, ALL_TIME, None)

    assert stats.comparison_kind is None
    assert stats.comparison is None
