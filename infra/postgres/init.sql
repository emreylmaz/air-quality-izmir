-- Role setup — runs after schema.sql (prefix 02_)
-- Owner: database-architect + security-compliance
-- Passwords: Coolify Magic Variables (SERVICE_PASSWORD_*) set at runtime.
-- Local dev: psql -v app_writer_pw='...' ... (override) or use defaults below.

\set app_writer_pw `echo "${SERVICE_PASSWORD_APP_WRITER:-local_writer_pw}"`
\set app_reader_pw `echo "${SERVICE_PASSWORD_APP_READER:-local_reader_pw}"`
\set grafana_ro_pw `echo "${SERVICE_PASSWORD_GRAFANA_RO:-local_grafana_pw}"`

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_writer') THEN
        CREATE ROLE app_writer LOGIN PASSWORD :'app_writer_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_reader') THEN
        CREATE ROLE app_reader LOGIN PASSWORD :'app_reader_pw';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro LOGIN PASSWORD :'grafana_ro_pw';
    END IF;
END
$$;

-- Grant privileges
GRANT USAGE ON SCHEMA public TO app_writer, app_reader, grafana_ro;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_writer;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_reader, grafana_ro;

-- Future tables get same privileges automatically
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO app_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO app_reader, grafana_ro;
