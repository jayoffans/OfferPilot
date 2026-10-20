#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv migrator_password S5_MIGRATOR_PASSWORD
\getenv app_password S5_APP_PASSWORD
CREATE ROLE s5_migrator LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION PASSWORD :'migrator_password';
CREATE ROLE s5_app LOGIN NOCREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
CREATE DATABASE offerpilot_s5_test OWNER s5_migrator;
REVOKE ALL ON DATABASE offerpilot_s5_test FROM PUBLIC;
GRANT CONNECT ON DATABASE offerpilot_s5_test TO s5_app;
SQL
