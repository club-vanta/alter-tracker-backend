"""Tests for the /staff router.

These tests verify site-admin-only staff management operations: listing users,
approving registrations, changing roles, and disabling/enabling accounts.
All these endpoints require SITE_ADMIN role - regular users cannot manage other users.
"""

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import PossibleRoles
from tests.conftest import make_user

# ── List all staff ────────────────────────────────────────────────────────────


def test_list_all_staff_returns_200_ok_with_all_accounts(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that site admins can list all staff accounts.

    WHY: Site admins need to see all users to manage the system - approve new
    registrations, check who has access, etc.
    """
    make_user(session, username="extra1")
    make_user(session, username="extra2")
    resp = client.get("/staff/", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    usernames = [u["username"] for u in resp.json()]
    assert "extra1" in usernames
    assert "extra2" in usernames


def test_list_all_staff_by_non_admin_returns_403_forbidden(client: TestClient, staff_headers: dict):
    """
    Verify that regular users cannot list all users.

    WHY: User management is site-admin-only. Regular users shouldn't see other
    users' accounts - this could leak information about who works events.
    """
    resp = client.get("/staff/", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_list_all_staff_without_token_returns_401_unauthorized(client: TestClient):
    """
    Verify that unauthenticated requests are rejected.

    WHY: All staff endpoints require authentication. Anonymous users
    should not be able to enumerate staff accounts.
    """
    resp = client.get("/staff/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── List pending ──────────────────────────────────────────────────────────────


def test_list_pending_returns_200_ok_with_only_unapproved_accounts(
    client: TestClient, admin_headers: dict, pending_user
):
    """
    Verify that /pending returns only unapproved accounts.

    WHY: Site admins need a filtered view of users awaiting approval. This
    endpoint makes it easy to see who needs attention without scrolling
    through all users.
    """
    resp = client.get("/staff/pending", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert any(u["username"] == "pending" for u in data)
    assert all(u["is_approved"] is False for u in data)


def test_list_pending_is_empty_when_all_accounts_are_approved(
    client: TestClient, admin_headers: dict, admin_user, staff_user
):
    """
    Verify that /pending returns empty list when everyone is approved.

    WHY: Edge case - the endpoint should return an empty list, not error,
    when there's no pending work.
    """
    resp = client.get("/staff/pending", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


def test_list_pending_by_non_admin_returns_403_forbidden(client: TestClient, staff_headers: dict):
    """
    Verify that regular users cannot see pending users.

    WHY: Approving users is a site-admin function. Users shouldn't see who
    else is trying to register.
    """
    resp = client.get("/staff/pending", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Approve / revoke ──────────────────────────────────────────────────────────


def test_approving_pending_user_returns_200_ok_with_is_approved_true(
    client: TestClient, admin_headers: dict, pending_user
):
    """
    Verify that site admins can approve pending registrations.

    WHY: Core admin workflow - new users register, site admin reviews and
    approves. This enables the user to log in.
    """
    resp = client.patch(
        f"/staff/{pending_user.id}/approve",
        json={"is_approved": True},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_approved"] is True


def test_revoking_approval_of_staff_user_returns_200_ok_with_is_approved_false(
    client: TestClient, admin_headers: dict, staff_user
):
    """
    Verify that site admins can revoke access from approved users.

    WHY: If someone leaves or their access needs to be suspended, site admins
    must be able to revoke approval. This blocks their login.
    """
    resp = client.patch(
        f"/staff/{staff_user.id}/approve",
        json={"is_approved": False},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_approved"] is False


def test_admin_revoking_own_approval_returns_400_bad_request(client: TestClient, admin_headers: dict, admin_user):
    """
    Verify that site admins cannot revoke their own approval.

    WHY: Self-lockout protection. If a site admin could revoke themselves,
    they might accidentally lock themselves out with no way to recover.
    """
    resp = client.patch(
        f"/staff/{admin_user.id}/approve",
        json={"is_approved": False},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "site admins cannot revoke their own approval" in resp.json()["detail"].lower()


def test_approving_nonexistent_user_returns_404_not_found(client: TestClient, admin_headers: dict):
    """
    Verify that approving a nonexistent user returns 404.

    WHY: Clear error handling. If the user ID doesn't exist (maybe they
    were already deleted), return 404 instead of a confusing error.
    """
    resp = client.patch(
        "/staff/99999/approve",
        json={"is_approved": True},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_approving_user_by_non_admin_returns_403_forbidden(client: TestClient, staff_headers: dict, pending_user):
    """
    Verify that regular users cannot approve other users.

    WHY: Privilege escalation prevention. If users could approve others,
    they could approve their friends without site admin oversight.
    """
    resp = client.patch(
        f"/staff/{pending_user.id}/approve",
        json={"is_approved": True},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_approving_user_without_token_returns_401_unauthorized(client: TestClient, pending_user):
    """
    Verify that unauthenticated approval attempts are rejected.

    WHY: All admin operations require authentication. Anonymous users
    cannot modify user accounts.
    """
    resp = client.patch(
        f"/staff/{pending_user.id}/approve",
        json={"is_approved": True},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Change role ───────────────────────────────────────────────────────────────


def test_promoting_user_to_site_admin_returns_200_ok_with_updated_role(
    client: TestClient, admin_headers: dict, staff_user
):
    """
    Verify that site admins can promote users to site admin role.

    WHY: Role management workflow. Existing site admins can create new site
    admins to share the administrative workload.
    """
    resp = client.patch(
        f"/staff/{staff_user.id}/role",
        json={"role": "SITE_ADMIN"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"]["name"] == "SITE_ADMIN"


def test_demoting_another_admin_to_user_returns_200_ok_with_updated_role(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that site admins can demote other site admins to user.

    WHY: If someone no longer needs site admin privileges, they can be demoted.
    This follows the principle of least privilege.
    """
    other_admin = make_user(session, username="otheradmin", role=PossibleRoles.SITE_ADMIN)
    resp = client.patch(
        f"/staff/{other_admin.id}/role",
        json={"role": "USER"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"]["name"] == "USER"


def test_admin_demoting_themselves_returns_400_bad_request(client: TestClient, admin_headers: dict, admin_user):
    """
    Verify that site admins cannot demote themselves.

    WHY: Self-lockout protection. If the only site admin demoted themselves,
    there would be no one left to manage the system.
    """
    resp = client.patch(
        f"/staff/{admin_user.id}/role",
        json={"role": "USER"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "site admins cannot demote themselves" in resp.json()["detail"].lower()


def test_setting_role_to_unknown_value_returns_422_unprocessable_entity(
    client: TestClient, admin_headers: dict, staff_user
):
    """
    Verify that invalid role names are rejected.

    WHY: Input validation. Only USER and SITE_ADMIN are valid roles. Trying
    to set "SUPERUSER", "ADMIN", "STAFF", or other invalid roles should fail.
    """
    resp = client.patch(
        f"/staff/{staff_user.id}/role",
        json={"role": "SUPERUSER"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_changing_role_by_non_admin_returns_403_forbidden(client: TestClient, staff_headers: dict, pending_user):
    """
    Verify that regular users cannot change roles.

    WHY: Privilege escalation prevention. Users shouldn't be able to
    promote themselves or others to site admin.
    """
    resp = client.patch(
        f"/staff/{pending_user.id}/role",
        json={"role": "SITE_ADMIN"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Disable ───────────────────────────────────────────────────────────────────


def test_disabling_staff_user_returns_200_ok_with_audit_trail(
    client: TestClient, admin_headers: dict, session: Session, admin_user
):
    """
    Verify that site admins can disable user accounts with a reason.

    WHY: Soft-delete preserves audit trails. When someone leaves or needs
    to be suspended, we disable their account instead of deleting it.
    This keeps historical data like "who checked in this guest?" intact.
    """
    target = make_user(session, username="tobedisabled")
    resp = client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Left the organization"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_disabled"] is True
    assert data["disabled_reason"] == "Left the organization"
    assert data["disabled_at"] is not None


def test_admin_disabling_themselves_returns_400_bad_request(client: TestClient, admin_headers: dict, admin_user):
    """
    Verify that site admins cannot disable their own account.

    WHY: Self-lockout protection. If the only site admin disabled themselves,
    the system would become unmanageable.
    """
    resp = client.patch(
        f"/staff/{admin_user.id}/disable",
        json={"reason": "Testing self-disable"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot disable" in resp.json()["detail"].lower()


def test_disabling_already_disabled_user_returns_409_conflict(
    client: TestClient, admin_headers: dict, session: Session
):
    """
    Verify that disabling an already-disabled user returns 409.

    WHY: Idempotency check. If the user is already disabled, we return
    a conflict rather than silently succeeding.
    """
    target = make_user(session, username="alreadydisabled")
    # First disable
    client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "First disable"},
        headers=admin_headers,
    )
    # Try to disable again
    resp = client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Second disable"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "already disabled" in resp.json()["detail"].lower()


def test_disabling_nonexistent_user_returns_404_not_found(client: TestClient, admin_headers: dict):
    """
    Verify that disabling a nonexistent user returns 404.

    WHY: Clear error handling. If the user ID doesn't exist, return 404
    not a confusing error.
    """
    resp = client.patch(
        "/staff/99999/disable",
        json={"reason": "Testing nonexistent"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_disabling_user_by_non_admin_returns_403_forbidden(client: TestClient, staff_headers: dict, pending_user):
    """
    Verify that regular users cannot disable accounts.

    WHY: Only site admins can disable users. Users shouldn't be able to
    suspend other users.
    """
    resp = client.patch(
        f"/staff/{pending_user.id}/disable",
        json={"reason": "Testing unauthorized"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_disable_requires_reason(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that disabling a user requires a reason.

    WHY: Audit trail requirement. We need to know why someone was disabled
    for accountability and potential re-enabling decisions.
    """
    target = make_user(session, username="needsreason")
    # Missing reason field
    resp = client.patch(
        f"/staff/{target.id}/disable",
        json={},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_disable_reason_too_short_returns_422_unprocessable_entity(
    client: TestClient, admin_headers: dict, session: Session
):
    """
    Verify that disable reason must be at least 5 characters.

    WHY: Enforce meaningful reasons. "ok" is not a useful audit trail.
    """
    target = make_user(session, username="shortreason")
    resp = client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "abc"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_disabling_user_without_token_returns_401_unauthorized(client: TestClient, session: Session):
    """
    Verify that unauthenticated disable attempts are rejected.

    WHY: All admin operations require authentication.
    """
    target = make_user(session, username="notoken")
    resp = client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Testing unauthorized"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Enable ────────────────────────────────────────────────────────────────────


def test_enabling_disabled_user_returns_200_ok(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that site admins can re-enable disabled user accounts.

    WHY: Reversible action. If someone returns or was disabled by mistake,
    site admins can restore their access.
    """
    target = make_user(session, username="toreenable")
    # First disable
    client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Temporary suspension"},
        headers=admin_headers,
    )
    # Then enable
    resp = client.patch(f"/staff/{target.id}/enable", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["is_disabled"] is False
    assert data["disabled_at"] is None
    assert data["disabled_reason"] is None


def test_enabling_already_enabled_user_returns_409_conflict(client: TestClient, admin_headers: dict, session: Session):
    """
    Verify that enabling an already-enabled user returns 409.

    WHY: Idempotency check. If the user is already enabled, we return
    a conflict rather than silently succeeding.
    """
    target = make_user(session, username="alreadyenabled")
    resp = client.patch(f"/staff/{target.id}/enable", headers=admin_headers)
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "not disabled" in resp.json()["detail"].lower()


def test_enabling_user_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict, session: Session, admin_headers: dict
):
    """
    Verify that regular users cannot enable accounts.

    WHY: Only site admins can enable users. Users shouldn't be able to
    restore suspended accounts.
    """
    target = make_user(session, username="staffcantreenable")
    # Admin disables
    client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # User tries to enable
    resp = client.patch(f"/staff/{target.id}/enable", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_enabling_nonexistent_user_returns_404_not_found(client: TestClient, admin_headers: dict):
    """
    Verify that enabling a nonexistent user returns 404.

    WHY: Clear error handling.
    """
    resp = client.patch("/staff/99999/enable", headers=admin_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_enabling_user_without_token_returns_401_unauthorized(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that unauthenticated enable attempts are rejected.

    WHY: All admin operations require authentication.
    """
    target = make_user(session, username="enablenotoken")
    # Admin disables
    client.patch(
        f"/staff/{target.id}/disable",
        json={"reason": "Testing"},
        headers=admin_headers,
    )
    # Unauthenticated tries to enable
    resp = client.patch(f"/staff/{target.id}/enable")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
