"""
Tests for the guest_type feature: payment exemption categories per RSVP.

Covers: GuestType enum/schema validation, PATCH .../guests/{id}/type,
the check-in payment gate exemption, GET .../meetups/{id}/stats, and
end-to-end scenarios that chain multiple endpoints together.

Sync-specific guest_type regression tests live in test_sync.py.
Event-log filter regression test lives in test_events.py (TestEventFiltering).
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.models import EventLog, EventType, GuestType, MeetupRsvp, Organization, OrgRole, User
from app.schemas import GuestTypeUpdateRequest
from tests.conftest import get_auth_headers, make_guest, make_meetup, make_org, make_org_member, make_rsvp, make_user


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    """Add staff_user to the default org with OrgRole.STAFF (mirrors test_meetups.py)."""
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


# -- GuestType enum -------------------------------------------------------


def test_guest_type_enum_has_exactly_four_values():
    """
    Verify GuestType has exactly NORMAL, INVITED, VENDOR, STAFF.

    WHY: Regression guard - if someone adds a fifth value without updating
    the stats formulas in get_meetup_stats(), this test turns that into a
    loud failure instead of a silently wrong stats endpoint.
    """
    values = {member.value for member in GuestType}
    assert values == {"NORMAL", "INVITED", "VENDOR", "STAFF"}
    assert len(GuestType) == 4


def test_guest_type_update_request_rejects_invalid_value():
    """
    Verify that GuestTypeUpdateRequest rejects a value outside the enum.

    WHY: This is the 422 the PATCH .../type endpoint will return for a
    malformed request body, enforced entirely by the GuestType field type
    - no manual validation needed in the router.
    """
    with pytest.raises(ValidationError):
        GuestTypeUpdateRequest(guest_type="BOGUS")  # type: ignore[arg-type]


def test_guest_list_response_includes_guest_type(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that GET .../meetups/{id}/guests exposes guest_type per RSVP.

    WHY: RsvpPublic.guest_type must round-trip through the existing guest
    list endpoint (no router code change needed there - model_validate
    picks up the new field automatically), matching how has_paid already
    surfaces there.
    """
    guest = make_guest(session, mazmo_user_id=501, mazmo_handle="default_type_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.get(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK
    guest_entry = resp.json()["guests"][0]
    assert guest_entry["rsvp"]["guest_type"] == "NORMAL"


def test_new_rsvp_defaults_to_guest_type_normal_via_walkin(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a walk-in RSVP defaults to guest_type=NORMAL.

    WHY: add_walkin_guest() builds MeetupRsvp() without setting guest_type,
    same as the sync path - must rely on the model default.
    """
    guest = make_guest(session, mazmo_user_id=502, mazmo_handle="walkin_guest")

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/add-walkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["rsvp"]["guest_type"] == "NORMAL"


# -- PATCH .../guests/{guest_id}/type --------------------------------------


def test_update_guest_type_returns_200_and_updates_rsvp(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that an admin can reclassify a guest and the RSVP reflects it.
    """
    guest = make_guest(session, mazmo_user_id=510, mazmo_handle="vendor_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "VENDOR"

    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.guest_type == "VENDOR"


def test_update_guest_type_returns_403_for_staff_non_admin(
    client: TestClient,
    staff_headers: dict,
    session: Session,
    meetup,
    org_staff_member,
):
    """
    Verify that a STAFF-role org member cannot change guest_type.

    WHY: Same permission level as mark_guest_paid - reclassification is
    admin-only.
    """
    guest = make_guest(session, mazmo_user_id=511, mazmo_handle="staff_blocked_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=staff_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_update_guest_type_returns_401_without_auth(client: TestClient, session: Session, meetup):
    """Verify that an unauthenticated request is rejected."""
    guest = make_guest(session, mazmo_user_id=512, mazmo_handle="no_auth_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_update_guest_type_returns_404_when_guest_not_rsvped_to_meetup(client: TestClient, admin_headers: dict, meetup):
    """Verify that changing guest_type for a non-RSVPed guest returns 404."""
    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{uuid.uuid4()}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_update_guest_type_returns_404_for_nonexistent_meetup(
    client: TestClient, admin_headers: dict, org: Organization
):
    """Verify that changing guest_type for a non-existent meetup returns 404."""
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{uuid.uuid4()}/guests/{uuid.uuid4()}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_update_guest_type_returns_403_for_admin_of_different_org(client: TestClient, session: Session):
    """
    Verify multi-tenant isolation: an Org A admin cannot change guest_type
    on an Org B meetup.

    WHY: get_org_admin(org_id) checks membership in the org_id from the
    URL path - an admin of a different org has no membership row there at
    all, so this must 403 before ever looking at the RSVP.
    """
    org_a = make_org(session, name="Org A Guest Type", slug="org-a-guest-type")
    org_b = make_org(session, name="Org B Guest Type", slug="org-b-guest-type")
    admin_a = make_user(session, username="admin_a_guest_type")
    make_org_member(session, org=org_a, user=admin_a, role=OrgRole.ADMIN)
    headers_a = get_auth_headers(client, "admin_a_guest_type", "a-very-secure-passphrase")

    guest = make_guest(session, mazmo_user_id=513, mazmo_handle="cross_org_guest")
    meetup_b = make_meetup(
        session, org=org_b, name="Org B Meetup", mazmo_meetup_url="https://mazmo.net/test/org-b-meetup-gt-1"
    )
    make_rsvp(session, meetup=meetup_b, guest=guest)

    resp = client.patch(
        f"/organizations/{org_b.id}/meetups/{meetup_b.id}/guests/{guest.id}/type",
        headers=headers_a,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_update_guest_type_creates_audit_log_with_old_and_new_reason(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
    admin_user: User,
):
    """
    Verify that changing guest_type writes a GUEST_TYPE_CHANGED EventLog
    with a reason naming both the old and new value.
    """
    guest = make_guest(session, mazmo_user_id=514, mazmo_handle="audit_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "STAFF"},
    )

    assert resp.status_code == status.HTTP_200_OK

    event = session.exec(
        select(EventLog).where(EventLog.guest_id == guest.id).where(EventLog.event_type == EventType.GUEST_TYPE_CHANGED)
    ).one()
    assert event.actor_id == admin_user.id
    assert event.meetup_id == meetup.id
    assert event.reason == "Changed guest_type from NORMAL to STAFF"


def test_update_guest_type_does_not_modify_has_paid(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """
    Verify that reclassifying a guest who already paid leaves has_paid,
    paid_at, and paid_by_id untouched.
    """
    guest = make_guest(session, mazmo_user_id=515, mazmo_handle="already_paid_guest")
    paid_at = datetime(2026, 3, 17, 21, 0, tzinfo=UTC)
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, has_paid=True, paid_at=paid_at)

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(rsvp)
    assert rsvp.has_paid is True
    # paid_at comes back tz-naive from the DB (the model field has no
    # sa_type=DateTime(timezone=True)), so compare naive values only.
    assert rsvp.paid_at is not None
    assert rsvp.paid_at.replace(tzinfo=None) == paid_at.replace(tzinfo=None)
    assert rsvp.guest_type == "VENDOR"


def test_update_guest_type_back_to_normal(
    client: TestClient,
    admin_headers: dict,
    session: Session,
    meetup,
):
    """Verify a VENDOR -> NORMAL round-trip works."""
    guest = make_guest(session, mazmo_user_id=516, mazmo_handle="roundtrip_guest")
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, guest_type="VENDOR")

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "NORMAL"},
    )

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "NORMAL"
    session.refresh(rsvp)
    assert rsvp.guest_type == "NORMAL"


# -- Check-in payment gate exemption ---------------------------------------


@pytest.fixture()
def paid_meetup(session: Session, org: Organization):
    """A meetup that requires payment before check-in."""
    return make_meetup(
        session,
        org=org,
        name="Alter Paid Event - Guest Type",
        mazmo_meetup_url="https://mazmo.net/test/alter-paid-guest-type",
        requires_payment=True,
    )


def test_checkin_blocks_normal_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """
    Verify that an unpaid NORMAL guest is still blocked at check-in.

    WHY: Regression guard - the guest_type exemption must not weaken the
    existing payment gate for the default category.
    """
    guest = make_guest(session, mazmo_user_id=520, mazmo_handle="normal_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest)

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_checkin_allows_invited_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid INVITED guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=521, mazmo_handle="invited_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="INVITED")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_vendor_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid VENDOR guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=522, mazmo_handle="vendor_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="VENDOR")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_staff_unpaid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """Verify that an unpaid STAFF guest passes the payment gate."""
    guest = make_guest(session, mazmo_user_id=523, mazmo_handle="staff_unpaid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="STAFF")

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_normal_paid_guest_when_requires_payment(
    client: TestClient, staff_headers: dict, session: Session, paid_meetup, org_staff_member
):
    """
    Verify that a NORMAL guest who has paid still checks in successfully.

    WHY: Regression guard for the existing happy path.
    """
    guest = make_guest(session, mazmo_user_id=524, mazmo_handle="normal_paid")
    make_rsvp(session, meetup=paid_meetup, guest=guest, has_paid=True, paid_at=datetime.now(UTC))

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_normal_guest_when_requires_payment_false(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify that a NORMAL guest checks in freely when the meetup itself
    does not require payment.

    WHY: Regression guard - the meetup-level flag must still be the
    primary gate; guest_type only matters when requires_payment is True.
    """
    guest = make_guest(session, mazmo_user_id=525, mazmo_handle="normal_free_event")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.post(
        f"/organizations/{meetup.org_id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )

    assert resp.status_code == status.HTTP_200_OK


def test_checkin_allows_banned_guest_regardless_of_guest_type(
    client: TestClient,
    staff_headers: dict,
    admin_headers: dict,
    session: Session,
    org: Organization,
    paid_meetup,
    org_staff_member,
):
    """
    Verify that check-in does NOT block a banned guest, and that the
    guest_type payment exemption does not change that.

    This is intentional, confirmed behavior, not a gap: checkin_guest()
    in app/routers/meetups.py never queries OrganizationBan or checks
    is_banned. Ban status is informational only - GuestWithBanPublic.
    is_banned is surfaced in the guest list (GET .../meetups/{id}/guests)
    purely so door staff can see the warning and still decide to let the
    guest in anyway. The API has never enforced a hard block here, and it
    should not start now as an accidental side effect of this feature.

    This test verifies the two things that ARE this feature's
    responsibility: (1) a banned guest classified as STAFF (and therefore
    payment-exempt) still checks in successfully (200) - the guest_type
    exemption composes correctly with a ban, neither blocks the other -
    and (2) is_banned is still correctly reported as True via the guest
    list endpoint afterward, so staff retain the warning they need to
    intervene by hand if they choose to.
    """
    guest = make_guest(session, mazmo_user_id=526, mazmo_handle="banned_staff_guest")
    make_rsvp(session, meetup=paid_meetup, guest=guest, guest_type="STAFF")
    client.patch(
        f"/organizations/{org.id}/guests/{guest.id}/ban",
        json={"reason": "Testing ban plus payment-exemption composition"},
        headers=admin_headers,
    )

    resp = client.post(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    list_resp = client.get(
        f"/organizations/{paid_meetup.org_id}/meetups/{paid_meetup.id}/guests",
        headers=staff_headers,
    )
    assert list_resp.status_code == status.HTTP_200_OK
    guest_entry = next(g for g in list_resp.json()["guests"] if g["guest"]["id"] == str(guest.id))
    assert guest_entry["guest"]["is_banned"] is True


# -- GET .../meetups/{meetup_id}/stats -------------------------------------


def test_meetup_stats_returns_200_with_grouped_shape(client: TestClient, staff_headers: dict, meetup, org_staff_member):
    """Verify the response has all 4 sub-objects with their fields."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert set(data.keys()) == {"attendance", "cancellations", "guest_types", "payment"}
    assert set(data["attendance"].keys()) == {"total_rsvps", "arrived_count", "not_arrived_count", "walkin_count"}
    assert set(data["cancellations"].keys()) == {"cancelled_count", "cancelled_but_paid_count"}
    assert set(data["guest_types"].keys()) == {"normal_count", "invited_count", "vendor_count", "staff_count"}
    assert set(data["payment"].keys()) == {"paid_count", "unpaid_count", "exempt_from_payment_count"}


def test_meetup_stats_returns_401_without_auth(client: TestClient, meetup):
    """Verify that an unauthenticated request is rejected."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_meetup_stats_returns_403_for_member_of_different_org(client: TestClient, session: Session):
    """Verify multi-tenant isolation for the stats endpoint."""
    org_a = make_org(session, name="Stats Org A", slug="stats-org-a")
    org_b = make_org(session, name="Stats Org B", slug="stats-org-b")
    member_a = make_user(session, username="member_a_stats")
    make_org_member(session, org=org_a, user=member_a, role=OrgRole.STAFF)
    headers_a = get_auth_headers(client, "member_a_stats", "a-very-secure-passphrase")

    meetup_b = make_meetup(
        session, org=org_b, name="Org B Stats Meetup", mazmo_meetup_url="https://mazmo.net/test/org-b-stats-1"
    )

    resp = client.get(f"/organizations/{org_b.id}/meetups/{meetup_b.id}/stats", headers=headers_a)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_meetup_stats_returns_404_for_nonexistent_meetup(
    client: TestClient, staff_headers: dict, org: Organization, org_staff_member
):
    """Verify that a non-existent meetup id returns 404."""
    resp = client.get(f"/organizations/{org.id}/meetups/{uuid.uuid4()}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_meetup_stats_returns_zero_counts_for_meetup_with_no_rsvps(
    client: TestClient, staff_headers: dict, meetup, org_staff_member
):
    """Verify a freshly created meetup with no RSVPs returns all zeros."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["attendance"] == {
        "total_rsvps": 0,
        "arrived_count": 0,
        "not_arrived_count": 0,
        "walkin_count": 0,
    }
    assert data["cancellations"] == {"cancelled_count": 0, "cancelled_but_paid_count": 0}
    assert data["guest_types"] == {
        "normal_count": 0,
        "invited_count": 0,
        "vendor_count": 0,
        "staff_count": 0,
    }
    assert data["payment"] == {"paid_count": 0, "unpaid_count": 0, "exempt_from_payment_count": 0}


def test_meetup_stats_counts_arrived_and_not_arrived_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify arrived_count and not_arrived_count split correctly."""
    arrived = make_guest(session, mazmo_user_id=530, mazmo_handle="arrived_guest")
    not_arrived = make_guest(session, mazmo_user_id=531, mazmo_handle="not_arrived_guest")
    make_rsvp(session, meetup=meetup, guest=arrived, has_arrived=True, arrival_order=1)
    make_rsvp(session, meetup=meetup, guest=not_arrived)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["attendance"]
    assert data["total_rsvps"] == 2
    assert data["arrived_count"] == 1
    assert data["not_arrived_count"] == 1


def test_meetup_stats_counts_walkins_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify walkin_count only counts is_walkin=True RSVPs."""
    walkin = make_guest(session, mazmo_user_id=532, mazmo_handle="walkin_stats_guest")
    rsvped = make_guest(session, mazmo_user_id=533, mazmo_handle="rsvped_stats_guest")
    rsvp = make_rsvp(session, meetup=meetup, guest=walkin)
    rsvp.is_walkin = True
    session.add(rsvp)
    session.flush()
    make_rsvp(session, meetup=meetup, guest=rsvped)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["attendance"]["walkin_count"] == 1


def test_meetup_stats_excludes_cancelled_from_attendance_totals(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify a cancelled RSVP does not count toward attendance.total_rsvps."""
    active = make_guest(session, mazmo_user_id=534, mazmo_handle="active_stats_guest")
    cancelled = make_guest(session, mazmo_user_id=535, mazmo_handle="cancelled_stats_guest")
    make_rsvp(session, meetup=meetup, guest=active)
    cancelled_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled)
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["attendance"]["total_rsvps"] == 1
    assert data["cancellations"]["cancelled_count"] == 1


def test_meetup_stats_counts_cancelled_but_paid_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify cancelled_but_paid_count only counts cancelled+has_paid RSVPs."""
    cancelled_paid = make_guest(session, mazmo_user_id=536, mazmo_handle="cancelled_paid_guest")
    cancelled_unpaid = make_guest(session, mazmo_user_id=537, mazmo_handle="cancelled_unpaid_guest")
    paid_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_paid, has_paid=True, paid_at=datetime.now(UTC))
    paid_rsvp.cancelled_rsvp = True
    session.add(paid_rsvp)
    unpaid_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_unpaid)
    unpaid_rsvp.cancelled_rsvp = True
    session.add(unpaid_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["cancellations"]
    assert data["cancelled_count"] == 2
    assert data["cancelled_but_paid_count"] == 1


def test_meetup_stats_counts_all_four_guest_types_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """Verify one guest of each type produces the expected 4 counters."""
    normal = make_guest(session, mazmo_user_id=538, mazmo_handle="stats_normal")
    invited = make_guest(session, mazmo_user_id=539, mazmo_handle="stats_invited")
    vendor = make_guest(session, mazmo_user_id=540, mazmo_handle="stats_vendor")
    staff = make_guest(session, mazmo_user_id=541, mazmo_handle="stats_staff")
    make_rsvp(session, meetup=meetup, guest=normal)
    make_rsvp(session, meetup=meetup, guest=invited, guest_type="INVITED")
    make_rsvp(session, meetup=meetup, guest=vendor, guest_type="VENDOR")
    make_rsvp(session, meetup=meetup, guest=staff, guest_type="STAFF")

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()["guest_types"]
    assert data == {"normal_count": 1, "invited_count": 1, "vendor_count": 1, "staff_count": 1}


def test_meetup_stats_counts_multiple_guests_per_type_correctly(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify 3 VENDOR guests produce vendor_count == 3, not just a
    presence-detecting count.
    """
    for i in range(3):
        guest = make_guest(session, mazmo_user_id=550 + i, mazmo_handle=f"vendor_multi_{i}")
        make_rsvp(session, meetup=meetup, guest=guest, guest_type="VENDOR")

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["guest_types"]["vendor_count"] == 3


def test_meetup_stats_paid_and_unpaid_scoped_to_normal_guest_type(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify a VENDOR guest with has_paid=True counts only toward
    exempt_from_payment_count, never paid_count or unpaid_count.

    WHY: This is the double-counting bug explicitly ruled out in the
    design - guest_types.normal_count must always equal
    payment.paid_count + payment.unpaid_count.
    """
    paid_vendor = make_guest(session, mazmo_user_id=560, mazmo_handle="paid_vendor_guest")
    make_rsvp(session, meetup=meetup, guest=paid_vendor, guest_type="VENDOR", has_paid=True, paid_at=datetime.now(UTC))

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["payment"]["paid_count"] == 0
    assert data["payment"]["unpaid_count"] == 0
    assert data["payment"]["exempt_from_payment_count"] == 1
    assert data["guest_types"]["normal_count"] == 0


def test_meetup_stats_invariants_hold_across_mixed_fixture(
    client: TestClient, staff_headers: dict, session: Session, meetup, org_staff_member
):
    """
    Verify the 3 documented invariants numerically against a fixture that
    mixes all 4 guest types, paid/unpaid, cancelled, and walk-ins:
      guest_types.normal_count == payment.paid_count + payment.unpaid_count
      attendance.total_rsvps == sum(guest_types.*)
      attendance.total_rsvps == sum(payment.*)
    """
    normal_paid = make_guest(session, mazmo_user_id=570, mazmo_handle="mix_normal_paid")
    normal_unpaid = make_guest(session, mazmo_user_id=571, mazmo_handle="mix_normal_unpaid")
    invited = make_guest(session, mazmo_user_id=572, mazmo_handle="mix_invited")
    vendor = make_guest(session, mazmo_user_id=573, mazmo_handle="mix_vendor")
    staff = make_guest(session, mazmo_user_id=574, mazmo_handle="mix_staff")
    cancelled_guest = make_guest(session, mazmo_user_id=575, mazmo_handle="mix_cancelled")
    walkin_guest = make_guest(session, mazmo_user_id=576, mazmo_handle="mix_walkin")

    make_rsvp(session, meetup=meetup, guest=normal_paid, has_paid=True, paid_at=datetime.now(UTC))
    make_rsvp(session, meetup=meetup, guest=normal_unpaid)
    make_rsvp(session, meetup=meetup, guest=invited, guest_type="INVITED")
    make_rsvp(session, meetup=meetup, guest=vendor, guest_type="VENDOR")
    make_rsvp(session, meetup=meetup, guest=staff, guest_type="STAFF")
    cancelled_rsvp = make_rsvp(session, meetup=meetup, guest=cancelled_guest)
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    walkin_rsvp = make_rsvp(session, meetup=meetup, guest=walkin_guest)
    walkin_rsvp.is_walkin = True
    session.add(walkin_rsvp)
    session.flush()

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    normal_count = data["guest_types"]["normal_count"]
    invited_count = data["guest_types"]["invited_count"]
    vendor_count = data["guest_types"]["vendor_count"]
    staff_count = data["guest_types"]["staff_count"]
    paid_count = data["payment"]["paid_count"]
    unpaid_count = data["payment"]["unpaid_count"]
    exempt_count = data["payment"]["exempt_from_payment_count"]
    total_rsvps = data["attendance"]["total_rsvps"]

    assert normal_count == paid_count + unpaid_count
    assert total_rsvps == normal_count + invited_count + vendor_count + staff_count
    assert total_rsvps == paid_count + unpaid_count + exempt_count
    # Concrete values for this fixture, not just the invariants:
    # normal_count is 3, not 2: walkin_guest's RSVP was created without an
    # explicit guest_type, so make_rsvp()'s default ("NORMAL") applies -
    # walk-in status and guest_type are independent axes.
    assert normal_count == 3
    assert paid_count == 1
    assert unpaid_count == 2
    assert invited_count == 1
    assert vendor_count == 1
    assert staff_count == 1
    assert total_rsvps == 6  # 7 RSVPs made, 1 cancelled -> 6 active


# -- End-to-end scenarios ---------------------------------------------------


def test_eros_scenario_invited_vendor_staff_and_normal_guests_end_to_end(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Replicate the real scenario that motivated this feature end to end:
    sync guests, enable payment, classify 3 exempt guests, check everyone
    in, mark the remaining NORMAL guest as paid, cancel one paid guest,
    register a walk-in, then verify stats and the audit trail.
    """
    meetup = make_meetup(session, org=org, name="Alter Eros", mazmo_meetup_url="https://mazmo.net/test/alter-eros-e2e")

    normal_guest = make_guest(session, mazmo_user_id=600, mazmo_handle="eros_normal")
    invited_guest = make_guest(session, mazmo_user_id=601, mazmo_handle="eros_invited")
    vendor_guest = make_guest(session, mazmo_user_id=602, mazmo_handle="eros_vendor")
    staff_guest = make_guest(session, mazmo_user_id=603, mazmo_handle="eros_staff")
    cancels_after_paying_guest = make_guest(session, mazmo_user_id=604, mazmo_handle="eros_cancels")
    walkin_guest = make_guest(session, mazmo_user_id=605, mazmo_handle="eros_walkin")

    make_rsvp(session, meetup=meetup, guest=normal_guest)
    make_rsvp(session, meetup=meetup, guest=invited_guest)
    make_rsvp(session, meetup=meetup, guest=vendor_guest)
    make_rsvp(session, meetup=meetup, guest=staff_guest)
    make_rsvp(session, meetup=meetup, guest=cancels_after_paying_guest)

    # 2. Admin enables requires_payment
    resp = client.patch(f"/organizations/{org.id}/meetups/{meetup.id}/enable-payment", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK

    # 3. Admin classifies invited/vendor/staff guests; normal_guest and
    #    cancels_after_paying_guest stay NORMAL.
    for guest, guest_type in (
        (invited_guest, "INVITED"),
        (vendor_guest, "VENDOR"),
        (staff_guest, "STAFF"),
    ):
        resp = client.patch(
            f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/type",
            headers=admin_headers,
            json={"guest_type": guest_type},
        )
        assert resp.status_code == status.HTTP_200_OK

    # 4. Staff checks in the 3 exempt guests without payment -> 200 each.
    for guest in (invited_guest, vendor_guest, staff_guest):
        resp = client.post(
            f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_200_OK

    # 5. Staff attempts check-in of a NORMAL guest without paying -> 409.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT

    # 6. Admin marks that guest as paid; retry check-in -> 200.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{normal_guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    # 7. A guest who already paid cancels their RSVP.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{cancels_after_paying_guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    cancelled_rsvp = session.exec(
        select(MeetupRsvp)
        .where(MeetupRsvp.meetup_id == meetup.id)
        .where(MeetupRsvp.guest_id == cancels_after_paying_guest.id)
    ).one()
    cancelled_rsvp.cancelled_rsvp = True
    session.add(cancelled_rsvp)
    session.flush()

    # 8. Staff registers a walk-in.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{walkin_guest.id}/add-walkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_201_CREATED

    # 9. GET .../stats - verify every field against the scenario above.
    resp = client.get(f"/organizations/{org.id}/meetups/{meetup.id}/stats", headers=staff_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    # 5 active RSVPs: normal, invited, vendor, staff, walkin (4 of the 5
    # originally-RSVPed guests stay active + 1 walk-in); the 5th original
    # RSVP (cancels_after_paying_guest) is cancelled and excluded here.
    assert data["attendance"]["total_rsvps"] == 5
    assert data["attendance"]["arrived_count"] == 4  # invited, vendor, staff, normal
    assert data["attendance"]["not_arrived_count"] == 1  # walkin guest not checked in yet
    assert data["attendance"]["walkin_count"] == 1

    assert data["cancellations"]["cancelled_count"] == 1
    assert data["cancellations"]["cancelled_but_paid_count"] == 1

    assert data["guest_types"] == {
        "normal_count": 2,  # normal_guest + walkin_guest (defaults to NORMAL)
        "invited_count": 1,
        "vendor_count": 1,
        "staff_count": 1,
    }

    assert data["payment"]["paid_count"] == 1  # normal_guest
    assert data["payment"]["unpaid_count"] == 1  # walkin_guest
    assert data["payment"]["exempt_from_payment_count"] == 3  # invited, vendor, staff

    # 10. GET .../events?type=GUEST_TYPE_CHANGED - verify 3 entries with
    #     the correct reason each.
    resp = client.get(
        f"/organizations/{org.id}/events/?type=GUEST_TYPE_CHANGED",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    events = resp.json()["events"]
    assert len(events) == 3
    reasons = {e["reason"] for e in events}
    assert reasons == {
        "Changed guest_type from NORMAL to INVITED",
        "Changed guest_type from NORMAL to VENDOR",
        "Changed guest_type from NORMAL to STAFF",
    }


def test_reclassify_after_checkin_does_not_affect_already_checked_in_guest(
    client: TestClient,
    admin_headers: dict,
    staff_headers: dict,
    session: Session,
    org: Organization,
    org_staff_member,
):
    """
    Verify that reclassifying a guest who already checked in as NORMAL
    (and paid) does not retroactively change their check-in state.
    """
    meetup = make_meetup(
        session,
        org=org,
        name="Alter Retroactive Reclassify",
        mazmo_meetup_url="https://mazmo.net/test/alter-retro-reclassify",
        requires_payment=True,
    )
    guest = make_guest(session, mazmo_user_id=610, mazmo_handle="retro_reclassify_guest")
    make_rsvp(session, meetup=meetup, guest=guest)

    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/payment",
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK

    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    checkin_data = resp.json()
    arrival_order = checkin_data["arrival_order"]
    arrival_time = checkin_data["arrival_time"]

    # Admin reclassifies the guest as VENDOR after the fact.
    resp = client.patch(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/type",
        headers=admin_headers,
        json={"guest_type": "VENDOR"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["rsvp"]["guest_type"] == "VENDOR"

    rsvp = session.exec(
        select(MeetupRsvp).where(MeetupRsvp.meetup_id == meetup.id).where(MeetupRsvp.guest_id == guest.id)
    ).one()
    assert rsvp.has_arrived is True
    assert rsvp.arrival_order == arrival_order
    assert datetime.fromisoformat(arrival_time.replace("Z", "+00:00")) == rsvp.arrival_time

    # A second check-in attempt is still correctly rejected as "already
    # checked in" (409), not silently re-processed because of the
    # reclassification.
    resp = client.post(
        f"/organizations/{org.id}/meetups/{meetup.id}/guests/{guest.id}/checkin",
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
