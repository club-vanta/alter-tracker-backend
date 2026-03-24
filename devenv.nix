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
    JWT_SIGNING_KEY = "CHANGE_ME_IN_PRODUCTION_USE_openssl_rand_-hex_32";
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

    coverage = {
      exec = ''
        uv run pytest --cov=app --cov-report=html "$@"
        echo "✓ Coverage report: htmlcov/index.html"
      '';
      description = "Run tests with coverage and generate HTML report";
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
      exec = "PYTHONPATH=. uv run python scripts/seed_admin.py";
      description = "Create initial admin user (credentials from AWS Secrets Manager or env vars)";
    };
    aws-login = {
      exec = ''
        echo ""
        echo "Step 1 — log in to AWS:"
        echo "  aws login"
        echo ""
        echo "Step 2 — export AWS credentials into your current shell"
        echo "  (scripts can't do this for you — copy and run this):"
        echo ""
        echo '  eval $(aws configure export-credentials --format env)'
        echo ""
        echo "Step 3 — export Cloudflare API token:"
        echo ""
        echo '  export TF_VAR_cloudflare_api_token="your-api-token-here"'
        echo ""
        echo "Alternatively, you can set cloudflare_api_token in infra/terraform.tfvars"
        echo "After both steps, tofu plan/apply will work."
        echo ""
      '';
      description = "Print AWS + Cloudflare credential export instructions for tofu";
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
