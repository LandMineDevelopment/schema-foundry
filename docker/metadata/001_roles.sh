#!/bin/sh
set -eu

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set migration_password="$SCHEMII_METADATA_MIGRATION_PASSWORD" \
  --set schemii_password="$SCHEMII_METADATA_SCHEMII_PASSWORD" \
  --set schemer_password="$SCHEMII_METADATA_SCHEMER_PASSWORD" <<-'SQL'
CREATE ROLE schemii_metadata_owner NOLOGIN;
CREATE ROLE schemii_metadata_migration LOGIN PASSWORD :'migration_password';
CREATE ROLE schemii_metadata_schemii LOGIN PASSWORD :'schemii_password';
CREATE ROLE schemii_metadata_schemer LOGIN PASSWORD :'schemer_password';

ALTER DATABASE schemii_metadata OWNER TO schemii_metadata_owner;
REVOKE ALL ON DATABASE schemii_metadata FROM PUBLIC;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_migration;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_schemii;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_schemer;
GRANT schemii_metadata_owner TO schemii_metadata_migration;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO schemii_metadata_owner;
GRANT USAGE ON SCHEMA public TO schemii_metadata_schemii, schemii_metadata_schemer;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO schemii_metadata_schemii, schemii_metadata_schemer;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO schemii_metadata_schemii, schemii_metadata_schemer;
ALTER ROLE schemii_metadata_bootstrap NOLOGIN;
SQL
