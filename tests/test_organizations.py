"""Tests for the /organizations router CRUD endpoints.

Covers org creation, listing, retrieval, and member management.
Ban/unban/list-banned are already covered in test_guests.py.
"""

import uuid

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import OrgRole, User
from tests.conftest import get_auth_headers, make_org, make_org_member, make_user

# ======================================================================
# POST /organizations/
# ======================================================================


class TestCreateOrganization:
    """Tests for POST /organizations/."""

    def test_create_org_as_site_admin_returns_201(self, client: TestClient, admin_headers: dict):
        """
        SITE_ADMIN can create a new organization.

        WHY: Creating orgs is a privileged operation that should
        only be available to site administrators.
        """
        resp = client.post(
            "/organizations/",
            json={"name": "New Org", "slug": "new-org"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data["name"] == "New Org"
        assert data["slug"] == "new-org"
        assert "id" in data

    def test_create_org_without_token_returns_401(self, client: TestClient):
        """Unauthenticated requests are rejected."""
        resp = client.post("/organizations/", json={"name": "Org", "slug": "org"})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_org_as_regular_user_returns_403(self, client: TestClient, staff_headers: dict):
        """Regular users cannot create organizations."""
        resp = client.post(
            "/organizations/",
            json={"name": "Org", "slug": "org"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_create_org_duplicate_name_returns_409(self, client: TestClient, admin_headers: dict, session: Session):
        """
        Duplicate organization name returns 409 Conflict.

        WHY: Organization names must be unique to avoid confusion.
        """
        make_org(session, name="Existing Org", slug="existing-org")

        resp = client.post(
            "/organizations/",
            json={"name": "Existing Org", "slug": "other-slug"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "already taken" in resp.json()["detail"]

    def test_create_org_duplicate_slug_returns_409(self, client: TestClient, admin_headers: dict, session: Session):
        """
        Duplicate organization slug returns 409 Conflict.

        WHY: Slugs are used in URLs and must be globally unique.
        """
        make_org(session, name="First Org", slug="my-slug")

        resp = client.post(
            "/organizations/",
            json={"name": "Second Org", "slug": "my-slug"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "already taken" in resp.json()["detail"]

    def test_create_org_missing_name_returns_422(self, client: TestClient, admin_headers: dict):
        """Missing required fields return 422."""
        resp = client.post(
            "/organizations/",
            json={"slug": "no-name"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ======================================================================
# GET /organizations/
# ======================================================================


class TestListOrganizations:
    """Tests for GET /organizations/."""

    def test_list_orgs_as_site_admin_returns_200(self, client: TestClient, admin_headers: dict, session: Session):
        """
        SITE_ADMIN can list all organizations.

        WHY: Admins need an overview of all orgs to manage the system.
        """
        make_org(session, name="Org A", slug="org-a")
        make_org(session, name="Org B", slug="org-b")

        resp = client.get("/organizations/", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] >= 2
        assert "organizations" in data

    def test_list_orgs_without_token_returns_401(self, client: TestClient):
        """Unauthenticated requests are rejected."""
        resp = client.get("/organizations/")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_orgs_as_regular_user_returns_403(self, client: TestClient, staff_headers: dict):
        """Regular users cannot list all organizations."""
        resp = client.get("/organizations/", headers=staff_headers)
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ======================================================================
# GET /organizations/{org_id}
# ======================================================================


class TestGetOrganization:
    """Tests for GET /organizations/{org_id}."""

    def test_get_org_as_site_admin_returns_200(self, client: TestClient, admin_headers: dict, session: Session):
        """SITE_ADMIN can get any org."""
        org = make_org(session, name="Target Org", slug="target-org")

        resp = client.get(f"/organizations/{org.id}", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["name"] == "Target Org"
        assert data["slug"] == "target-org"

    def test_get_org_as_member_returns_200(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user: User
    ):
        """
        Org members can get their org.

        WHY: Members need to see org details but shouldn't
        be able to list all orgs.
        """
        org = make_org(session, name="Member Org", slug="member-org")
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.get(f"/organizations/{org.id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_200_OK

    def test_get_org_as_non_member_returns_403(self, client: TestClient, staff_headers: dict, session: Session):
        """Non-members cannot view an org."""
        org = make_org(session, name="Private Org", slug="private-org")

        resp = client.get(f"/organizations/{org.id}", headers=staff_headers)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_get_nonexistent_org_returns_404(self, client: TestClient, admin_headers: dict):
        """Nonexistent org returns 404."""
        fake_id = uuid.uuid4()
        resp = client.get(f"/organizations/{fake_id}", headers=admin_headers)
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_get_org_without_token_returns_401(self, client: TestClient, session: Session):
        """Unauthenticated requests are rejected."""
        org = make_org(session)
        resp = client.get(f"/organizations/{org.id}")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ======================================================================
# GET /organizations/{org_id}/members
# ======================================================================


class TestListOrgMembers:
    """Tests for GET /organizations/{org_id}/members."""

    def test_list_members_as_site_admin_returns_200(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        SITE_ADMIN can list org members.

        WHY: Site admins need global visibility for access control management.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.get(f"/organizations/{org.id}/members", headers=admin_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 1
        assert "members" in data

    def test_list_members_as_org_admin_returns_200(self, client: TestClient, session: Session, staff_user: User):
        """
        Org ADMIN can list members of their own org.

        WHY: Org admins need to see who is in their org to manage access,
        without requiring site-wide admin privileges.
        """
        org = make_org(session)
        org_admin_user = make_user(session, username="org_admin_user")
        make_org_member(session, org=org, user=org_admin_user, role=OrgRole.ADMIN)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.get(
            f"/organizations/{org.id}/members",
            headers=get_auth_headers(client, org_admin_user.username, "a-very-secure-passphrase"),
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 2

    def test_list_members_as_org_staff_returns_403(self, client: TestClient, session: Session, staff_user: User):
        """
        Org STAFF member cannot list org members - only ADMIN or SITE_ADMIN can.

        WHY: Prevents regular members from enumerating org membership.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.get(
            f"/organizations/{org.id}/members",
            headers=get_auth_headers(client, staff_user.username, "a-very-secure-passphrase"),
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_list_members_nonexistent_org_returns_404(self, client: TestClient, admin_headers: dict):
        """Nonexistent org returns 404 when listing members."""
        fake_id = uuid.uuid4()
        resp = client.get(f"/organizations/{fake_id}/members", headers=admin_headers)
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_list_members_as_non_member_returns_403(self, client: TestClient, staff_headers: dict, session: Session):
        """Users with no membership in the org cannot list members."""
        org = make_org(session)
        resp = client.get(f"/organizations/{org.id}/members", headers=staff_headers)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_list_members_without_token_returns_401(self, client: TestClient, session: Session):
        """Unauthenticated requests are rejected."""
        org = make_org(session)
        resp = client.get(f"/organizations/{org.id}/members")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ======================================================================
# POST /organizations/{org_id}/members/{user_id}
# ======================================================================


class TestAddOrgMember:
    """Tests for POST /organizations/{org_id}/members/{user_id}."""

    def test_add_member_as_site_admin_returns_201(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        SITE_ADMIN can add a user to an org.

        WHY: Only site admins should be able to grant org access -
        this prevents privilege escalation.
        """
        org = make_org(session)

        resp = client.post(
            f"/organizations/{org.id}/members/{staff_user.id}",
            json={"role": "STAFF"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data["user_id"] == staff_user.id
        assert data["role"] == "STAFF"

    def test_add_member_as_admin_returns_201(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """Can add an org ADMIN role member."""
        org = make_org(session)

        resp = client.post(
            f"/organizations/{org.id}/members/{staff_user.id}",
            json={"role": "ADMIN"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["role"] == "ADMIN"

    def test_add_member_nonexistent_org_returns_404(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """Adding to a nonexistent org returns 404."""
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/organizations/{fake_id}/members/{staff_user.id}",
            json={"role": "STAFF"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_add_member_nonexistent_user_returns_404(self, client: TestClient, admin_headers: dict, session: Session):
        """Adding a nonexistent user returns 404."""
        org = make_org(session)
        resp = client.post(
            f"/organizations/{org.id}/members/99999",
            json={"role": "STAFF"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_add_duplicate_member_returns_409(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        Adding an existing member returns 409 Conflict.

        WHY: A user can only have one role per org. If they need a
        different role, use PATCH to update it.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.post(
            f"/organizations/{org.id}/members/{staff_user.id}",
            json={"role": "ADMIN"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "already a member" in resp.json()["detail"]

    def test_add_member_as_regular_user_returns_403(self, client: TestClient, staff_headers: dict, session: Session):
        """Regular users cannot add org members."""
        org = make_org(session)
        resp = client.post(
            f"/organizations/{org.id}/members/1",
            json={"role": "STAFF"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ======================================================================
# PATCH /organizations/{org_id}/members/{user_id}
# ======================================================================


class TestUpdateOrgMemberRole:
    """Tests for PATCH /organizations/{org_id}/members/{user_id}."""

    def test_update_member_role_returns_200(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        SITE_ADMIN can change a member's org role.

        WHY: Users may be promoted to org admin or demoted back to staff.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.patch(
            f"/organizations/{org.id}/members/{staff_user.id}",
            json={"role": "ADMIN"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["role"] == "ADMIN"

    def test_update_role_nonexistent_member_returns_404(
        self, client: TestClient, admin_headers: dict, session: Session
    ):
        """Updating a non-member's role returns 404."""
        org = make_org(session)
        resp = client.patch(
            f"/organizations/{org.id}/members/99999",
            json={"role": "ADMIN"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_update_role_as_regular_user_returns_403(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user: User
    ):
        """Regular users cannot change org roles."""
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        new_user = make_user(session, username="other")
        make_org_member(session, org=org, user=new_user, role=OrgRole.STAFF)

        resp = client.patch(
            f"/organizations/{org.id}/members/{new_user.id}",
            json={"role": "ADMIN"},
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ======================================================================
# DELETE /organizations/{org_id}/members/{user_id}
# ======================================================================


class TestRemoveOrgMember:
    """Tests for DELETE /organizations/{org_id}/members/{user_id}."""

    def test_remove_member_as_site_admin_returns_204(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        SITE_ADMIN can remove a user from an org.

        WHY: Admins need to revoke org access when a user leaves or
        their role changes.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.delete(
            f"/organizations/{org.id}/members/{staff_user.id}",
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_remove_nonexistent_member_returns_404(self, client: TestClient, admin_headers: dict, session: Session):
        """Removing a non-member returns 404."""
        org = make_org(session)
        resp = client.delete(
            f"/organizations/{org.id}/members/99999",
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_remove_member_as_regular_user_returns_403(
        self, client: TestClient, staff_headers: dict, session: Session, staff_user: User
    ):
        """Regular users cannot remove org members."""
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.delete(
            f"/organizations/{org.id}/members/{staff_user.id}",
            headers=staff_headers,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_remove_member_without_token_returns_401(self, client: TestClient, session: Session, staff_user: User):
        """Unauthenticated requests are rejected."""
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        resp = client.delete(f"/organizations/{org.id}/members/{staff_user.id}")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_remove_and_readd_member_works(
        self, client: TestClient, admin_headers: dict, session: Session, staff_user: User
    ):
        """
        A removed member can be re-added.

        WHY: The delete should fully remove the membership row,
        allowing a fresh add without 409 conflict.
        """
        org = make_org(session)
        make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)

        # Remove
        client.delete(
            f"/organizations/{org.id}/members/{staff_user.id}",
            headers=admin_headers,
        )

        # Re-add
        resp = client.post(
            f"/organizations/{org.id}/members/{staff_user.id}",
            json={"role": "ADMIN"},
            headers=admin_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["role"] == "ADMIN"
