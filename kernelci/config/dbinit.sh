#!/bin/bash
# Initialize the KCIDB PostgreSQL database.
#
# This is a wrapper around the upstream kcidb-ng dbinit.sh that uses
# configurable hostnames via environment variables instead of hardcoded 'db'.
#
# Required env vars:
#   PGPASSWORD  — PostgreSQL superuser password
#   PG_URI      — kcidb connection string for schema init
#   PG_HOST     — PostgreSQL hostname (default: kcidb-db)

set -e

PG_HOST="${PG_HOST:-kcidb-db}"
PG_PASS="${PGPASSWORD:-kcidb}"

echo "Waiting for database at ${PG_HOST}..."
while ! pg_isready -h "$PG_HOST" -U postgres; do
  sleep 1
done
echo "Database is ready."

# Check if already initialized
if psql -h "$PG_HOST" -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='kcidb'" | grep -qw 1; then
  echo "Database kcidb already exists. Skipping initialization."
  exit 0
fi

echo "Creating database and roles..."
psql -h "$PG_HOST" -U postgres -c "CREATE ROLE kcidb WITH LOGIN PASSWORD '${PG_PASS}';"
psql -h "$PG_HOST" -U postgres -c "CREATE DATABASE kcidb WITH OWNER kcidb;"
psql -h "$PG_HOST" -U postgres -c "CREATE ROLE kcidb_editor WITH LOGIN PASSWORD '${PG_PASS}';"
psql -h "$PG_HOST" -U postgres -d kcidb -c \
  "ALTER SCHEMA public OWNER TO kcidb;
   GRANT USAGE, CREATE ON SCHEMA public TO kcidb, kcidb_editor;"
psql -h "$PG_HOST" -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE kcidb TO kcidb_editor;"
psql -h "$PG_HOST" -U postgres -c "GRANT USAGE, CREATE ON SCHEMA public TO kcidb_editor;"
psql -h "$PG_HOST" -U postgres -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO kcidb_editor;"
psql -h "$PG_HOST" -U postgres -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO kcidb_editor;"
psql -h "$PG_HOST" -U postgres -c "GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO kcidb_editor;"

echo "Initializing KCIDB schema..."
kcidb-db-init -d "${PG_URI}" --ignore-initialized

psql -h "$PG_HOST" -U postgres -c "CREATE ROLE kcidb_viewer WITH LOGIN PASSWORD '${PG_PASS}';"
psql -h "$PG_HOST" -U postgres -c "GRANT CONNECT ON DATABASE kcidb TO kcidb_viewer;"
psql -h "$PG_HOST" -U postgres -c "GRANT USAGE ON SCHEMA public TO kcidb_viewer;"
psql -h "$PG_HOST" -U postgres -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO kcidb_viewer;"
psql -h "$PG_HOST" -U postgres -c "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO kcidb_viewer;"

echo "Database initialized."
