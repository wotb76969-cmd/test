#!/usr/bin/env bash
# Applies 001 -> 003 to a THROWAWAY local PostgreSQL database, checks RLS isolation,
# re-applies 003 (idempotency), rolls it back and applies it again.
# Usage: PGHOST=... PGPORT=... PGUSER=postgres ./run_local_pg_test.sh   (never point it at production)
set -euo pipefail
cd "$(dirname "$0")"
DB=chelsy_rls_test_$$
psql -v ON_ERROR_STOP=1 -q -c "create database $DB"
trap 'psql -q -c "drop database if exists $DB" >/dev/null' EXIT
run() { psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$1"; }
run supabase_shim.sql
run ../001_chelsy_ai_data.sql
run ../002_cloud_sync_indexes.sql
run ../003_conversations_soft_delete.sql
run ../003_conversations_soft_delete.sql      # idempotent
run rls_isolation_test.sql
run ../003_rollback.sql
psql -v ON_ERROR_STOP=1 -q -d "$DB" -c "select count(*) as messages_kept_after_rollback from public.chelsy_ai_messages"
run ../003_conversations_soft_delete.sql
echo "Supabase migration test: OK"
