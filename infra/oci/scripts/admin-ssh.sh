#!/bin/bash
# Opens a shell on the server as `opc` (passwordless sudo) through OCI
# Bastion, or runs one command and exits. Works without a terminal, so
# agents can use it too.
#
# Usage (from the repo root):
#   infra/oci/scripts/admin-ssh.sh                      # interactive shell
#   infra/oci/scripts/admin-ssh.sh 'sudo docker ps'     # one command
#
#   SSH_KEY      private key whose .pub is sent to the session
#                (default ~/.ssh/id_ed25519)
#   BASTION_ID   default: `tofu output bastion_id` in infra/oci
#   INSTANCE_ID  default: `tofu output instance_id` in infra/oci
#
# Each call creates a 1-hour session (about 30 seconds of setup).

set -euo pipefail

key="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
region="${OCI_CLI_REGION:-sa-valparaiso-1}"
root="$(cd "$(dirname "$0")/.." && pwd)"
bastion="${BASTION_ID:-$(tofu -chdir="$root" output -raw bastion_id)}"
instance="${INSTANCE_ID:-$(tofu -chdir="$root" output -raw instance_id)}"

ip="$(oci compute instance list-vnics --instance-id "$instance" \
  --query 'data[0]."private-ip"' --raw-output)"

session="$(oci bastion session create-managed-ssh \
  --bastion-id "$bastion" \
  --target-resource-id "$instance" \
  --target-private-ip "$ip" \
  --target-os-username opc \
  --ssh-public-key-file "$key.pub" \
  --key-type PUB \
  --session-ttl 3600 \
  --display-name "admin-$(date -u +%Y%m%d%H%M%S)" \
  --wait-for-state SUCCEEDED \
  --query 'data.resources[0].identifier' --raw-output)"

proxy="ssh -i $key -o StrictHostKeyChecking=accept-new -W %h:%p -p 22 $session@host.bastion.$region.oci.oraclecloud.com"
exec ssh -i "$key" -o StrictHostKeyChecking=accept-new -o ProxyCommand="$proxy" "opc@$ip" "$@"
