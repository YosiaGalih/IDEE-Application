#!/bin/sh
# Runs once, when the data folder is created (docker-entrypoint-initdb.d). Creates the runtime role that
# api, worker and scheduler use: not a superuser, owns nothing. Its table privileges come from
# `python -m app.cli grant-runtime-role` after every migration (migrate service), so audit_events stays
# append-only for it (T10).
set -eu
: "${DB_APP_PASSWORD:?DB_APP_PASSWORD is required}"
psql -v ON_ERROR_STOP=1 -v app_password="$DB_APP_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE kodekantor_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
REVOKE ALL ON DATABASE kodekantor FROM PUBLIC;
GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
