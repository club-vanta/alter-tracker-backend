#!/bin/bash
# Run this on the EC2 instance to start (or update) the application.
# The instance IAM role provides access to AWS Secrets Manager — no credentials needed.
# Secrets are loaded into shell environment variables only — nothing is written to disk.
#
# Usage:
#   ./scripts/deploy.sh
#
# On first run: pulls the image, runs migrations, seeds the admin user, starts the app.
# On subsequent runs: pulls the latest image and restarts the app.

set -euo pipefail

REGION="us-east-1"
SECRET_ID="alter-tracker-backend/secrets"
COMPOSE_FILE="$(dirname "$0")/../docker-compose.yml"

echo "Fetching secrets from AWS Secrets Manager..."
SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ID" \
  --region "$REGION" \
  --query SecretString \
  --output text)

extract() {
  echo "$SECRET_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['$1'])"
}

DB_USER=$(extract db_user);         export DB_USER
DB_PASSWORD=$(extract db_password); export DB_PASSWORD
JWT_SIGNING_KEY=$(extract jwt_signing_key); export JWT_SIGNING_KEY
ADMIN_USERNAME=$(extract admin_username);   export ADMIN_USERNAME
ADMIN_PASSWORD=$(extract admin_password);   export ADMIN_PASSWORD

echo "Pulling latest image..."
docker compose -f "$COMPOSE_FILE" pull app

echo "Starting services..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "Done. App is running on :8000"
