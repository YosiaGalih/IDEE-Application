#!/bin/sh
# Runs once, when the data folder is created (docker-entrypoint-initdb.d). Creates the runtime role that
# api, worker and scheduler use: not a superuser, owns nothing. Its table privileges come from
# `python -m app.cli grant-runtime-role` after every migration (migrate service), so audit_events stays
# append-only for it (T10).
#
# The password never reaches argv (psql reads it with \getenv) nor the server log: this session logs no
# statements, and a failing one is not echoed (log_min_error_statement).
set -eu
: "${DB_APP_PASSWORD:?DB_APP_PASSWORD is required}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = panic;
\getenv app_password DB_APP_PASSWORD
CREATE ROLE kodekantor_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
REVOKE ALL ON DATABASE kodekantor FROM PUBLIC;
GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
