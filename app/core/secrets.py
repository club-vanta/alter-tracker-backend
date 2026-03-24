"""
AWS Secrets Manager integration.

Fetches secrets from AWS Secrets Manager in production, falls back to
environment variables for local development.

Usage:
    from app.core.secrets import get_secret

    # Returns dict like {"admin_username": "admin", "admin_password": "...", "jwt_signing_key": "..."}
    secrets = get_secret("alter-tracker-backend/secrets")
"""

import json
import os
from functools import lru_cache
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class SecretNotFoundError(Exception):
    """Raised when a secret cannot be found in Secrets Manager or environment."""

    pass


@lru_cache
def _get_boto3_client():
    """Lazily import and create boto3 client (only when needed)."""
    try:
        import boto3
    except ImportError as e:
        raise ImportError("boto3 is required for AWS Secrets Manager. Install it with: uv add boto3") from e

    return boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1"))


def get_secret(secret_name: str, *, required: bool = True) -> dict[str, Any] | None:
    """
    Fetch a secret from AWS Secrets Manager.

    Args:
        secret_name: The name/ARN of the secret (e.g., "alter-tracker-backend/admin-credentials")
        required: If True, raises SecretNotFoundError when secret is missing.
                  If False, returns None.

    Returns:
        Parsed JSON secret as a dict, or None if not found and required=False.

    Raises:
        SecretNotFoundError: If required=True and secret cannot be found.
    """
    # Check for local override via environment variable
    # SECRET_<name> with slashes replaced by underscores, uppercased
    env_key = f"SECRET_{secret_name.replace('/', '_').replace('-', '_').upper()}"
    env_value = os.getenv(env_key)
    if env_value:
        log.debug("Using secret from environment", secret_name=secret_name, env_key=env_key)
        try:
            return json.loads(env_value)
        except json.JSONDecodeError:
            # If it's not JSON, assume it's a simple string value
            return {"value": env_value}

    # Try AWS Secrets Manager
    try:
        client = _get_boto3_client()
        response = client.get_secret_value(SecretId=secret_name)
        secret_string = response["SecretString"]
        log.debug("Fetched secret from AWS Secrets Manager", secret_name=secret_name)
        return json.loads(secret_string)
    except ImportError as e:
        # boto3 not installed - only acceptable in local dev
        if required:
            raise SecretNotFoundError(
                f"Secret '{secret_name}' not found. "
                f"Set {env_key} environment variable for local development, "
                f"or install boto3 for AWS Secrets Manager access."
            ) from e
        return None
    except Exception as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            if required:
                raise SecretNotFoundError(
                    f"Secret '{secret_name}' not found in AWS Secrets Manager. "
                    f"Create it with: aws secretsmanager put-secret-value "
                    f"--secret-id {secret_name} --secret-string '{{...}}'"
                ) from e
            return None
        # Credential errors - log and return None (fall back to defaults in dev)
        log.debug(
            "AWS Secrets Manager unavailable",
            secret_name=secret_name,
            error=str(e),
            hint="This is expected in local dev without valid AWS credentials",
        )
        if required:
            raise SecretNotFoundError(
                f"Secret '{secret_name}' not accessible. Set {env_key} environment variable for local development."
            ) from e
        return None


def get_admin_credentials() -> tuple[str, str]:
    """
    Get the initial admin credentials.

    For local dev, uses ADMIN_USERNAME and ADMIN_PASSWORD env vars,
    falling back to defaults.

    For production, fetches from AWS Secrets Manager.

    Returns:
        Tuple of (username, password)
    """
    # Local dev: check simple env vars first
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if username and password:
        log.debug("Using admin credentials from environment variables")
        return username, password

    # Try Secrets Manager (will fail gracefully if boto3 not installed)
    secret = get_secret("alter-tracker-backend/secrets", required=False)
    if secret:
        return secret["admin_username"], secret["admin_password"]

    # Final fallback for local dev only
    log.warning(
        "Using default admin credentials - DO NOT use in production",
        hint="Set ADMIN_USERNAME/ADMIN_PASSWORD or configure AWS Secrets Manager",
    )
    return "admin", "changeme-insecure-123"
