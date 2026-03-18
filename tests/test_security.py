"""Unit tests for security utilities (no DB, no HTTP)."""

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
    return JWTPayload(sub=username, role=PossibleRoles.STAFF, exp=None)  # type: ignore[typeddict-item]


# ── Password hashing ──────────────────────────────────────────────────────────


def test_correct_password_verifies_against_its_hash():
    hashed = get_password_hash("mysecretpassword")
    assert verify_password("mysecretpassword", hashed) is True


def test_wrong_password_does_not_verify_against_hash():
    hashed = get_password_hash("correctpassword")
    assert verify_password("wrongpassword", hashed) is False


def test_same_password_produces_different_hashes_due_to_random_salt():
    h1 = get_password_hash("samepassword")
    h2 = get_password_hash("samepassword")
    assert h1 != h2


def test_empty_password_verifies_against_its_own_hash():
    hashed = get_password_hash("")
    assert verify_password("", hashed) is True
    assert verify_password("notempty", hashed) is False


# ── JWT ───────────────────────────────────────────────────────────────────────


def test_valid_token_decodes_to_correct_payload():
    token = create_access_token(data=_make_payload())
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"
    assert payload["role"] == PossibleRoles.STAFF


def test_expired_token_returns_none():
    token = create_access_token(
        data=_make_payload(),
        expires_delta=timedelta(seconds=-1),
    )
    assert decode_access_token(token) is None


def test_tampered_token_returns_none():
    token = create_access_token(data=_make_payload())
    assert decode_access_token(token + "tampered") is None


def test_random_string_as_token_returns_none():
    assert decode_access_token("notavalidtoken") is None


def test_empty_string_as_token_returns_none():
    assert decode_access_token("") is None


def test_token_with_custom_expiry_is_valid_within_window():
    token = create_access_token(
        data=_make_payload(),
        expires_delta=timedelta(hours=2),
    )
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"
