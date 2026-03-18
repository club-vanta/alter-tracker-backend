"""Tests for the /staff router."""

from fastapi import status

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import make_user
from app.models.models import PossibleRoles


# ── List all staff ────────────────────────────────────────────────────────────


def test_list_all_staff_returns_200_ok_with_all_accounts(
    client: TestClient, admin_headers: dict, session: Session
):
    make_user(session, username="extra1")
    make_user(session, username="extra2")
    resp = client.get("/staff/", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    usernames = [u["username"] for u in resp.json()]
    assert "extra1" in usernames
    assert "extra2" in usernames


def test_list_all_staff_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict
):
    resp = client.get("/staff/", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_list_all_staff_without_token_returns_401_unauthorized(client: TestClient):
    resp = client.get("/staff/")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── List pending ──────────────────────────────────────────────────────────────


def test_list_pending_returns_200_ok_with_only_unapproved_accounts(
    client: TestClient, admin_headers: dict, pending_user
):
    resp = client.get("/staff/pending", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert any(u["username"] == "pending" for u in data)
    assert all(u["is_approved"] is False for u in data)


def test_list_pending_is_empty_when_all_accounts_are_approved(
    client: TestClient, admin_headers: dict, admin_user, staff_user
):
    resp = client.get("/staff/pending", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


def test_list_pending_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict
):
    resp = client.get("/staff/pending", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Approve / revoke ──────────────────────────────────────────────────────────


def test_approving_pending_user_returns_200_ok_with_is_approved_true(
    client: TestClient, admin_headers: dict, pending_user
):
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
    resp = client.patch(
        f"/staff/{staff_user.id}/approve",
        json={"is_approved": False},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_approved"] is False


def test_admin_revoking_own_approval_returns_400_bad_request(
    client: TestClient, admin_headers: dict, admin_user
):
    resp = client.patch(
        f"/staff/{admin_user.id}/approve",
        json={"is_approved": False},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot revoke" in resp.json()["detail"].lower()


def test_approving_nonexistent_user_returns_404_not_found(
    client: TestClient, admin_headers: dict
):
    resp = client.patch(
        "/staff/99999/approve",
        json={"is_approved": True},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_approving_user_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict, pending_user
):
    resp = client.patch(
        f"/staff/{pending_user.id}/approve",
        json={"is_approved": True},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_approving_user_without_token_returns_401_unauthorized(
    client: TestClient, pending_user
):
    resp = client.patch(
        f"/staff/{pending_user.id}/approve",
        json={"is_approved": True},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Change role ───────────────────────────────────────────────────────────────


def test_promoting_staff_to_admin_returns_200_ok_with_updated_role(
    client: TestClient, admin_headers: dict, staff_user
):
    resp = client.patch(
        f"/staff/{staff_user.id}/role",
        json={"role": "ADMIN"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"]["name"] == "ADMIN"


def test_demoting_another_admin_to_staff_returns_200_ok_with_updated_role(
    client: TestClient, session: Session, admin_headers: dict
):
    other_admin = make_user(session, username="otheradmin", role=PossibleRoles.ADMIN)
    resp = client.patch(
        f"/staff/{other_admin.id}/role",
        json={"role": "STAFF"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"]["name"] == "STAFF"


def test_admin_demoting_themselves_returns_400_bad_request(
    client: TestClient, admin_headers: dict, admin_user
):
    resp = client.patch(
        f"/staff/{admin_user.id}/role",
        json={"role": "STAFF"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot demote" in resp.json()["detail"].lower()


def test_setting_role_to_unknown_value_returns_422_unprocessable_entity(
    client: TestClient, admin_headers: dict, staff_user
):
    resp = client.patch(
        f"/staff/{staff_user.id}/role",
        json={"role": "SUPERUSER"},
        headers=admin_headers,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_changing_role_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict, pending_user
):
    resp = client.patch(
        f"/staff/{pending_user.id}/role",
        json={"role": "ADMIN"},
        headers=staff_headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Delete ────────────────────────────────────────────────────────────────────


def test_deleting_staff_user_returns_204_no_content(
    client: TestClient, admin_headers: dict, session: Session
):
    target = make_user(session, username="tobedeleted")
    resp = client.delete(f"/staff/{target.id}", headers=admin_headers)
    assert resp.status_code == status.HTTP_204_NO_CONTENT


def test_admin_deleting_own_account_returns_400_bad_request(
    client: TestClient, admin_headers: dict, admin_user
):
    resp = client.delete(f"/staff/{admin_user.id}", headers=admin_headers)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot delete" in resp.json()["detail"].lower()


def test_deleting_nonexistent_user_returns_404_not_found(
    client: TestClient, admin_headers: dict
):
    resp = client.delete("/staff/99999", headers=admin_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_deleting_user_by_non_admin_returns_403_forbidden(
    client: TestClient, staff_headers: dict, pending_user
):
    resp = client.delete(f"/staff/{pending_user.id}", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_deleting_user_without_token_returns_401_unauthorized(
    client: TestClient, pending_user
):
    resp = client.delete(f"/staff/{pending_user.id}")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
