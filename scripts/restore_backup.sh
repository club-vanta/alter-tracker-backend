#!/bin/bash
# Replaces the database with the contents of a dump.
#
# Usage:
#   restore_backup.sh --yes <dump>
#
# <dump> is either a gzipped plain dump made by the backup service
# (*.sql.gz) or a custom-format dump from `pg_dump -Fc` (*.dump).
#
# DESTRUCTIVE: drops and recreates the database. Stop every service that
# writes to it first (app, weather-worker, backup).
#
# Runs on the host and talks to the Postgres container with `docker exec`,
# using the credentials already in that container's environment.
# DB_CONTAINER overrides the container name (tests use their own).

set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-alter-tracker-db-1}"
DB_NAME="alter_event_tracker"

if [[ "${1:-}" != "--yes" || -z "${2:-}" ]]; then
  echo "Usage: $0 --yes <dump.sql.gz|dump.dump>" >&2
  echo "Drops and recreates ${DB_NAME} in ${DB_CONTAINER}." >&2
  exit 2
fi
DUMP="$2"

if [[ ! -f "$DUMP" ]]; then
  echo "Cannot restore: ${DUMP} does not exist." >&2
  exit 1
fi

psql_in_db() {
  docker exec -i "$DB_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -q' _ "$1"
}

echo "Recreating ${DB_NAME} in ${DB_CONTAINER}..."
psql_in_db postgres <<SQL
DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);
CREATE DATABASE ${DB_NAME};
SQL

echo "Restoring ${DUMP}..."
case "$DUMP" in
  *.sql.gz)
    gunzip -c "$DUMP" | psql_in_db "$DB_NAME"
    ;;
  *.dump)
    docker exec -i "$DB_CONTAINER" sh -c \
      'pg_restore --exit-on-error --no-owner --role="$POSTGRES_USER" -U "$POSTGRES_USER" -d "$1"' \
      _ "$DB_NAME" <"$DUMP"
    ;;
  *)
    echo "Cannot restore: ${DUMP} is neither *.sql.gz nor *.dump." >&2
    exit 1
    ;;
esac

echo "Restore complete. Exact row counts (compare with the source):"
psql_in_db "$DB_NAME" <"$(dirname "$0")/sql/row_counts.sql"
