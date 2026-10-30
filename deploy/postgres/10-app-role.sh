#!/bin/sh
set -eu
# Only the database container receives its admin password. Applications use a
# separate non-superuser role and own only their database/schema.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_role="$APP_ROLE" --set=app_password="$APP_PASSWORD" --set=app_database="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"app_role" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE :"app_database" OWNER TO :"app_role";
GRANT ALL ON SCHEMA public TO :"app_role";
SQL
