# Database Backup Design

**Date:** 2026-04-12
**Status:** Approved

## Goal

Automatically back up the PostgreSQL database to S3 every 2 hours. Backups are retained for 90 days and then expired by a lifecycle rule.

## Components

### `infra/backup.tf` (new)

- `aws_s3_bucket` — `alter-tracker-backend-db-backups-{account_id}` (account ID suffix ensures global uniqueness)
- `aws_s3_bucket_public_access_block` — block all public access
- `aws_s3_bucket_lifecycle_configuration` — expire all objects after 90 days
- `aws_iam_role_policy` on the existing `ec2_ssm_role` — grants `s3:PutObject` on the backup bucket. The container inherits the instance IAM role via the metadata endpoint (hop limit 2 is already configured on the instance).

### `docker/backup/Dockerfile` (new)

- Base: `postgres:18-alpine` — provides `pg_dump` at the same version as the `db` service
- Installs `aws-cli` via apk
- Copies and runs `backup.sh`

### `docker/backup/backup.sh` (new)

Infinite loop:
1. Run `pg_dump $DATABASE_URL | gzip | aws s3 cp - s3://$BACKUP_BUCKET/backup-$(date +%Y%m%d_%H%M%S).sql.gz`
2. Log success or failure
3. `sleep 7200`

### `docker-compose.yml` (updated)

New `backup` service:
- Image: `ghcr.io/club-vanta/alter-tracker-backend-backup:latest`
- `restart: unless-stopped`
- `depends_on: db` (service_healthy)
- Environment: `DATABASE_URL` (postgresql:// format for pg_dump), `BACKUP_BUCKET=alter-tracker-backend-db-backups-784421200272` (hardcoded — docker-compose.yml is bundled as a plain file in user_data, not a templatefile, so Terraform outputs can't be injected), `AWS_DEFAULT_REGION=us-east-1`
- `logging:` block with `awslogs` driver, stream `backup` — consistent with all other services

### `.github/workflows/cd.yml` (updated)

New `build-and-push-backup` job:
- Builds `docker/backup/` context
- Pushes `ghcr.io/club-vanta/alter-tracker-backend-backup:latest`
- Same platform (`linux/arm64`), same GHCR auth pattern as the existing job

### `scripts/deploy.sh` (updated)

Change `docker compose pull app` → `docker compose pull` so all services (including backup) are pulled on deploy.

## Backup Format

```
s3://alter-tracker-backend-db-backups-{account_id}/backup-YYYYMMDD_HHMMSS.sql.gz
```

Plain SQL dump, gzip-compressed. Restore with:
```bash
aws s3 cp s3://.../<filename> - | gunzip | psql $DATABASE_URL
```

## Deployment

1. `tofu apply` — creates S3 bucket and grants IAM permissions
2. Push to `main` — CI builds and pushes the backup image to GHCR
3. `deploy.sh` on the instance — pulls the new image and starts the backup container
