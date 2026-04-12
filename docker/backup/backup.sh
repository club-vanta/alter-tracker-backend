#!/bin/sh
set -e

echo "Backup service started. Running every 2 hours."

while true; do
  FILENAME="backup-$(date +%Y%m%d_%H%M%S).sql.gz"
  echo "Starting backup: ${FILENAME}"

  if pg_dump "${DATABASE_URL}" | gzip | aws s3 cp - "s3://${BACKUP_BUCKET}/${FILENAME}"; then
    echo "Backup complete: ${FILENAME}"
  else
    echo "Backup FAILED: ${FILENAME}" >&2
  fi

  sleep 7200
done
