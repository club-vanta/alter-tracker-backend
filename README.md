# Alter Event Tracker Backend

Door tracker API for Alter meetups. Integrates with Mazmo to sync guest lists and track check-ins at events.

**Tech Stack:** FastAPI, PostgreSQL, SQLModel, JWT auth, structlog

**API Documentation:** Once running, visit [`/docs`](http://localhost:8000/docs) (Swagger UI) or [`/redoc`](http://localhost:8000/redoc) (ReDoc). All endpoints are documented there with examples.

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

# 5. Start server with hot-reload
dev-backend
```

Server runs at http://localhost:8000/docs

---

## Domain Knowledge

### Mazmo and Meetups

- Alter organizes meetups through [mazmo.net](https://mazmo.net), where people RSVP to events
- This app does not create events on Mazmo - it syncs guest lists and tracks door check-ins
- Each meetup is linked to a Mazmo URL (e.g., `https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217`)
- When creating a meetup, the event date is fetched automatically from Mazmo
- Syncing pulls all RSVPs from Mazmo and stores them locally
- Sync is idempotent: it adds new guests and updates RSVP timestamps, but never modifies check-in data

### Guests

- A Guest is someone who has RSVPed to at least one meetup
- Guests are not created manually - they appear when syncing from Mazmo
- Each guest has:
  - `mazmo_user_id` - Their ID on Mazmo's platform (used as primary key)
  - `username` - Their @handle (rarely changes)
  - `displayname` - Display name (changes frequently)
- Guests can be banned globally by admins
- Ban records: who banned them, when, and why
- Staff can view the banned list to know who shouldn't be let in

### Staff and Roles

- Staff are the volunteers who use this app at meetup doors
- Two roles exist:

| Role | Capabilities |
|------|--------------|
| **STAFF** | View guest lists, check people in, view banned list |
| **ADMIN** | Everything STAFF can do, plus: ban/unban guests, approve new accounts, disable accounts, promote to admin |

- New registrations start as "unapproved" - admin approval required before login
- Accounts are disabled (not deleted) to preserve audit trails

### Check-in Flow

1. Create meetup using Mazmo URL
2. Sync guest list from Mazmo
3. When guest arrives, staff searches and checks them in
4. System records: arrival timestamp, arrival order (1st, 2nd, 3rd...), which staff member performed check-in
5. Undo requires a reason (for audit trail)

### Event Log (Audit Trail)

The EventLog tracks important actions for accountability and investigation.

**Event Types:**

| Type | Description |
|------|-------------|
| `CHECK_IN` | Guest marked as arrived at a meetup |
| `UNDO_CHECK_IN` | Check-in reversed (includes reason) |
| `BAN` | Guest banned (includes reason) |
| `UNBAN` | Guest unbanned |

**Each event records:**
- Actor (staff/admin who performed the action)
- Timestamp
- Target guest
- Related meetup (for check-in events)
- Reason (for undo/ban events)

**Access control:**
- Staff can view: meetup events (check-ins, undos), ban/unban history for any guest
- Admins can view: complete audit log, query by staff member

Note: "Events" here refers to audit log entries, not Mazmo "eventos" (meetups).

---

## Configuration

### Environment Variables

| Variable                      | Required | Default                     | Description                                                                        |
| ----------------------------- | -------- | --------------------------- | ---------------------------------------------------------------------------------- |
| `DATABASE_URL`                | Yes      | -                           | PostgreSQL connection string (e.g., `postgresql+psycopg://user:pass@host:5432/db`) |
| `SECRET_KEY`                  | Yes      | -                           | JWT signing key. Generate with: `openssl rand -hex 32`                             |
| `ALGORITHM`                   | No       | `HS256`                     | JWT signing algorithm                                                              |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No       | `720`                       | JWT token lifetime (12 hours)                                                      |
| `JSON_LOGS`                   | No       | `false`                     | Set `true` for JSON output (CloudWatch)                                            |
| `LOG_LEVEL`                   | No       | `INFO`                      | Minimum log level: DEBUG, INFO, WARNING, ERROR                                     |
| `BACKEND_PORT`                | No       | `8000`                      | Port uvicorn listens on                                                            |
| `DEBUG`                       | No       | `false`                     | Enable SQLAlchemy SQL logging (never in prod)                                      |
| `MAZMO_BASE_URL`              | No       | `https://prod.mazmoapi.net` | Mazmo API base URL                                                                 |
| `MAZMO_USER_BATCH_SIZE`       | No       | `30`                        | Max user IDs per Mazmo request                                                     |
| `MAZMO_REQUEST_TIMEOUT`       | No       | `15.0`                      | Mazmo API timeout in seconds                                                       |

### Local Development (devenv.nix)

The `devenv.nix` file automatically sets all required environment variables for local development.

Override any variable by creating a `.env` file (gitignored):

```bash
# .env
LOG_LEVEL=DEBUG
SECRET_KEY=your-custom-key
```

### Production (AWS Secrets Manager)

Secrets are stored in AWS Secrets Manager and fetched automatically:

| Secret Name                               | Format                                   | Description        |
| ----------------------------------------- | ---------------------------------------- | ------------------ |
| `alter-tracker-backend/admin-credentials` | `{"username": "...", "password": "..."}` | Initial admin user |

**Create secrets via AWS CLI:**

```bash
aws secretsmanager create-secret \
  --name "alter-tracker-backend/admin-credentials" \
  --secret-string '{"username": "admin", "password": "secure-password-here"}'
```

**Local override:** Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables, or use defaults (admin/changeme-insecure-123).

---

## Available Commands

All commands are defined in `devenv.nix` and available in the dev shell:

| Command                  | Description                                                |
| ------------------------ | ---------------------------------------------------------- |
| `db-start`               | Start PostgreSQL Docker container                          |
| `db-stop`                | Stop PostgreSQL container                                  |
| `db-teardown`            | Remove PostgreSQL container and volume (destructive)       |
| `db-migrate`             | Run Alembic migrations (`alembic upgrade head`)            |
| `db-revision "message"`  | Generate new migration (`alembic revision --autogenerate`) |
| `dev-backend`            | Start FastAPI with hot-reload on port 8000                 |
| `seed-admin`             | Create initial admin user (idempotent)                     |
| `run-tests`              | Run pytest test suite                                      |
| `coverage`               | Run tests with coverage, generate HTML report in `htmlcov/`|
| `lint`                   | Run ruff linter                                            |
| `format`                 | Run ruff formatter                                         |
| `export-aws-credentials` | Export AWS credentials as env vars (for OpenTofu)          |

---

## Database

### Schema Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  user_roles  │     │    users     │     │   guests     │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ id           │◄────│ role_id      │     │ mazmo_user_id│ (PK - from Mazmo)
│ name         │     │ username     │     │ username     │
│ (STAFF,ADMIN)│     │ hashed_pass  │     │ displayname  │
└──────────────┘     │ is_approved  │     │ is_banned    │
                     │ is_disabled  │     │ banned_by_id │──►users.id
                     └──────────────┘     └──────────────┘
                            │                    │
                            ▼                    │
                     ┌──────────────┐            │
                     │   meetups    │            │
                     ├──────────────┤            │
                     │ id (UUID)    │            │
                     │ name         │            │
                     │ date         │            │
                     │ mazmo_url    │            │
                     └──────────────┘            │
                            │                    │
                            ▼                    ▼
                     ┌────────────────────────────────┐
                     │         meetup_rsvps           │
                     ├────────────────────────────────┤
                     │ meetup_id + guest_id (PK)      │
                     │ rsvp_time                      │
                     │ cancelled_rsvp                 │
                     │ has_arrived                    │
                     │ arrival_time, arrival_order    │
                     │ checked_in_by_id ──────────────┼──► users.id
                     └────────────────────────────────┘

┌──────────────────────────────────┐
│           event_log              │  (Audit trail)
├──────────────────────────────────┤
│ id, event_type, timestamp        │
│ actor_id ────────────────────────┼──► users.id
│ guest_id ────────────────────────┼──► guests.mazmo_user_id
│ meetup_id ───────────────────────┼──► meetups.id
│ reason                           │
└──────────────────────────────────┘
```

### Migrations

```bash
# Create a new migration after modifying models
db-revision "add new_field to guests"

# Review the generated migration
cat alembic/versions/XXXX_add_new_field_to_guests.py

# Apply migrations
db-migrate
```

Migration files live in `alembic/versions/` with sequential prefixes (0001_, 0002_, etc.).

---

## Mazmo Integration

**URL Transformation:**
```
Frontend: https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217
API:      https://prod.mazmoapi.net/communities/eventos-reuniones-argentina/threads/alter-cordoba-4217
```

**Two-step fetch:**
1. `GET /threads/{slug}` → Returns RSVP list with user IDs and timestamps
2. `GET /users?ids=123,456,...` → Returns user details (username, displayname)

**Error handling:**
- `MazmoNetworkError` → HTTP 504 (Mazmo unreachable)
- `MazmoAPIError` → HTTP 502 (Mazmo returned error)

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
{"timestamp": "2024-01-15T10:30:00", "level": "info", "event": "Checking in guest", "guest_id": 12345, "request_id": "a1b2c3d4"}
```

**Request tracing:**
- Every log includes `request_id` for correlation
- Pass `X-Request-ID` header for distributed tracing
- Response includes `X-Request-ID` header

---

## Infrastructure (AWS)

The `infra/` directory contains OpenTofu/Terraform configuration:

| File           | Purpose                                    |
| -------------- | ------------------------------------------ |
| `ec2.tf`       | EC2 instance (t3.micro, Amazon Linux 2023) |
| `vpc.tf`       | VPC, subnets, security groups              |
| `secrets.tf`   | Secrets Manager secrets                    |
| `ssm.tf`       | SSM for instance management                |
| `providers.tf` | AWS provider config                        |
| `locals.tf`    | Shared variables                           |

**Deploy:**
```bash
cd infra
export-aws-credentials  # If using SSO
tofu init
tofu plan
tofu apply
```

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

### Add a New Configuration Variable

1. Add field to `app/core/config.py`
2. Set in `devenv.nix` for local dev
3. Document in this README

---

## Troubleshooting

### "database_url is required" / "secret_key is required"

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

Re-authenticate and export credentials:
```bash
aws sso login
export-aws-credentials
```

### "Cannot connect to database"

Ensure PostgreSQL is running:
```bash
db-start
docker ps  # Should show alter-tracker-postgres
```

### Check-in not working / "Guest not RSVPed"

Sync the guest list from Mazmo first via `POST /meetups/{meetup_id}/sync`.

### Port 8000 already in use

Kill the other process or use a different port:
```bash
lsof -i :8000
kill <PID>
# or
BACKEND_PORT=8001 dev-backend
```
