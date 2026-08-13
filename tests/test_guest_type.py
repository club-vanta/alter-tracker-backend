"""
Tests for the guest_type feature: payment exemption categories per RSVP.

Covers: GuestType enum/schema validation, PATCH .../guests/{id}/type,
the check-in payment gate exemption, GET .../meetups/{id}/stats, and
end-to-end scenarios that chain multiple endpoints together.

Sync-specific guest_type regression tests live in test_sync.py.
Event-log filter regression test lives in test_events.py (TestEventFiltering).
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session

from app.models.models import GuestType, Organization, OrgRole, User
from app.schemas import GuestTypeUpdateRequest
from tests.conftest import make_guest, make_org_member, make_rsvp


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
