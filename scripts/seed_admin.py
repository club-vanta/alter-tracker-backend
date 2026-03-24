#!/usr/bin/env python3
"""
Seed the initial admin user.

Reads ADMIN_USERNAME and ADMIN_PASSWORD from environment variables.
In local dev these are set in devenv.nix. In production, deploy.sh
exports them from AWS Secrets Manager before starting the container.

Usage:
    uv run python scripts/seed_admin.py
"""

import os
import sys

import structlog
from sqlmodel import Session, create_engine, select

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.models import PossibleRoles, Role, User

log = structlog.get_logger(__name__)


def seed_admin() -> bool:
    """
    Create the initial admin user if it doesn't exist.

    Returns:
        True if admin was created, False if already exists.
    """
    settings = get_settings()
    engine = create_engine(settings.database_url)

    username = os.environ["ADMIN_USERNAME"]
    password = os.environ["ADMIN_PASSWORD"]

    with Session(engine) as session:
        # Check if admin already exists
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            log.info("Admin user already exists", username=username)
            return False

        # Get admin role
        admin_role = session.exec(select(Role).where(Role.name == PossibleRoles.ADMIN)).first()
        if not admin_role or admin_role.id is None:
            log.error(
                "Admin role not found - run migrations first",
                hint="uv run alembic upgrade head",
            )
            sys.exit(1)

        # Create admin user
        admin = User(
            username=username,
            hashed_password=get_password_hash(password),
            is_approved=True,
            role_id=admin_role.id,
        )
        session.add(admin)
        session.commit()

        log.info("Admin user created", username=username)
        return True


if __name__ == "__main__":
    # Configure logging for CLI
    from app.core.logging import setup_logging

    setup_logging(json_logs=False, log_level="INFO")

    seed_admin()
    sys.exit(0)  # Idempotent operation - always succeeds
