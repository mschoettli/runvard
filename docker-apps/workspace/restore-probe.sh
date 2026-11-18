#!/bin/sh
set -eu

test -f /run/workspace-probe/source.dump
export PGPASSWORD="$(cat /run/secrets/workspace_migration_password)"

pg_restore \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  /run/workspace-probe/source.dump

unset PGPASSWORD
