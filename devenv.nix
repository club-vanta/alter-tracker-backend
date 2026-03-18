{
  pkgs,
  lib,
  config,
  inputs,
  ...
}: {
  # ── Language runtimes ────────────────────────────────────────────────────────
  languages.python = {
    enable = true;
    version = "3.13";
    uv = {
      enable = true;
      sync.enable = true;
    };
  };

  # ── Environment variables ────────────────────────────────────────────────────
  env = {
    DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/alter_event_tracker";
    SECRET_KEY = "CHANGE_ME_IN_PRODUCTION_USE_openssl_rand_-hex_32";
    ALGORITHM = "HS256";
    ACCESS_TOKEN_EXPIRE_MINUTES = "60";
    BACKEND_PORT = "8000";
    MAZMO_THREAD_ID = "0";
  };

  # ── Convenience scripts ──────────────────────────────────────────────────────
  scripts = {
    # I KNOW, i should use services.postgres, but for some stupid-ass reason, it crashes Hyprland (yes, WTF)
    db-start.exec = ''
        docker run -d \
        --name alter-tracker-postgres \
        --restart unless-stopped \
        -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_DB=alter_event_tracker \
        -p 5432:5432 \
        -v alter-tracker-pgdata:/var/lib/postgresql \
        postgres:18 \
      || docker start alter-tracker-postgres
      echo "✓ Postgres started"
    '';

    db-stop.exec = ''
      docker stop alter-tracker-postgres
      echo "✓ Postgres stopped"
    '';

    db-teardown.exec = ''
      docker stop alter-tracker-postgres 2>/dev/null || true
      docker rm alter-tracker-postgres 2>/dev/null || true
      docker volume rm alter-tracker-pgdata 2>/dev/null || true
      echo "✓ Postgres container and volume removed"
    '';

    db-migrate.exec = ''
      uv run alembic upgrade head
    '';

    db-revision.exec = ''
      uv run alembic revision --autogenerate -m "$1"
    '';

    dev-backend.exec = ''
      uv run uvicorn app.main:app --reload --port $BACKEND_PORT --host 0.0.0.0
    '';

    run-tests.exec = ''
      uv run pytest "$@"
    '';

    lint.exec = ''
      uv run ruff check .
    '';

    format.exec = ''
      uv run ruff format .
    '';

    seed-admin.exec = ''
            uv run python -c "
      from app.models.models import User, PossibleRoles, Role
      from app.core.security import get_password_hash
      from sqlmodel import Session, select, create_engine
      import os

      engine = create_engine(os.environ['DATABASE_URL'])
      with Session(engine) as session:
          existing = session.exec(select(User).where(User.username == 'admin')).first()
          if not existing:
              admin_role = session.exec(select(Role).where(Role.name == PossibleRoles.ADMIN)).first()
              if not admin_role:
                  print('ERROR: user_roles table not seeded. Run db-migrate first.')
                  exit(1)
              admin = User(
                  username='admin',
                  hashed_password=get_password_hash('changeme-insecure-123'),
                  is_approved=True,
                  role_id=admin_role.id,
              )
              session.add(admin)
              session.commit()
              print('Admin user created: username=admin, password=changeme-insecure-123')
          else:
              print('Admin user already exists.')
      "
    '';
  };

  # ── Extra system packages ────────────────────────────────────────────────────
  packages = with pkgs; [
    curl
    jq
    httpie
  ];

  # ── Shell hook ───────────────────────────────────────────────────────────────
  enterShell = ''
    echo ""
    echo "  🎪  Alter Event Tracker – Dev Environment"
    echo "  ──────────────────────────────────────────"
    echo "  db-start        → start Postgres container"
    echo "  db-stop         → stop Postgres container"
    echo "  db-teardown     → remove container + volume"
    echo "  db-migrate      → alembic upgrade head"
    echo "  db-revision     → alembic autogenerate"
    echo "  dev-backend     → FastAPI on :8000 (hot-reload)"
    echo "  run-tests       → run pytest"
    echo "  seed-admin      → create initial admin user"
    echo ""
  '';
}
