#!/bin/bash
# Deploys one commit on the OCI instance. Do not run it directly: the CD
# workflow calls /usr/local/bin/alter-deploy <sha> through OCI Run Command,
# and alter-deploy downloads the release and runs this script as root.
#
# Usage: deploy.sh <full commit sha> <release dir>
#
# - Reads configuration from instance metadata (set by infra/oci) and
#   secrets from OCI Vault with the instance principal. Secrets only live in
#   this process's environment; nothing is written to disk.
# - Runs the images tagged sha-<short sha>. Redeploying an older sha is the
#   rollback.
# - Installs the host files in deploy/host/ from the same release.

set -euo pipefail

sha="$1"
release_dir="$2"

OCI="/opt/oci-cli/bin/oci"
BASE="/opt/alter-tracker"
LOG_DIR="/var/log/alter-tracker"
KEEP_RELEASES=5

mkdir -p "$LOG_DIR"
# Run Command only returns the last 1 KB of output: the full log goes to a
# file that the Unified Monitoring Agent ships to OCI Logging.
exec > >(tee -a "$LOG_DIR/deploy.log") 2>&1
echo "== Deploy ${sha} started at $(date -u +%FT%TZ)"

# -- Configuration from instance metadata --

instance="$(curl -sf -H 'Authorization: Bearer Oracle' http://169.254.169.254/opc/v2/instance/)"
meta() {
  jq -er --arg k "$1" '.metadata[$k]' <<<"$instance"
}

OCI_REGION="$(jq -er .canonicalRegionName <<<"$instance")"
OCI_COMPARTMENT_ID="$(jq -er .compartmentId <<<"$instance")"
OCI_NAMESPACE="$(meta oci_namespace)"
BACKUP_BUCKET="$(meta backup_bucket)"
export OCI_REGION OCI_COMPARTMENT_ID OCI_NAMESPACE BACKUP_BUCKET

vault_id="$(meta vault_id)"

# -- Secrets from OCI Vault --

read_secret() {
  "$OCI" secrets secret-bundle get-secret-bundle-by-name \
    --auth instance_principal \
    --region "$OCI_REGION" \
    --vault-id "$vault_id" \
    --secret-name "$1" \
    --query 'data."secret-bundle-content".content' \
    --raw-output | base64 -d
}

app_secrets="$(read_secret "$(meta app_secret_name)")"
field() {
  if ! jq -er --arg k "$1" '.[$k]' <<<"$app_secrets"; then
    echo "Cannot deploy: the Vault secret $(meta app_secret_name) has no '$1' field." \
      "Populate it as described in README, \"Production secrets\"." >&2
    exit 1
  fi
}

DB_USER="$(field db_user)"
DB_PASSWORD="$(field db_password)"
JWT_SIGNING_KEY="$(field jwt_signing_key)"
ADMIN_USERNAME="$(field admin_username)"
ADMIN_PASSWORD="$(field admin_password)"
TUNNEL_TOKEN="$(read_secret "$(meta tunnel_secret_name)")"
export DB_USER DB_PASSWORD JWT_SIGNING_KEY ADMIN_USERNAME ADMIN_PASSWORD TUNNEL_TOKEN
unset app_secrets

# Same tag the CD workflow pushes (docker/metadata-action, format=short).
IMAGE_TAG="sha-${sha:0:7}"
export IMAGE_TAG

# -- Containers --

compose=(docker compose -f "$release_dir/docker-compose.yml")

echo "Pulling ${IMAGE_TAG}..."
"${compose[@]}" pull --quiet

fail_with_logs() {
  echo "Deploy FAILED: $1 Last logs:"
  "${compose[@]}" logs --tail 40 migrate app
  exit 1
}

echo "Starting services..."
# Fails when migrate does not complete (for example a broken migration).
"${compose[@]}" up -d --remove-orphans || fail_with_logs "docker compose up failed."

echo "Waiting for the app health check..."
healthy=false
for _ in $(seq 1 90); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null; then
    healthy=true
    break
  fi
  sleep 2
done

[[ "$healthy" == true ]] || fail_with_logs "the app did not answer /health within 3 minutes."

# -- Host files from this release --

install -m 755 "$release_dir/deploy/host/push-metrics.sh" /usr/local/bin/alter-push-metrics
install -m 644 "$release_dir/deploy/host/alter-tracker.cron" /etc/cron.d/alter-tracker
ln -sfn "$release_dir" "$BASE/current"

# -- Cleanup --

docker image prune -af --filter "until=168h" >/dev/null
# Keep the newest releases, never the one just deployed. grep exits 1 on
# empty input, which pipefail would turn into a failed deploy.
find "$BASE/releases" -mindepth 1 -maxdepth 1 -type d -not -name '.*' -printf '%T@ %p\n' |
  sort -rn | tail -n "+$((KEEP_RELEASES + 1))" | cut -d' ' -f2- |
  { grep -vxF "$release_dir" || true; } | xargs -r rm -rf

echo "== Deploy ${sha} OK (${IMAGE_TAG})"
