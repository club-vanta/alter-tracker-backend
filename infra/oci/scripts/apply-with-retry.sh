#!/bin/bash
# Creates the A1 instance, retrying while OCI answers "Out of host capacity".
#
# Use it only after a normal `tofu apply` created everything else and failed
# on the instance for lack of capacity: it targets the instance (and what
# depends on it) and applies without asking, so review that plan first.
#
# Usage (from infra/oci): scripts/apply-with-retry.sh
#   MAX_ATTEMPTS         default 48
#   RETRY_DELAY_SECONDS  default 600 (48 x 10 minutes = 8 hours)

set -uo pipefail

MAX_ATTEMPTS="${MAX_ATTEMPTS:-48}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-600}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "== Attempt ${attempt}/${MAX_ATTEMPTS} at $(date -u +%FT%TZ)"
  tofu apply -auto-approve -input=false -target=oci_core_instance.app 2>&1 | tee "$log"
  status="${PIPESTATUS[0]}"

  if [[ "$status" -eq 0 ]]; then
    echo "Instance created. Run a full 'tofu apply' to create what depends on it."
    exit 0
  fi
  if ! grep -q "Out of host capacity" "$log"; then
    echo "Failed for a reason other than capacity: not retrying." >&2
    exit "$status"
  fi

  echo "Out of host capacity. Retrying in ${RETRY_DELAY_SECONDS}s."
  sleep "$RETRY_DELAY_SECONDS"
done

echo "Still no capacity after ${MAX_ATTEMPTS} attempts." >&2
exit 1
