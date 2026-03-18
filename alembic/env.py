"""
Alembic env.py – runtime migration configuration.

Key decisions:
  1. DATABASE_URL is read from the environment (never hardcoded).
  2. We import all SQLModel models so that `autogenerate` can detect schema
     changes correctly. Add new model imports here as the project grows.
  3. We use SQLModel.metadata (not Base.metadata) for compatibility.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

# ── Import all models so Alembic autogenerate sees them ──────────────────────
# Add new model modules here as the project grows.
from app.models.models import User, Guest  # noqa: F401

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the database URL from the environment at runtime.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Make sure you are running inside the devenv shell."
    )
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = SQLModel.metadata


# ── Migration helpers ─────────────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection (outputs SQL script).
    Useful for reviewing changes before applying them in production.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database."""
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type changes (Postgres-specific)
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
