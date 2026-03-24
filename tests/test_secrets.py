"""Tests for the AWS Secrets Manager integration.

These tests verify that secrets can be fetched from environment variables
(local dev) or AWS Secrets Manager (production), with appropriate fallbacks.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.core.secrets import SecretNotFoundError, get_admin_credentials, get_secret

# ── get_secret from environment ───────────────────────────────────────────────


def test_get_secret_returns_json_from_environment():
    """
    Verify that secrets can be read from environment variables as JSON.

    WHY: Local development uses env vars instead of AWS. JSON format
    allows storing structured secrets like {"username": "...", "password": "..."}.
    """
    env_key = "SECRET_MY_APP_CREDENTIALS"
    env_value = '{"username": "admin", "password": "secret123"}'

    with patch.dict(os.environ, {env_key: env_value}):
        result = get_secret("my-app/credentials")

    assert result == {"username": "admin", "password": "secret123"}


def test_get_secret_returns_dict_for_non_json_environment_value():
    """
    Verify that non-JSON env values are wrapped in {"value": ...}.

    WHY: Simple string secrets (like API keys) don't need JSON format.
    We wrap them for consistent return type.
    """
    env_key = "SECRET_MY_APP_API_KEY"
    env_value = "sk-abc123"

    with patch.dict(os.environ, {env_key: env_value}):
        result = get_secret("my-app/api-key")

    assert result == {"value": "sk-abc123"}


def test_get_secret_transforms_secret_name_to_env_key():
    """
    Verify that secret names are correctly transformed to env var names.

    WHY: AWS secret names use slashes and dashes, but env vars use
    underscores and uppercase. We need consistent transformation.
    """
    # "alter-tracker-backend/admin-credentials" should become
    # "SECRET_ALTER_TRACKER_BACKEND_ADMIN_CREDENTIALS"
    env_key = "SECRET_ALTER_TRACKER_BACKEND_ADMIN_CREDENTIALS"
    env_value = '{"test": "value"}'

    with patch.dict(os.environ, {env_key: env_value}):
        result = get_secret("alter-tracker-backend/admin-credentials")

    assert result == {"test": "value"}


# ── get_secret when not found ─────────────────────────────────────────────────


def test_get_secret_raises_when_required_and_not_found():
    """
    Verify that missing required secrets raise SecretNotFoundError.

    WHY: In production, missing secrets should fail fast with a clear error
    rather than silently using defaults.
    """
    # Ensure env var doesn't exist and boto3 import fails
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.core.secrets._get_boto3_client") as mock_boto,
    ):
        mock_boto.side_effect = ImportError("No module named 'boto3'")

        with pytest.raises(SecretNotFoundError, match="not found"):
            get_secret("nonexistent/secret", required=True)


def test_get_secret_returns_none_when_not_required_and_not_found():
    """
    Verify that missing optional secrets return None.

    WHY: Some secrets are optional (e.g., analytics keys). We shouldn't
    crash if they're not configured.
    """
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.core.secrets._get_boto3_client") as mock_boto,
    ):
        mock_boto.side_effect = ImportError("No module named 'boto3'")

        result = get_secret("optional/secret", required=False)

    assert result is None


# ── get_secret from AWS ───────────────────────────────────────────────────────


def test_get_secret_fetches_from_aws_when_env_not_set():
    """
    Verify that secrets are fetched from AWS when env var is not set.

    WHY: Production uses AWS Secrets Manager. If no env override exists,
    we should fetch from AWS.
    """
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {
        "SecretString": '{"username": "prod-admin", "password": "prod-secret"}'
    }

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.core.secrets._get_boto3_client", return_value=mock_client),
    ):
        # Clear the lru_cache to ensure fresh client
        from app.core.secrets import _get_boto3_client

        _get_boto3_client.cache_clear()

        result = get_secret("my-app/credentials")

    assert result == {"username": "prod-admin", "password": "prod-secret"}
    mock_client.get_secret_value.assert_called_once_with(SecretId="my-app/credentials")


class _AWSError(Exception):
    """Simulates a botocore ClientError with a .response attribute."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code}}


def test_get_secret_handles_aws_resource_not_found():
    """
    Verify that ResourceNotFoundException from AWS is handled gracefully.

    WHY: If the secret doesn't exist in AWS, we should raise a clear error
    (if required) or return None (if optional).
    """
    mock_client = MagicMock()
    mock_error = _AWSError("Secret not found", "ResourceNotFoundException")
    mock_client.get_secret_value.side_effect = mock_error

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.core.secrets._get_boto3_client", return_value=mock_client),
    ):
        from app.core.secrets import _get_boto3_client

        _get_boto3_client.cache_clear()

        # Should return None when not required
        result = get_secret("nonexistent/secret", required=False)
        assert result is None

        # Should raise when required
        with pytest.raises(SecretNotFoundError, match="not found in AWS"):
            get_secret("nonexistent/secret", required=True)


# ── get_admin_credentials ─────────────────────────────────────────────────────


def test_get_admin_credentials_uses_env_vars_when_set():
    """
    Verify that admin credentials are read from env vars when available.

    WHY: Local development uses simple env vars for convenience.
    """
    with patch.dict(os.environ, {"ADMIN_USERNAME": "localadmin", "ADMIN_PASSWORD": "localpass"}):
        username, password = get_admin_credentials()

    assert username == "localadmin"
    assert password == "localpass"


def test_get_admin_credentials_falls_back_to_defaults():
    """
    Verify that default credentials are used when nothing else is available.

    WHY: For local development without any configuration, we provide
    insecure defaults so devs can get started quickly.
    """
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.core.secrets.get_secret", return_value=None),
    ):
        username, password = get_admin_credentials()

    assert username == "admin"
    assert password == "changeme-insecure-123"


def test_get_admin_credentials_uses_secrets_manager_when_available():
    """
    Verify that credentials are fetched from Secrets Manager in production.

    WHY: Production should use AWS Secrets Manager, not env vars or defaults.
    """
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "app.core.secrets.get_secret",
            return_value={"username": "prod-admin", "password": "prod-secret"},
        ),
    ):
        username, password = get_admin_credentials()

    assert username == "prod-admin"
    assert password == "prod-secret"
