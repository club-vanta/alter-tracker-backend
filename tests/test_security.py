"""Unit tests for security utilities (no DB, no HTTP).

These tests verify the cryptographic primitives used for authentication:
password hashing and JWT token handling. These are critical security functions
that must work correctly - bugs here could compromise the entire system.
"""

from datetime import timedelta

from app.core.security import (
    JWTPayload,
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from app.models.models import PossibleRoles

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_payload(username: str = "testuser") -> JWTPayload:
    """Create a valid JWTPayload for testing."""
    return JWTPayload(sub=username, role=PossibleRoles.STAFF, org_id=1, exp=None)  # type: ignore[typeddict-item]


# ── Password hashing ──────────────────────────────────────────────────────────


def test_correct_password_verifies_against_its_hash():
    """
    Verify that the correct password matches its hash.

    WHY: This is the fundamental password verification flow. If this fails,
    no one can log in. We use bcrypt which handles salting automatically.
    """
    hashed = get_password_hash("mysecretpassword")
    assert verify_password("mysecretpassword", hashed) is True


def test_wrong_password_does_not_verify_against_hash():
    """
    Verify that wrong passwords are rejected.

    WHY: Core security - if wrong passwords could verify, anyone could log in
    as anyone else. This test catches bugs in the hashing/verification logic.
    """
    hashed = get_password_hash("correctpassword")
    assert verify_password("wrongpassword", hashed) is False


def test_same_password_produces_different_hashes_due_to_random_salt():
    """
    Verify that each hash includes a unique random salt.

    WHY: Salting prevents rainbow table attacks. If two users have the same
    password, their hashes should still be different. This test ensures
    we're not using a broken hashing configuration.
    """
    h1 = get_password_hash("samepassword")
    h2 = get_password_hash("samepassword")
    assert h1 != h2


def test_empty_password_verifies_against_its_own_hash():
    """
    Verify that empty passwords work correctly (hash and verify).

    WHY: Edge case testing. While we reject empty passwords at the API level,
    the hashing function itself should handle any string correctly. This
    ensures the crypto layer doesn't have special-case bugs.
    """
    hashed = get_password_hash("")
    assert verify_password("", hashed) is True
    assert verify_password("notempty", hashed) is False


# ── JWT ───────────────────────────────────────────────────────────────────────


def test_valid_token_decodes_to_correct_payload():
    """
    Verify that a freshly created token decodes to the original payload.

    WHY: This is the happy path for token handling. The token must preserve
    the username and role so we know who made the request and what they
    can do.
    """
    token = create_access_token(data=_make_payload())
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"
    assert payload["role"] == PossibleRoles.STAFF


def test_expired_token_returns_none():
    """
    Verify that expired tokens are rejected.

    WHY: Token expiration limits the damage from leaked tokens. An attacker
    who steals an old token shouldn't be able to use it forever.
    """
    token = create_access_token(
        data=_make_payload(),
        expires_delta=timedelta(seconds=-1),
    )
    assert decode_access_token(token) is None


def test_tampered_token_returns_none():
    """
    Verify that modified tokens fail signature verification.

    WHY: Tokens are signed with HMAC. If someone modifies the payload
    (e.g., changing their role from STAFF to ADMIN), the signature won't
    match and we must reject it.
    """
    token = create_access_token(data=_make_payload())
    assert decode_access_token(token + "tampered") is None


def test_random_string_as_token_returns_none():
    """
    Verify that arbitrary strings are rejected as tokens.

    WHY: Defense against attackers sending garbage. The decode function
    should gracefully reject anything that isn't a valid JWT.
    """
    assert decode_access_token("notavalidtoken") is None


def test_empty_string_as_token_returns_none():
    """
    Verify that empty string is rejected as a token.

    WHY: Edge case - empty Authorization headers could slip through if
    not handled. The decode function should treat "" as invalid.
    """
    assert decode_access_token("") is None


def test_token_with_custom_expiry_is_valid_within_window():
    """
    Verify that custom expiration times work correctly.

    WHY: Different token lifetimes may be used (e.g., short-lived access
    tokens, longer refresh tokens). This ensures the expiry parameter
    is actually respected.
    """
    token = create_access_token(
        data=_make_payload(),
        expires_delta=timedelta(hours=2),
    )
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"
