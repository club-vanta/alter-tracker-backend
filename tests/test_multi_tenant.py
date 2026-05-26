"""
Cross-org isolation tests.

Verifies that staff from org A cannot read, write, or enumerate resources
belonging to org B — even with a valid JWT.

Org layout used in all tests:
  org 1 ("test-org")   — default test org, all standard fixtures
  org 2 ("other-org")  — seeded in conftest.setup_test_database
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import PossibleRoles
from tests.conftest import get_auth_headers, make_ban, make_guest, make_meetup, make_user

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def org1_admin(session: Session):
    return make_user(session, username="admin_org1", role=PossibleRoles.ADMIN, org_id=1)


@pytest.fixture()
def org2_admin(session: Session):
    return make_user(session, username="admin_org2", role=PossibleRoles.ADMIN, org_id=2)


@pytest.fixture()
def org1_headers(client: TestClient, org1_admin):
    return get_auth_headers(client, "admin_org1", "a-very-secure-passphrase")


@pytest.fixture()
def org2_headers(client: TestClient, org2_admin):
    return get_auth_headers(client, "admin_org2", "a-very-secure-passphrase")


# ── Meetup isolation ──────────────────────────────────────────────────────────


def test_list_meetups_only_returns_own_org(client: TestClient, session: Session, org1_headers, org2_headers):
    make_meetup(session, name="Org1 Event", mazmo_meetup_url="https://mazmo.net/c/org1-event-1", org_id=1)
    make_meetup(session, name="Org2 Event", mazmo_meetup_url="https://mazmo.net/c/org2-event-1", org_id=2)

    resp1 = client.get("/meetups/", headers=org1_headers)
    assert resp1.status_code == 200
    names1 = [m["name"] for m in resp1.json()["meetups"]]
    assert "Org1 Event" in names1
    assert "Org2 Event" not in names1

    resp2 = client.get("/meetups/", headers=org2_headers)
    assert resp2.status_code == 200
    names2 = [m["name"] for m in resp2.json()["meetups"]]
    assert "Org2 Event" in names2
    assert "Org1 Event" not in names2


# ── Ban isolation ─────────────────────────────────────────────────────────────


def test_ban_in_org1_not_visible_in_org2(
    client: TestClient, session: Session, org1_admin, org2_admin, org1_headers, org2_headers
):
    guest = make_guest(session, mazmo_user_id=9001, username="sharedguest")
    make_ban(session, guest=guest, banned_by=org1_admin, reason="Bad behaviour in org1", org_id=1)

    # org1 sees the guest as banned
    resp1 = client.get(f"/guests/{guest.mazmo_user_id}", headers=org1_headers)
    assert resp1.status_code == 200
    assert resp1.json()["is_banned"] is True

    # org2 sees the same guest as NOT banned
    resp2 = client.get(f"/guests/{guest.mazmo_user_id}", headers=org2_headers)
    assert resp2.status_code == 200
    assert resp2.json()["is_banned"] is False


def test_banned_list_is_org_scoped(
    client: TestClient, session: Session, org1_admin, org2_admin, org1_headers, org2_headers
):
    guest1 = make_guest(session, mazmo_user_id=9002, username="bannedinguest1")
    guest2 = make_guest(session, mazmo_user_id=9003, username="bannedinguest2")
    make_ban(session, guest=guest1, banned_by=org1_admin, org_id=1)
    make_ban(session, guest=guest2, banned_by=org2_admin, org_id=2)

    resp1 = client.get("/guests/banned", headers=org1_headers)
    assert resp1.status_code == 200
    ids1 = [g["mazmo_user_id"] for g in resp1.json()["guests"]]
    assert 9002 in ids1
    assert 9003 not in ids1

    resp2 = client.get("/guests/banned", headers=org2_headers)
    assert resp2.status_code == 200
    ids2 = [g["mazmo_user_id"] for g in resp2.json()["guests"]]
    assert 9003 in ids2
    assert 9002 not in ids2


def test_org2_cannot_unban_org1_guest(client: TestClient, session: Session, org1_admin, org2_headers):
    guest = make_guest(session, mazmo_user_id=9004, username="org1bannedguest")
    make_ban(session, guest=guest, banned_by=org1_admin, org_id=1)

    # org2 admin tries to unban — guest is not banned in org2, so 409
    resp = client.patch(f"/guests/{guest.mazmo_user_id}/unban", headers=org2_headers)
    assert resp.status_code == 409


def test_ban_in_org1_does_not_affect_org2_meetup_guest_list(
    client: TestClient, session: Session, org1_admin, org2_admin, org1_headers, org2_headers
):
    from tests.conftest import make_rsvp

    guest = make_guest(session, mazmo_user_id=9005, username="crossorgguest")
    make_ban(session, guest=guest, banned_by=org1_admin, org_id=1)

    meetup2 = make_meetup(session, name="Org2 Meetup", mazmo_meetup_url="https://mazmo.net/c/org2-mt-1", org_id=2)
    make_rsvp(session, meetup=meetup2, guest=guest)

    resp = client.get(f"/meetups/{meetup2.id}/guests", headers=org2_headers)
    assert resp.status_code == 200
    guests = resp.json()["guests"]
    assert len(guests) == 1
    # org2 should see is_banned=False — the ban belongs to org1
    assert guests[0]["guest"]["is_banned"] is False
