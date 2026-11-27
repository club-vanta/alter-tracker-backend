#!/bin/bash
# Deploys a commit to the OCI server through Run Command and waits for it.
#
# Usage: oci-deploy.sh <full 40-char commit sha>
#
# Needs the OCI CLI with API key auth in the environment (OCI_CLI_USER,
# OCI_CLI_TENANCY, OCI_CLI_FINGERPRINT, OCI_CLI_KEY_CONTENT, OCI_CLI_REGION)
# plus OCI_COMPARTMENT_ID and OCI_INSTANCE_ID.
#
# The command runs `sudo /usr/local/bin/alter-deploy <sha>` on the server
# (installed by infra/oci/cloud-init.sh). Run Command returns only the last
# 1 KB of output; the full deploy log is in OCI Logging.

set -euo pipefail

sha="${1:-}"
if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: $0 <full 40-char commit sha>" >&2
  exit 2
fi
: "${OCI_COMPARTMENT_ID:?}" "${OCI_INSTANCE_ID:?}"

TIMEOUT_SECONDS=1200
POLL_SECONDS=10

request="$(mktemp)"
trap 'rm -f "$request"' EXIT

jq -n \
  --arg compartment "$OCI_COMPARTMENT_ID" \
  --arg instance "$OCI_INSTANCE_ID" \
  --arg sha "$sha" \
  --argjson timeout "$TIMEOUT_SECONDS" '{
    compartmentId: $compartment,
    displayName: ("deploy-" + $sha[0:7]),
    timeoutInSeconds: $timeout,
    target: {instanceId: $instance},
    content: {
      source: {sourceType: "TEXT", text: ("sudo /usr/local/bin/alter-deploy " + $sha)},
      output: {outputType: "TEXT"}
    }
  }' >"$request"

command_id="$(oci instance-agent command create --from-json "file://$request" --query data.id --raw-output)"
echo "Started Run Command ${command_id} for ${sha}"

deadline=$((SECONDS + TIMEOUT_SECONDS + 120))
state="UNKNOWN"
execution=""
while ((SECONDS < deadline)); do
  sleep "$POLL_SECONDS"
  # The execution is not readable until the agent picks the command up.
  if ! execution="$(oci instance-agent command-execution get \
    --command-id "$command_id" --instance-id "$OCI_INSTANCE_ID" 2>/dev/null)"; then
    echo "Waiting for the agent to pick up the command..."
    continue
  fi
  state="$(jq -r '.data["lifecycle-state"]' <<<"$execution")"
  case "$state" in
    SUCCEEDED | FAILED | TIMED_OUT | CANCELED) break ;;
    *) echo "State: ${state}" ;;
  esac
done

echo "-- Last output from the server --"
jq -r '.data.content.text // "(no output)"' <<<"$execution"
echo "--"

if [[ "$state" != "SUCCEEDED" ]]; then
  echo "Deploy failed: Run Command ended in state ${state}." >&2
  exit 1
fi

exit_code="$(jq -r '.data.content["exit-code"] // 0' <<<"$execution")"
if [[ "$exit_code" != "0" ]]; then
  echo "Deploy failed: alter-deploy exited with code ${exit_code}." >&2
  exit 1
fi

echo "Deploy of ${sha} succeeded."
