{
  pkgs,
  lib,
  config,
  inputs,
  ...
}: {
  packages = with pkgs; [
    curl
    jq
    httpie
    ruff
    basedpyright
  ];

  languages.python = {
    enable = true;
    version = "3.13";
    uv = {
      enable = true;
      sync.enable = true;
    };
  };
  languages.opentofu = {
    enable = true;
  };

  env = {
    DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/alter_event_tracker";
    SECRET_KEY = "CHANGE_ME_IN_PRODUCTION_USE_openssl_rand_-hex_32";
    ALGORITHM = "HS256";
    ACCESS_TOKEN_EXPIRE_MINUTES = "60";
    BACKEND_PORT = "8000";
    MAZMO_THREAD_ID = "0";
  };

  scripts = {
    # I KNOW, i should use services.postgres, but for some stupid-ass reason, it crashes Hyprland (yes, WTF)
    db-start = {
      exec = ''
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
      description = "Start Postgres Docker container";
    };

    db-stop = {
      exec = ''
        docker stop alter-tracker-postgres
        echo "✓ Postgres stopped"
      '';
      description = "Stop Postgres Docker container";
    };

    db-teardown = {
      exec = ''
        docker stop alter-tracker-postgres 2>/dev/null || true
        docker rm alter-tracker-postgres 2>/dev/null || true
        docker volume rm alter-tracker-pgdata 2>/dev/null || true
        echo "✓ Postgres container and volume removed"
      '';
      description = "Remove Postgres container and volume";
    };

    db-migrate = {
      exec = "uv run alembic upgrade head";
      description = "Run Alembic migrations";
    };

    db-revision = {
      exec = ''uv run alembic revision --autogenerate -m "$1"'';
      description = "Generate a new Alembic migration";
    };

    dev-backend = {
      exec = "uv run uvicorn app.main:app --reload --port $BACKEND_PORT --host 0.0.0.0";
      description = "Start FastAPI on :8000 with hot-reload";
    };

    run-tests = {
      exec = ''uv run pytest "$@"'';
      description = "Run pytest test suite";
    };

    lint = {
      exec = "ruff check .";
      description = "Run ruff linter";
    };

    format = {
      exec = "ruff format .";
      description = "Run ruff formatter";
    };

    seed-admin = {
      exec = ''
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
      description = "Create initial admin user (run once after db-migrate)";
    };
    export-aws-credentials = {
      exec = "eval $(aws configure export-credentials --format env)";
      description = "Exports the AWS credentials as environment variables. Needed for opentofu. Use after doing aws  login";
    };
  };

  enterShell = ''
    echo ""
    echo "  🎪  Alter Event Tracker - Dev Environment"
    echo ""
    ${pkgs.gnused}/bin/sed -e 's| |••|g' -e 's|=| |' <<EOF | ${pkgs.util-linuxMinimal}/bin/column -t | ${pkgs.gnused}/bin/sed -e 's|^|  |' -e 's|••| |g'
    ${lib.generators.toKeyValue {} (lib.mapAttrs (name: value: value.description) config.scripts)}
    EOF
    echo ""
  '';
}
