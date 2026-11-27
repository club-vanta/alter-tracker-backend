#!/bin/sh
# Dumps the database every BACKUP_INTERVAL_SECONDS and streams the gzipped
# dump to BACKUP_REMOTE with rclone.
#
# BACKUP_REMOTE is any rclone destination: in production an on-the-fly OCI
# Object Storage remote (":oracleobjectstorage:<bucket>"), in tests a local
# directory.
#
# BACKUP_ONCE=1 runs a single backup and exits with its status (used by
# tests and for a manual backup before risky operations).
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_REMOTE:?BACKUP_REMOTE is required}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-7200}"

backup_once() {
  filename="backup-$(date -u +%Y%m%d_%H%M%S).sql.gz"
  echo "Starting backup: ${filename}"

  # pipefail: without it a failed pg_dump still uploads a truncated file
  # and reports success. busybox ash (the image's sh) supports it.
  # shellcheck disable=SC3040
  if (set -o pipefail; pg_dump "${DATABASE_URL}" | gzip | rclone rcat "${BACKUP_REMOTE}/${filename}"); then
    echo "Backup complete: ${filename}"
    return 0
  fi

  echo "Backup FAILED: ${filename}" >&2
  # Do not leave a partial dump behind: a restore would pick it as the latest.
  rclone deletefile "${BACKUP_REMOTE}/${filename}" 2>/dev/null || true
  return 1
}

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
  backup_once
  exit $?
fi

echo "Backup service started. Running every ${INTERVAL} seconds."
while true; do
  backup_once || true
  sleep "${INTERVAL}"
done
