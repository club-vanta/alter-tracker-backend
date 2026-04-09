"""
OpenAPI examples for staff router endpoints.

Endpoints:
  GET   /staff/              - List all staff accounts (admin only)
  GET   /staff/pending       - List unapproved accounts (admin only)
  PATCH /staff/{id}/approve  - Approve or revoke a staff account (admin only)
  PATCH /staff/{id}/role     - Change a staff member's role (admin only)
  PATCH /staff/{id}/disable  - Disable a staff account (admin only)
  PATCH /staff/{id}/enable   - Re-enable a disabled account (admin only)
"""

from typing import Any

from app.openapi_examples._constants import (
    ADMIN_USER,
    STAFF_USER_APPROVED,
    STAFF_USER_DISABLED,
    STAFF_USER_PENDING,
)
from app.openapi_examples._error_responses import (
    error_400_self_approve_revoke,
    error_400_self_disable,
    error_400_self_role,
    error_401_invalid_credentials,
    error_403_admin_required,
    error_404_staff,
    error_409_already_disabled,
    error_409_not_disabled,
    error_422_validation_reason,
)

# ── GET /staff/ ───────────────────────────────────────────────────────────────

LIST_STAFF_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All staff accounts",
        "content": {
            "application/json": {
                "examples": {
                    "all_staff": {
                        "summary": "Mix of approved, pending, and disabled",
                        "value": [
                            ADMIN_USER,
                            STAFF_USER_APPROVED,
                            STAFF_USER_PENDING,
                            STAFF_USER_DISABLED,
                        ],
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
}

# ── GET /staff/pending ────────────────────────────────────────────────────────

LIST_PENDING_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Unapproved staff accounts",
        "content": {
            "application/json": {
                "examples": {
                    "pending_users": {
                        "summary": "Staff awaiting approval",
                        "value": [STAFF_USER_PENDING],
                    },
                    "no_pending": {
                        "summary": "No pending approvals",
                        "value": [],
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
}

# ── PATCH /staff/{id}/approve ─────────────────────────────────────────────────

APPROVE_REQUEST_EXAMPLES: dict[str, Any] = {
    "approve": {
        "summary": "Approve a staff account",
        "description": "Set is_approved to true so the user can log in",
        "value": {"is_approved": True},
    },
    "revoke": {
        "summary": "Revoke approval",
        "description": "Set is_approved to false to lock out the user",
        "value": {"is_approved": False},
    },
}

APPROVE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Approval status updated",
        "content": {
            "application/json": {
                "examples": {
                    "approved": {
                        "summary": "User now approved",
                        "value": {**STAFF_USER_PENDING, "is_approved": True},
                    },
                    "revoked": {
                        "summary": "User approval revoked",
                        "value": {**STAFF_USER_APPROVED, "is_approved": False},
                    },
                }
            }
        },
    },
    **error_400_self_approve_revoke(),
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_staff(),
}

# ── PATCH /staff/{id}/role ────────────────────────────────────────────────────

ROLE_REQUEST_EXAMPLES: dict[str, Any] = {
    "promote_to_admin": {
        "summary": "Promote to admin",
        "description": "Give a staff member admin privileges",
        "value": {"role": "ADMIN"},
    },
    "demote_to_staff": {
        "summary": "Demote to staff",
        "description": "Remove admin privileges from a user",
        "value": {"role": "STAFF"},
    },
}

ROLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Role updated",
        "content": {
            "application/json": {
                "examples": {
                    "promoted": {
                        "summary": "User promoted to admin",
                        "value": {
                            **STAFF_USER_APPROVED,
                            "role": {"id": 2, "name": "ADMIN"},
                        },
                    },
                    "demoted": {
                        "summary": "User demoted to staff",
                        "value": STAFF_USER_APPROVED,
                    },
                }
            }
        },
    },
    **error_400_self_role(),
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_staff(),
}

# ── PATCH /staff/{id}/disable ─────────────────────────────────────────────────

DISABLE_REQUEST_EXAMPLES: dict[str, Any] = {
    "disable": {
        "summary": "Disable with reason",
        "description": "Disable a staff account. A reason is required for audit purposes.",
        "value": {"reason": "Left the organization and no longer volunteers"},
    },
}

DISABLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Account disabled",
        "content": {
            "application/json": {
                "examples": {
                    "disabled": {
                        "summary": "Account now disabled",
                        "value": STAFF_USER_DISABLED,
                    },
                }
            }
        },
    },
    **error_400_self_disable(),
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_staff(),
    **error_409_already_disabled(),
    **error_422_validation_reason(),
}

# ── PATCH /staff/{id}/enable ──────────────────────────────────────────────────

ENABLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Account re-enabled",
        "content": {
            "application/json": {
                "examples": {
                    "enabled": {
                        "summary": "Account now active again",
                        "value": {
                            **STAFF_USER_DISABLED,
                            "is_disabled": False,
                            "disabled_at": None,
                            "disabled_reason": None,
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_staff(),
    **error_409_not_disabled(),
}
