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
