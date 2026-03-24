"""
OpenAPI examples for auth router endpoints.

Endpoints:
  POST /auth/register  - Create a new staff account (pending approval)
  POST /auth/token     - Login and get JWT
  GET  /auth/userinfo  - Get current user's profile
"""

from typing import Any

from app.openapi_examples._constants import (
    ADMIN_USER,
    EXAMPLE_JWT,
    STAFF_USER_PENDING,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_409_username_taken,
    error_422_validation,
)

# ── POST /auth/register ───────────────────────────────────────────────────────

REGISTER_REQUEST_EXAMPLES: dict[str, Any] = {
    "new_volunteer": {
        "summary": "New volunteer registration",
        "description": "Register a new staff account. Account will be pending admin approval.",
        "value": {
            "username": "nuevo_voluntario",
            "password": "contraseña_segura_123!",
        },
    },
}

REGISTER_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Account created (pending approval)",
        "content": {
            "application/json": {
                "examples": {
                    "success": {
                        "summary": "Registration successful",
                        "value": STAFF_USER_PENDING,
                    },
                }
            }
        },
    },
    **error_409_username_taken(),
    **error_422_validation(),
}

# ── POST /auth/token ──────────────────────────────────────────────────────────

# Note: OAuth2PasswordRequestForm uses form data, not JSON body
# So we don't need request examples, just response examples

TOKEN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Login successful",
        "content": {
            "application/json": {
                "examples": {
                    "success": {
                        "summary": "JWT token returned",
                        "value": {
                            "access_token": EXAMPLE_JWT,
                            "token_type": "bearer",
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# ── GET /auth/userinfo ────────────────────────────────────────────────────────

USERINFO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Current user profile",
        "content": {
            "application/json": {
                "examples": {
                    "admin_user": {
                        "summary": "Admin viewing their profile",
                        "value": ADMIN_USER,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}
