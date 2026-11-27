#!/bin/bash
# Publishes custom metrics to OCI Monitoring every 5 minutes (see
# alter-tracker.cron). The alarms in infra/oci/monitoring.tf watch them.
#
#   disk_used_pct  per mount (/ and /var/lib/docker)
#   log_errors     error/critical lines from the stack in the last 5 minutes
#   log_warnings   warning lines from the stack in the last 5 minutes
#
# Installed by scripts/deploy.sh as /usr/local/bin/alter-push-metrics.

set -euo pipefail

OCI="/opt/oci-cli/bin/oci"
NAMESPACE="alter_tracker"
WINDOW="5m"

instance="$(curl -sf -H 'Authorization: Bearer Oracle' http://169.254.169.254/opc/v2/instance/)"
instance_id="$(jq -er .id <<<"$instance")"
compartment_id="$(jq -er .compartmentId <<<"$instance")"
region="$(jq -er .canonicalRegionName <<<"$instance")"

disk_pct() {
  df --output=pcent "$1" | tail -1 | tr -dc '0-9'
}

errors=0
warnings=0
for container in $(docker ps -q --filter label=com.docker.compose.project=alter-tracker); do
  # structlog JSON lines carry "level": "<name>".
  logs="$(docker logs --since "$WINDOW" "$container" 2>&1 || true)"
  errors=$((errors + $(grep -cE '"level": "(error|critical)"' <<<"$logs" || true)))
  warnings=$((warnings + $(grep -c '"level": "warning"' <<<"$logs" || true)))
done

payload="$(mktemp)"
trap 'rm -f "$payload"' EXIT

jq -n \
  --arg ns "$NAMESPACE" \
  --arg compartment "$compartment_id" \
  --arg id "$instance_id" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson root "$(disk_pct /)" \
  --argjson data "$(disk_pct /var/lib/docker)" \
  --argjson errors "$errors" \
  --argjson warnings "$warnings" '
  def metric($name; $dims; $value):
    {namespace: $ns, compartmentId: $compartment, name: $name,
     dimensions: ({resourceId: $id} + $dims),
     datapoints: [{timestamp: $ts, value: $value}]};
  [ metric("disk_used_pct"; {mount: "/"}; $root),
    metric("disk_used_pct"; {mount: "/var/lib/docker"}; $data),
    metric("log_errors"; {}; $errors),
    metric("log_warnings"; {}; $warnings) ]' >"$payload"

"$OCI" monitoring metric-data post \
  --auth instance_principal \
  --endpoint "https://telemetry-ingestion.${region}.oraclecloud.com" \
  --metric-data "file://$payload" >/dev/null
