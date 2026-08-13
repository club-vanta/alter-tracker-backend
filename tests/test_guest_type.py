"""
Tests for the guest_type feature: payment exemption categories per RSVP.

Covers: GuestType enum/schema validation, PATCH .../guests/{id}/type,
the check-in payment gate exemption, GET .../meetups/{id}/stats, and
end-to-end scenarios that chain multiple endpoints together.

Sync-specific guest_type regression tests live in test_sync.py.
Event-log filter regression test lives in test_events.py (TestEventFiltering).
"""

import pytest
from sqlmodel import Session

from app.models.models import GuestType, Organization, OrgRole, User
from tests.conftest import make_org_member


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
