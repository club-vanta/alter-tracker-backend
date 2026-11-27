#!/bin/bash
# End-to-end test of the backup image and scripts/restore_backup.sh.
#
# 1. A backup of a seeded database lands in BACKUP_REMOTE (a local dir).
# 2. A backup whose pg_dump fails exits non-zero and leaves no file behind.
#    (Without pipefail the old script uploaded an empty dump and reported
#    success.)
# 3. Restoring the good dump brings back data deleted after the backup.
#
# Needs Docker. Run from the repo root: tests/infra/test_backup_restore.sh

set -euo pipefail

PREFIX="alter-backup-test-$$"
NETWORK="$PREFIX-net"
DB="$PREFIX-db"
IMAGE="$PREFIX-image"
BACKUP_DIR="$(mktemp -d)"

cleanup() {
  docker rm -f "$DB" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker image rm "$IMAGE" >/dev/null 2>&1 || true
  # Files written by the container may belong to another uid.
  docker run --rm -v "$BACKUP_DIR:/b" alpine:3.20 sh -c 'rm -rf /b/*' >/dev/null 2>&1 || true
  rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

sql() {
  docker exec -i "$DB" psql -v ON_ERROR_STOP=1 -U test -d alter_event_tracker -At -c "$1"
}

run_backup() {
  docker run --rm --network "$NETWORK" \
    -e DATABASE_URL="$1" \
    -e BACKUP_REMOTE=/backups \
    -e BACKUP_ONCE=1 \
    -v "$BACKUP_DIR:/backups" \
    "$IMAGE"
}

backup_count() {
  find "$BACKUP_DIR" -name 'backup-*.sql.gz' | wc -l
}

echo "-- Building backup image"
docker build -q -t "$IMAGE" docker/backup >/dev/null

echo "-- Starting Postgres"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$DB" --network "$NETWORK" \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=alter_event_tracker \
  postgres:18 >/dev/null
for _ in $(seq 1 60); do
  if docker exec "$DB" pg_isready -U test -d alter_event_tracker >/dev/null 2>&1 \
    && sql "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
sql "SELECT 1" >/dev/null || fail "Postgres did not start"

sql "CREATE TABLE guests (id int PRIMARY KEY, username text); INSERT INTO guests VALUES (1, 'alice'), (2, 'bob');"

echo "-- Case 1: successful backup"
run_backup "postgresql://test:test@$DB:5432/alter_event_tracker" || fail "backup exited non-zero"
[[ "$(backup_count)" -eq 1 ]] || fail "expected 1 backup file, found $(backup_count)"
dump="$(find "$BACKUP_DIR" -name 'backup-*.sql.gz')"
gunzip -c "$dump" | grep -q "alice" || fail "dump does not contain the seeded rows"

echo "-- Case 2: failing pg_dump"
sleep 1 # backup file names have one-second resolution
if run_backup "postgresql://test:wrong@$DB:5432/alter_event_tracker"; then
  fail "backup with a failing pg_dump exited 0"
fi
[[ "$(backup_count)" -eq 1 ]] || fail "failed backup left a file behind ($(backup_count) files)"

echo "-- Case 3: restore"
sql "DELETE FROM guests WHERE id = 2;"
# The restore script reads dumps from the host, so copy it somewhere we own.
restore_file="$(mktemp --suffix=.sql.gz)"
docker run --rm -v "$BACKUP_DIR:/b" alpine:3.20 cat "/b/$(basename "$dump")" >"$restore_file"
DB_CONTAINER="$DB" scripts/restore_backup.sh --yes "$restore_file" >/dev/null
rm -f "$restore_file"
[[ "$(sql "SELECT count(*) FROM guests")" == "2" ]] || fail "restore did not bring back the deleted row"
[[ "$(sql "SELECT username FROM guests WHERE id = 2")" == "bob" ]] || fail "restored row has the wrong data"

echo "-- Case 4: restore a custom-format dump (pg_dump -Fc, used at cutover)"
custom_dump="$(mktemp --suffix=.dump)"
docker exec "$DB" pg_dump -Fc -U test alter_event_tracker >"$custom_dump"
sql "DELETE FROM guests;"
DB_CONTAINER="$DB" scripts/restore_backup.sh --yes "$custom_dump" >/dev/null
rm -f "$custom_dump"
[[ "$(sql "SELECT count(*) FROM guests")" == "2" ]] || fail "custom-format restore did not bring back the rows"

echo "PASS: backup and restore"
