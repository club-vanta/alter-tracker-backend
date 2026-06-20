# Alter Event Tracker Backend

[![codecov](https://codecov.io/github/club-vanta/alter-tracker-backend/branch/main/graph/badge.svg?token=CDC5NNOY2K)](https://codecov.io/github/club-vanta/alter-tracker-backend)

Door tracker API for Alter meetups. Integrates with Mazmo to sync guest lists
and track check-ins at events.

**Tech Stack:** FastAPI, PostgreSQL, SQLModel, JWT auth, structlog

**API Documentation:** Once running, visit [`/docs`](http://localhost:8000/docs)
(Swagger UI) or [`/redoc`](http://localhost:8000/redoc) (ReDoc). All endpoints
are documented there with examples.

**Frontend:** The `velvet` repo consumes this API. After any endpoint change,
regenerate the frontend types from the `velvet` directory:

```bash
npm run generate:api   # reads from ../alter-tracker-backend/openapi.json
# or if the server is running:
npx openapi-typescript http://localhost:8000/openapi.json -o src/api/types.ts
```

## Documentation

- [docs.club-vanta.com](https://docs.club-vanta.com) - staff and admin guide, database schema, API reference

---

## Quick Start

```bash
# 1. Enter dev environment (sets up Python, env vars, scripts)
direnv allow  # or: source shell.sh

# 2. Start PostgreSQL database
db-start

# 3. Run database migrations
db-migrate

# 4. Create initial admin user
seed-admin
#    - username: admin
#    - password: insecure-changeme-123

# 5. Start server with hot-reload
dev-backend
```

Server runs at http://localhost:8000/docs

---

## Configuration

### Environment Variables

| Variable                      | Required | Default                     | Description                                                                                                                        |
| ----------------------------- | -------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`                | Yes      | -                           | PostgreSQL connection string (e.g., `postgresql+psycopg://user:pass@host:5432/db`)                                                 |
| `JWT_SIGNING_KEY`             | Yes      | -                           | JWT signing key. Generate with: `openssl rand -hex 32`                                                                             |
| `ADMIN_USERNAME`              | Yes*     | -                           | Initial admin username. Used by `seed-admin` only. Set automatically by `deploy.sh` from AWS Secrets Manager (local: `devenv.nix`) |
| `ADMIN_PASSWORD`              | Yes*     | -                           | Initial admin password. Used by `seed-admin` only. Set automatically by `deploy.sh` from AWS Secrets Manager (local: `devenv.nix`) |
| `DB_USER`                     | Yes*     | -                           | PostgreSQL username. Used by docker-compose. Set automatically by `deploy.sh` from AWS Secrets Manager (local: `devenv.nix`)       |
| `DB_PASSWORD`                 | Yes*     | -                           | PostgreSQL password. Used by docker-compose. Set automatically by `deploy.sh` from AWS Secrets Manager (local: `devenv.nix`)       |
| `ALGORITHM`                   | No       | `HS256`                     | JWT signing algorithm                                                                                                              |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No       | `720`                       | JWT token lifetime (12 hours)                                                                                                      |
| `JSON_LOGS`                   | No       | `false`                     | Set `true` for JSON output (CloudWatch)                                                                                            |
| `LOG_LEVEL`                   | No       | `INFO`                      | Minimum log level: DEBUG, INFO, WARNING, ERROR                                                                                     |
| `BACKEND_PORT`                | No       | `8000`                      | Port uvicorn listens on                                                                                                            |
| `DEBUG`                       | No       | `false`                     | Enable SQLAlchemy SQL logging (never in prod)                                                                                      |
| `MAZMO_BASE_URL`              | No       | `https://prod.mazmoapi.net` | Mazmo API base URL                                                                                                                 |
| `MAZMO_USER_BATCH_SIZE`       | No       | `30`                        | Max user IDs per Mazmo request                                                                                                     |
| `MAZMO_REQUEST_TIMEOUT`       | No       | `15.0`                      | Mazmo API timeout in seconds                                                                                                       |

> \* Required at deploy time only (`seed-admin` / docker-compose), not by the
> running API. In production, `deploy.sh` sets these automatically from AWS
> Secrets Manager — you never need to set them manually.

### Local Development (devenv.nix)

The `devenv.nix` file automatically sets all required environment variables for
local development.

Override any variable by creating a `.env` file (gitignored):

```bash
# .env
LOG_LEVEL=DEBUG
JWT_SIGNING_KEY=your-custom-key
```

### Production (AWS Secrets Manager)

All secrets are stored in a single AWS Secrets Manager entry and fetched by
`deploy.sh` at deploy time — the app itself never talks to Secrets Manager:

| Secret Name                     | Fields                                                                          |
| ------------------------------- | ------------------------------------------------------------------------------- |
| `alter-tracker-backend/secrets` | `admin_username`, `admin_password`, `jwt_signing_key`, `db_user`, `db_password` |

Terraform creates the secret container automatically. After `tofu apply`,
populate it with your values:

```bash
aws secretsmanager put-secret-value \
  --secret-id "alter-tracker-backend/secrets" \
  --secret-string '{
    "admin_username": "admin",
    "admin_password": "your-secure-password",
    "jwt_signing_key": "'"$(openssl rand -hex 32)"'",
    "db_user": "alter_tracker",
    "db_password": "'"$(openssl rand -hex 16)"'"
  }'
```

> Do **not** put secret values in Terraform — they would end up in the state
> file.

**Local dev:** `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `JWT_SIGNING_KEY`, `DB_USER`,
and `DB_PASSWORD` are all set in `devenv.nix` — no AWS access needed.

---

## Available Commands

All commands are defined in `devenv.nix` and available in the dev shell:

| Command                  | Description                                                 |
| ------------------------ | ----------------------------------------------------------- |
| `db-start`               | Start PostgreSQL Docker container                           |
| `db-stop`                | Stop PostgreSQL container                                   |
| `db-teardown`            | Remove PostgreSQL container and volume (destructive)        |
| `db-migrate`             | Run Alembic migrations (`alembic upgrade head`)             |
| `db-revision "message"`  | Generate new migration (`alembic revision --autogenerate`)  |
| `dev-backend`            | Start FastAPI with hot-reload on port 8000                  |
| `seed-admin`             | Create initial admin user (idempotent)                      |
| `run-tests`              | Run pytest test suite                                       |
| `coverage`               | Run tests with coverage, generate HTML report in `htmlcov/` |
| `lint`                   | Run ruff linter                                             |
| `format`                 | Run ruff formatter                                          |

---

## Database

For the current database schema see [docs.club-vanta.com](https://docs.club-vanta.com).

### Migrations

```bash
# Create a new migration after modifying models
db-revision "add new_field to guests"

# Review the generated migration
cat alembic/versions/XXXX_add_new_field_to_guests.py

# Apply migrations
db-migrate
```

Migration files live in `alembic/versions/` with sequential prefixes (0001_,
0002_, etc.).

---

## Logging

### Development (default)

Pretty console output with colors:

```
2024-01-15T10:30:00 [info     ] Checking in guest    guest_id=12345 meetup_id=abc-123 request_id=a1b2c3d4
```

Enable verbose logging:

```bash
LOG_LEVEL=DEBUG dev-backend
```

### Production

Set `JSON_LOGS=true` for CloudWatch-friendly JSON:

```json
{
  "timestamp": "2024-01-15T10:30:00",
  "level": "info",
  "event": "Checking in guest",
  "guest_id": 12345,
  "request_id": "a1b2c3d4"
}
```

**Request tracing:**

- Every log includes `request_id` for correlation
- Pass `X-Request-ID` header for distributed tracing
- Response includes `X-Request-ID` header

---

## Infrastructure (AWS)

The `infra/` directory contains OpenTofu/Terraform configuration:

| File            | Purpose                                          |
| --------------- | ------------------------------------------------ |
| `ec2.tf`        | EC2 instance and security group                  |
| `vpc.tf`        | VPC, subnets (IPv4 + IPv6)                       |
| `cloudflare.tf` | Cloudflare DNS records (A + AAAA) + health check |
| `monitoring.tf` | SNS topic + disk usage alert cron job            |
| `secrets.tf`    | AWS Secrets Manager secret                       |
| `ssm.tf`        | SSM for instance management                      |
| `providers.tf`  | AWS and Cloudflare provider config               |
| `variables.tf`  | All input variable declarations                  |
| `outputs.tf`    | Instance IP outputs                              |
| `locals.tf`     | Shared locals                                    |

### Monitoring

Two alerting mechanisms are in place:

- **UptimeRobot** — free external uptime monitor. Set it up manually at
  [uptimerobot.com](https://uptimerobot.com): add an HTTPS monitor for
  `https://api-alter-tracker.club-vanta.com/health`, 5-minute interval, alert to
  `infra-alerts@club-vanta.com`. Cloudflare health checks require a paid plan
  and are not managed by Terraform.
- **Disk usage alert** — a cron job on the instance publishes an SNS alert to
  `infra-alerts@club-vanta.com` if disk usage exceeds 80%.

> **Note:** `infra-alerts@club-vanta.com` is not a real mailbox — it is an email
> routing rule configured manually in the Cloudflare Email Routing dashboard,
> forwarding to a real address. If the forwarding destination ever changes,
> update it there, not in Terraform.

### Credentials

**AWS** — credentials live in `~/.aws/credentials` (standard credential chain, picked up by both the S3 backend and provider):

```ini
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
```

Generate access keys at: IAM → Users → your user → Security credentials → Create access key.

**Cloudflare** — credentials live in `infra/terraform.tfvars` (gitignored — never commit it):

```hcl
cloudflare_api_token  = "cfut_..."
cloudflare_account_id = "..."
```

The Cloudflare API token must have:

| Resource                   | Permission |
| -------------------------- | ---------- |
| Account / Cloudflare Pages | Edit       |
| Zone / Health Checks       | Edit       |
| Zone / Zone Settings       | Edit       |
| Zone / DNS                 | Edit       |

### Deploying

```bash
cd infra
tofu init
tofu plan
tofu apply
```

### Deploying the application

After `tofu apply` has created the infrastructure and you have populated the
secret in AWS Secrets Manager, deploy the app to the instance.

`deploy.sh` and `docker-compose.yml` are bundled onto the instance by the
startup script — no git clone needed.

**Get the SSM login command from tofu:**

```bash
tofu output ssm_login_command
```

**SSM into the instance and deploy:**

```bash
# Paste the command from tofu output, then:
deploy.sh
```

This will:

1. Fetch all secrets from AWS Secrets Manager (using the instance IAM role — no
   credentials needed)
2. Pull the latest Docker image from GHCR
3. Run database migrations
4. Seed the initial admin user (idempotent — safe to run multiple times)
5. Start the app on port 8000

**To update the app after a new release:**

```bash
# SSM in and re-run:
deploy.sh
```

---

### Starting/stopping the instance for meetups

The instance only needs to be running during meetups — stopping it saves
~$7.50/month in compute costs.

Use the GitHub Actions manual workflows — triggerable from the GitHub UI or the GitHub mobile app:

| Workflow | What it does |
| -------- | ------------ |
| **Start Instance** | Starts the EC2 instance, waits until running, then runs `tofu apply` to update Cloudflare DNS with the new public IP |
| **Stop Instance** | Stops the EC2 instance |

Go to **Actions → Start Instance (or Stop Instance) → Run workflow**.

### GitHub Secrets

The workflows require these repository secrets (already configured):

| Secret | Description |
| ------ | ----------- |
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `TF_VAR_cloudflare_api_token` | Cloudflare API token |
| `TF_VAR_cloudflare_account_id` | Cloudflare account ID |

To recreate them:

```bash
gh secret set AWS_ACCESS_KEY_ID --repo club-vanta/alter-tracker-backend --body "AKIA..."
gh secret set AWS_SECRET_ACCESS_KEY --repo club-vanta/alter-tracker-backend --body "..."
gh secret set TF_VAR_cloudflare_api_token --repo club-vanta/alter-tracker-backend --body "cfut_..."
gh secret set TF_VAR_cloudflare_account_id --repo club-vanta/alter-tracker-backend --body "..."
```

> The values are in `~/.aws/credentials` (AWS keys) and `infra/terraform.tfvars` (Cloudflare). `terraform.tfvars` is gitignored and never committed.

---

## Common Tasks

### Add a New Endpoint

1. Create or edit router in `app/routers/`
2. Add request/response schemas in `app/schemas/`
3. Add OpenAPI examples in `app/openapi_examples/`
4. If new router, include in `app/main.py`:
   ```python
   from app.routers import new_router
   app.include_router(new_router.router)
   ```

### Add a New Database Field

1. Edit model in `app/models/models.py`
2. Generate migration: `db-revision "add field_name to table_name"`
3. Review generated migration in `alembic/versions/`
4. Apply: `db-migrate`

### Promote a User to SITE_ADMIN

`seed-admin` creates the initial admin already as `SITE_ADMIN`. For any other
user, connect to the database and run:

```sql
UPDATE users
SET role_id = (SELECT id FROM user_roles WHERE name = 'SITE_ADMIN')
WHERE username = 'their_username';
```

The user must re-login after the change — the role is embedded in the JWT at
login time and won't update until a new token is issued.

To verify:

```sql
SELECT u.username, r.name AS role
FROM users u
JOIN user_roles r ON u.role_id = r.id
WHERE u.username = 'their_username';
```

### Add a New Configuration Variable

1. Add field to `app/core/config.py`
2. Set in `devenv.nix` for local dev
3. Document in this README

---

## Troubleshooting

### "database_url is required" / "jwt_signing_key is required"

You're not in the devenv shell. Run:

```bash
direnv allow  # or: source shell.sh
```

### "Admin role not found - run migrations first"

Run migrations:

```bash
db-migrate
```

### Logs not appearing

Check log level:

```bash
LOG_LEVEL=DEBUG dev-backend
```

### AWS credentials expired / "Unable to locate credentials"

Check that `aws_access_key_id` and `aws_secret_access_key` are set in `infra/terraform.tfvars`.

### "Cannot connect to database"

Ensure PostgreSQL is running:

```bash
db-start
docker ps  # Should show alter-tracker-postgres
```

### Check-in not working / "Guest not RSVPed"

Sync the guest list from Mazmo first via `POST /meetups/{meetup_id}/sync`.

### Access the production database

The DB is a PostgreSQL container with no public port. Access it via SSM:

```bash
# 1. Open SSM session on the EC2 instance
aws ssm start-session --target i-054c0dd0cd6c37dc5 --document-name alter-tracker-backend-session-preferences

# 2. Fetch credentials and connect
SECRET=$(aws secretsmanager get-secret-value \
  --secret-id alter-tracker-backend/secrets \
  --query SecretString --output text)

DB_USER=$(echo $SECRET | python3 -c "import sys,json; print(json.load(sys.stdin)['db_user'])")
DB_PASS=$(echo $SECRET | python3 -c "import sys,json; print(json.load(sys.stdin)['db_password'])")

docker exec -it $(docker ps -qf name=db) psql -U $DB_USER -d alter_event_tracker
```

### Port 8000 already in use

Kill the other process or use a different port:

```bash
lsof -i :8000
kill <PID>
# or
BACKEND_PORT=8001 dev-backend
```
