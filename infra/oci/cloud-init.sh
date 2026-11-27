#!/bin/bash
# First-boot bootstrap for Oracle Linux 9 (aarch64). Rendered by templatefile()
# in compute.tf, so shell variables are written $${LIKE_THIS}.
#
# Installs Docker, mounts the data volume at /var/lib/docker, installs the
# OCI CLI and the deploy entry point. It does not deploy: the first deploy
# comes from the CD workflow once the Vault secret is populated.

set -euxo pipefail
exec > >(tee -a /var/log/alter-tracker-bootstrap.log) 2>&1

# -- Packages --

dnf install -y dnf-plugins-core jq cronie python3.12
dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# -- Data volume --
# Terraform attaches it after the instance boots, so wait for it.

DEVICE=/dev/oracleoci/oraclevdb
for _ in $(seq 1 120); do
  [ -e "$DEVICE" ] && break
  sleep 5
done
if [ ! -e "$DEVICE" ]; then
  echo "Data volume never appeared at $DEVICE" >&2
  exit 1
fi

# Format only a blank volume: a reattached one keeps its data.
if ! blkid "$DEVICE"; then
  mkfs.xfs "$DEVICE"
fi
UUID="$(blkid -s UUID -o value "$DEVICE")"
mkdir -p /var/lib/docker
# nofail keeps the machine bootable (and reachable through Bastion) without
# the volume, but Docker must never start without it: on the boot disk,
# Postgres would create a new, empty database and the app would serve it.
grep -q "$UUID" /etc/fstab ||
  echo "UUID=$UUID /var/lib/docker xfs defaults,nofail,x-systemd.before=docker.service 0 2" >>/etc/fstab
mkdir -p /etc/systemd/system/docker.service.d
cat >/etc/systemd/system/docker.service.d/require-data-volume.conf <<'UNIT'
[Unit]
RequiresMountsFor=/var/lib/docker
UNIT
systemctl daemon-reload
mount /var/lib/docker
restorecon -R /var/lib/docker

systemctl enable --now docker crond

# -- OCI CLI (instance principal auth, used by deploy.sh and push-metrics.sh) --

python3.12 -m venv /opt/oci-cli
/opt/oci-cli/bin/pip install --quiet "oci-cli==${oci_cli_version}"

# -- Deploy entry point --
# Run Command executes as the ocarun user, which may only run alter-deploy.

mkdir -p /opt/alter-tracker/releases /var/log/alter-tracker
echo "${alter_deploy_b64}" | base64 -d >/usr/local/bin/alter-deploy
chmod 755 /usr/local/bin/alter-deploy

cat >/etc/sudoers.d/90-alter-deploy <<'EOF'
ocarun ALL=(root) NOPASSWD: /usr/local/bin/alter-deploy
EOF
chmod 440 /etc/sudoers.d/90-alter-deploy
visudo -cf /etc/sudoers.d/90-alter-deploy

echo "Bootstrap complete"
