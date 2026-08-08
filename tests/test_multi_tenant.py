"""
Cross-org isolation tests.

Verifies that no organization can read or modify data belonging to another org.
Each test creates two independent orgs (org_a, org_b) with their own members,
then asserts that operations scoped to one org cannot leak into or affect the other.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import Organization, OrgRole, User
from tests.conftest import (
    get_auth_headers,
    make_ban,
    make_guest,
    make_meetup,
    make_org,
    make_org_member,
    make_rsvp,
    make_user,
)

_DEFAULT_PASSWORD = "a-very-secure-passphrase"


def _setup_two_orgs(session: Session, client: TestClient) -> tuple[Organization, Organization, dict, dict]:
    """Create two orgs, one ADMIN member each, and return (org_a, org_b, headers_a, headers_b)."""
    org_a = make_org(session, name="Org A", slug="org-a")
    org_b = make_org(session, name="Org B", slug="org-b")
    user_a = make_user(session, username="user_a")
    user_b = make_user(session, username="user_b")
    make_org_member(session, org=org_a, user=user_a, role=OrgRole.ADMIN)
    make_org_member(session, org=org_b, user=user_b, role=OrgRole.ADMIN)
    headers_a = get_auth_headers(client, "user_a", _DEFAULT_PASSWORD)
    headers_b = get_auth_headers(client, "user_b", _DEFAULT_PASSWORD)
    return org_a, org_b, headers_a, headers_b


# -- Meetups ------------------------------------------------------------------


def test_meetup_list_only_returns_own_org_meetups(client: TestClient, session: Session):
    """
    Verify that GET /organizations/{org_id}/meetups/ only returns meetups from that org.

    WHY: The meetup query must filter by org_id. Without this, a member of one org
    could see all meetups across all orgs.
    """
    org_a, org_b, headers_a, _ = _setup_two_orgs(session, client)
    make_meetup(session, org=org_a, name="Meetup A", mazmo_meetup_url="https://mazmo.net/c/meetup-a-1")
    make_meetup(session, org=org_b, name="Meetup B", mazmo_meetup_url="https://mazmo.net/c/meetup-b-1")

    resp = client.get(f"/organizations/{org_a.id}/meetups/", headers=headers_a)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["total"] == 1
    assert data["meetups"][0]["name"] == "Meetup A"


def test_non_member_cannot_list_meetups_of_another_org(client: TestClient, session: Session):
    """
    Verify that a user with no membership in org A gets 403 when listing org A's meetups.

    WHY: get_org_member checks membership in the org specified in the URL. A member
    of org B has no implicit access to org A's resources.
    """
    org_a, _, _, headers_b = _setup_two_orgs(session, client)

    resp = client.get(f"/organizations/{org_a.id}/meetups/", headers=headers_b)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_checkin_in_wrong_org_meetup_returns_404(client: TestClient, session: Session):
    """
    Verify that checking in a guest using a meetup UUID that belongs to a different org returns 404.

    WHY: A user with access to org B should not be able to affect a meetup in org A
    simply by knowing the meetup UUID. The meetup lookup must validate org ownership.
    """
    org_a, org_b, _, headers_b = _setup_two_orgs(session, client)
    guest = make_guest(session, mazmo_user_id=1)
    meetup_a = make_meetup(session, org=org_a, mazmo_meetup_url="https://mazmo.net/c/checkin-a-1")
    make_rsvp(session, meetup=meetup_a, guest=guest)

    resp = client.post(
        f"/organizations/{org_b.id}/meetups/{meetup_a.id}/guests/{guest.id}/checkin",
        headers=headers_b,
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


# -- Bans ---------------------------------------------------------------------


def test_is_banned_is_false_in_org_b_meetup_when_banned_in_org_a(client: TestClient, session: Session):
    """
    Verify that banning a guest in org A does not set is_banned=true in org B's meetup guest list.

    WHY: is_banned in GuestWithBanPublic must be resolved per-org. A ban in org A
    must not prevent the guest from attending events in org B.
    """
    org_a, org_b, _, headers_b = _setup_two_orgs(session, client)
    user_a = make_user(session, username="banner")
    guest = make_guest(session, mazmo_user_id=10)
    make_ban(session, org=org_a, guest=guest, banned_by=user_a)
    meetup_b = make_meetup(session, org=org_b, mazmo_meetup_url="https://mazmo.net/c/ban-scope-b-1")
    make_rsvp(session, meetup=meetup_b, guest=guest)

    resp = client.get(
        f"/organizations/{org_b.id}/meetups/{meetup_b.id}/guests",
        headers=headers_b,
    )

    assert resp.status_code == status.HTTP_200_OK
    guest_data = resp.json()["guests"][0]["guest"]
    assert guest_data["is_banned"] is False


def test_cross_org_unban_returns_409(client: TestClient, session: Session):
    """
    Verify that trying to unban a guest in org B when they are only banned in org A returns 409.

    WHY: The unban endpoint looks up the ban record scoped to the org in the URL.
    There is no ban record in org B, so the request must fail with 409 (not banned here).
    """
    org_a, org_b, _, headers_b = _setup_two_orgs(session, client)
    user_a = make_user(session, username="banner2")
    guest = make_guest(session, mazmo_user_id=20)
    make_ban(session, org=org_a, guest=guest, banned_by=user_a)

    resp = client.patch(
        f"/organizations/{org_b.id}/guests/{guest.id}/unban",
        headers=headers_b,
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_non_member_cannot_ban_in_another_org(client: TestClient, session: Session):
    """
    Verify that a user with no membership in org A cannot ban a guest in org A.

    WHY: get_org_admin requires ADMIN role in the specific org or SITE_ADMIN globally.
    Membership in org B confers no privileges in org A.
    """
    org_a, _, _, headers_b = _setup_two_orgs(session, client)
    guest = make_guest(session, mazmo_user_id=30)

    resp = client.patch(
        f"/organizations/{org_a.id}/guests/{guest.id}/ban",
        json={"reason": "Attempting cross-org ban"},
        headers=headers_b,
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


# -- Events -------------------------------------------------------------------


def test_events_are_scoped_to_org(client: TestClient, session: Session):
    """
    Verify that events created in org A do not appear in org B's event list.

    WHY: EventLog rows have an org_id column and the events endpoint filters by
    the org_id in the URL. Events must not leak across org boundaries.
    """
    org_a, org_b, headers_a, headers_b = _setup_two_orgs(session, client)
    guest = make_guest(session, mazmo_user_id=40)
    meetup_a = make_meetup(session, org=org_a, mazmo_meetup_url="https://mazmo.net/c/events-scope-a-1")
    make_rsvp(session, meetup=meetup_a, guest=guest)

    client.post(
        f"/organizations/{org_a.id}/meetups/{meetup_a.id}/guests/{guest.id}/checkin",
        headers=headers_a,
    )

    resp = client.get(f"/organizations/{org_b.id}/events/", headers=headers_b)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 0


# -- SITE_ADMIN ---------------------------------------------------------------


def test_site_admin_can_access_meetups_in_any_org(
    client: TestClient,
    session: Session,
    admin_user: User,
    admin_headers: dict,
):
    """
    Verify that SITE_ADMIN can GET meetups from any org without being a member.

    WHY: SITE_ADMIN bypasses all org membership checks. This is the escape hatch
    for cross-org administration.
    """
    org_a = make_org(session, name="Admin Org A", slug="admin-org-a")
    org_b = make_org(session, name="Admin Org B", slug="admin-org-b")
    make_meetup(session, org=org_a, mazmo_meetup_url="https://mazmo.net/c/admin-a-1")
    make_meetup(session, org=org_b, mazmo_meetup_url="https://mazmo.net/c/admin-b-1")

    resp_a = client.get(f"/organizations/{org_a.id}/meetups/", headers=admin_headers)
    resp_b = client.get(f"/organizations/{org_b.id}/meetups/", headers=admin_headers)

    assert resp_a.status_code == status.HTTP_200_OK
    assert resp_a.json()["total"] == 1
    assert resp_b.status_code == status.HTTP_200_OK
    assert resp_b.json()["total"] == 1
