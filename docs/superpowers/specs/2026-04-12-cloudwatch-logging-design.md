# CloudWatch Logging Design

**Date:** 2026-04-12
**Status:** Approved

## Goal

Ship all Docker container logs (app, db, migrate, seed) to AWS CloudWatch Logs so they can be viewed, searched, and retained without SSHing into the instance.

## Approach

Use Docker's built-in `awslogs` log driver. Each service in `docker-compose.yml` gets a `logging:` block pointing at a single CloudWatch log group. Docker ships logs directly — no CloudWatch Agent needed.

## Components

### `infra/logging.tf` (new)

- `aws_cloudwatch_log_group` — `/alter-tracker-backend`, 30-day retention
- `aws_iam_role_policy` — attached to the existing `ec2_ssm_role`, grants `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` on the log group ARN

### `docker-compose.yml` (updated)

Add a `logging:` block to each service:

```yaml
logging:
  driver: awslogs
  options:
    awslogs-region: us-east-1
    awslogs-group: /alter-tracker-backend
    awslogs-stream: <service-name>   # app | db | migrate | seed
```

## Log Structure

| Log Group              | Stream    | Format |
|------------------------|-----------|--------|
| `/alter-tracker-backend` | `app`   | JSON (JSON_LOGS=true) |
| `/alter-tracker-backend` | `db`    | Plain text (Postgres) |
| `/alter-tracker-backend` | `migrate` | Plain text |
| `/alter-tracker-backend` | `seed`  | Plain text |

## Querying

In CloudWatch Logs Insights, select the `/alter-tracker-backend` log group and filter by `@logStream` to isolate a service, or query all streams together.

## Deployment

1. `tofu apply` — creates the log group and grants IAM permissions
2. Re-deploy the app (`deploy.sh` on the instance) — Docker picks up the new log driver config
