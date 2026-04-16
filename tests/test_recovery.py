"""Tests for the password recovery flow.

Covers three endpoints:
  POST /staff/{user_id}/recovery-code   → admin generates a 6-digit code
  POST /auth/verify-recovery-code       → user checks the code without consuming it
  POST /auth/reset-password             → user resets their password with the code

The flow is intentionally out-of-band: an admin generates the code and communicates
it to the user (WhatsApp/Slack), the user then uses it to change their password.
"""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import make_user

# ── Helpers ───────────────────────────────────────────────────────────────────

_GENERIC_ERROR = "Código inválido o expirado."


def _generate_code(client: TestClient, user_id: int, headers: dict) -> str:
    """Generate a recovery code for a user and return it."""
    resp = client.post(f"/staff/{user_id}/recovery-code", headers=headers)
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["code"]


# ── POST /staff/{user_id}/recovery-code ──────────────────────────────────────


def test_admin_generates_recovery_code_returns_200_with_six_digit_code(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that admins get a 6-digit zero-padded code in the response.

    WHY: The code format (exactly 6 digits, possibly starting with 0) is a
    contract with the frontend and with the user who receives it out-of-band.
    """
    target = make_user(session, username="needscode")
    resp = client.post(f"/staff/{target.id}/recovery-code", headers=admin_headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["username"] == "needscode"
    code = data["code"]
    assert len(code) == 6
    assert code.isdigit()


def test_generating_code_stores_it_on_the_user_model(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify the generated code is persisted and the used flag is reset to False.

    WHY: The auth endpoints read recovery_code from the DB. If it's not
    stored correctly the entire flow breaks.
    """
    target = make_user(session, username="storedcode")
    code = _generate_code(client, target.id, admin_headers)

    session.refresh(target)
    assert target.recovery_code == code
    assert target.recovery_code_used is False
    assert target.recovery_code_created_at is not None


def test_generating_new_code_overwrites_previous_code(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that a second generation invalidates the first code.

    WHY: If the admin generates a code twice (e.g., user never received the
    first one), the old code must no longer work. Overwriting is the simplest
    way to enforce this.
    """
    target = make_user(session, username="twocodes")
    first_code = _generate_code(client, target.id, admin_headers)
    second_code = _generate_code(client, target.id, admin_headers)

    assert first_code != second_code or True  # codes could collide by chance, that's fine
    session.refresh(target)
    # Only the latest code is stored
    assert target.recovery_code == second_code

    # The first code is now invalid
    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "twocodes", "code": first_code},
    )
    # If both codes happen to be the same (1 in 1M chance), this will be 200 - acceptable
    if first_code != second_code:
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_generating_code_for_nonexistent_user_returns_404(client: TestClient, admin_headers: dict):
    """
    Verify that a 404 is returned when the user ID doesn't exist.

    WHY: Admins shouldn't get confusing errors when they mistype a user ID.
    """
    resp = client.post("/staff/99999/recovery-code", headers=admin_headers)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_generating_code_by_non_admin_returns_403(
    client: TestClient, session: Session, staff_headers: dict
):
    """
    Verify that regular staff cannot generate recovery codes.

    WHY: This endpoint gives the caller the ability to take over any user's
    account (indirectly), so it must be admin-only.
    """
    target = make_user(session, username="codeattempt")
    resp = client.post(f"/staff/{target.id}/recovery-code", headers=staff_headers)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_generating_code_without_auth_returns_401(client: TestClient, session: Session):
    """
    Verify that unauthenticated code generation is rejected.

    WHY: Anonymous callers must not be able to trigger account takeover flows.
    """
    target = make_user(session, username="anoncode")
    resp = client.post(f"/staff/{target.id}/recovery-code")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── POST /auth/verify-recovery-code ──────────────────────────────────────────


def test_verify_valid_code_returns_200_ok(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that a freshly generated, unused code is accepted.

    WHY: Core happy path - the user enters the code they received and gets
    a green light before proceeding to the password reset form.
    """
    target = make_user(session, username="verifyok")
    code = _generate_code(client, target.id, admin_headers)

    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "verifyok", "code": code},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["ok"] is True


def test_verify_does_not_consume_the_code(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that calling verify-recovery-code twice still works the second time.

    WHY: The user may close the browser tab after verifying and re-open the
    page. The code must remain valid so they don't have to ask the admin again.
    """
    target = make_user(session, username="verifytwoce")
    code = _generate_code(client, target.id, admin_headers)

    for _ in range(2):
        resp = client.post(
            "/auth/verify-recovery-code",
            json={"username": "verifytwoce", "code": code},
        )
        assert resp.status_code == status.HTTP_200_OK

    session.refresh(target)
    assert target.recovery_code_used is False


def test_verify_wrong_code_returns_400(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that an incorrect code is rejected.

    WHY: Basic security - a random 6-digit guess must not succeed.
    """
    target = make_user(session, username="wrongcode")
    code = _generate_code(client, target.id, admin_headers)
    bad_code = "000000" if code != "000000" else "111111"

    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "wrongcode", "code": bad_code},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_verify_expired_code_returns_400(client: TestClient, session: Session, admin_headers: dict):
    """
    Verify that a code older than 72 hours is rejected.

    WHY: Recovery codes must expire to limit the window of attack if the
    code leaks or the admin forgot to communicate it.
    """
    target = make_user(session, username="expiredcode")
    code = _generate_code(client, target.id, admin_headers)

    # Backdate the creation timestamp past the 72-hour window
    session.refresh(target)
    target.recovery_code_created_at = datetime.now(UTC) - timedelta(hours=73)
    session.add(target)
    session.flush()

    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "expiredcode", "code": code},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_verify_used_code_returns_400(client: TestClient, session: Session, admin_headers: dict):
    """
    Verify that a code that has already been used is rejected.

    WHY: One-time use. Once the password is reset, the code must not be
    reusable - otherwise anyone who saw the code could reset the password again.
    """
    target = make_user(session, username="usedcode")
    code = _generate_code(client, target.id, admin_headers)

    # Mark it as used directly (simulating a completed reset)
    session.refresh(target)
    target.recovery_code_used = True
    session.add(target)
    session.flush()

    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "usedcode", "code": code},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_verify_unknown_username_returns_same_400_as_bad_code(client: TestClient):
    """
    Verify that a nonexistent username returns the same generic error as a bad code.

    WHY: User enumeration prevention. An attacker must not be able to discover
    which usernames exist by trying recovery codes and observing different errors.
    """
    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "doesnotexist", "code": "123456"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_verify_no_code_generated_yet_returns_400(client: TestClient, session: Session):
    """
    Verify that a user with no recovery code set gets a 400.

    WHY: A freshly created user has recovery_code=None. Attempting to verify
    any code for them must fail cleanly.
    """
    make_user(session, username="nocodeyett")
    resp = client.post(
        "/auth/verify-recovery-code",
        json={"username": "nocodeyett", "code": "123456"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ── POST /auth/reset-password ─────────────────────────────────────────────────


def test_reset_password_with_valid_code_returns_200_with_username(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify the happy path: valid code → 200 with the username in the response.

    WHY: The frontend needs the username back to display the success message.
    """
    target = make_user(session, username="resetok")
    code = _generate_code(client, target.id, admin_headers)

    resp = client.post(
        "/auth/reset-password",
        json={"username": "resetok", "code": code, "new_password": "a-brand-new-passphrase"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["username"] == "resetok"


def test_reset_password_actually_changes_the_password(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that the user can log in with the new password after a reset.

    WHY: The whole point of the feature. If the new password isn't persisted
    correctly the flow is broken even though it returns 200.
    """
    target = make_user(session, username="newpwduser", password="old-passphrase-here")
    code = _generate_code(client, target.id, admin_headers)

    client.post(
        "/auth/reset-password",
        json={"username": "newpwduser", "code": code, "new_password": "the-new-passphrase-15"},
    )

    # Old password should no longer work
    resp_old = client.post("/auth/token", data={"username": "newpwduser", "password": "old-passphrase-here"})
    assert resp_old.status_code == status.HTTP_401_UNAUTHORIZED

    # New password should work
    resp_new = client.post("/auth/token", data={"username": "newpwduser", "password": "the-new-passphrase-15"})
    assert resp_new.status_code == status.HTTP_200_OK


def test_reset_password_marks_code_as_used(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that recovery_code_used is True after a successful reset.

    WHY: The used flag is what prevents the same code from being used again.
    """
    target = make_user(session, username="markedused")
    code = _generate_code(client, target.id, admin_headers)

    client.post(
        "/auth/reset-password",
        json={"username": "markedused", "code": code, "new_password": "new-secure-passphrase"},
    )

    session.refresh(target)
    assert target.recovery_code_used is True


def test_reset_password_code_cannot_be_reused(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that the same code cannot be used to reset the password twice.

    WHY: After a successful reset the code is marked as used. A second
    attempt (e.g., from another tab) must be rejected.
    """
    target = make_user(session, username="noreuse")
    code = _generate_code(client, target.id, admin_headers)

    client.post(
        "/auth/reset-password",
        json={"username": "noreuse", "code": code, "new_password": "first-new-passphrase"},
    )

    resp = client.post(
        "/auth/reset-password",
        json={"username": "noreuse", "code": code, "new_password": "second-new-passphrase"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_reset_password_with_wrong_code_returns_400(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that an incorrect code is rejected at the reset step.

    WHY: The reset endpoint has the same code validation as verify. Someone
    who bypasses the verify step and calls reset directly with a wrong code
    must still get an error.
    """
    target = make_user(session, username="resetwrong")
    code = _generate_code(client, target.id, admin_headers)
    bad_code = "000000" if code != "000000" else "111111"

    resp = client.post(
        "/auth/reset-password",
        json={"username": "resetwrong", "code": bad_code, "new_password": "a-new-secure-passphrase"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_reset_password_with_expired_code_returns_400(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that an expired code is rejected at the reset step.

    WHY: Same TTL check as verify - expiry applies to the actual reset too.
    """
    target = make_user(session, username="resetexpired")
    code = _generate_code(client, target.id, admin_headers)

    session.refresh(target)
    target.recovery_code_created_at = datetime.now(UTC) - timedelta(hours=73)
    session.add(target)
    session.flush()

    resp = client.post(
        "/auth/reset-password",
        json={"username": "resetexpired", "code": code, "new_password": "a-new-secure-passphrase"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_reset_password_unknown_username_returns_same_400_as_bad_code(client: TestClient):
    """
    Verify that a nonexistent username returns the same generic error as a bad code.

    WHY: User enumeration prevention - same policy as verify-recovery-code.
    """
    resp = client.post(
        "/auth/reset-password",
        json={"username": "doesnotexist", "code": "123456", "new_password": "a-new-secure-passphrase"},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"] == _GENERIC_ERROR


def test_reset_password_too_short_returns_422(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that passwords shorter than 15 characters are rejected.

    WHY: Minimum length is 15 chars, same policy as registration. Weak
    passwords must be rejected before the code is validated.
    """
    target = make_user(session, username="shortpwd")
    code = _generate_code(client, target.id, admin_headers)

    resp = client.post(
        "/auth/reset-password",
        json={"username": "shortpwd", "code": code, "new_password": "tooshort"},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_reset_password_too_long_returns_422(
    client: TestClient, session: Session, admin_headers: dict
):
    """
    Verify that passwords longer than 128 characters are rejected.

    WHY: Upper bound prevents potential DoS via bcrypt with very long inputs.
    """
    target = make_user(session, username="longpwd")
    code = _generate_code(client, target.id, admin_headers)

    resp = client.post(
        "/auth/reset-password",
        json={"username": "longpwd", "code": code, "new_password": "x" * 129},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
