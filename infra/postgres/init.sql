-- Role setup — runs after schema.sql (prefix 02_)
-- Owner: database-architect + security-compliance
-- Passwords: Coolify Magic Variables (SERVICE_PASSWORD_*) set at runtime.
-- Local dev: psql -v app_writer_pw='...' ... (override) or use defaults below.
--
-- IMPORTANT: psql `:'var'` interpolation is suppressed inside dollar-quoted
-- bodies (`$$ ... $$`), so passwords cannot be substituted directly into a
-- DO block. We split the operation:
--   1) DO block creates the role *without* a password (idempotent, no var
--      substitution required).
--   2) ALTER ROLE … PASSWORD :'var' runs at the top level where psql does
--      interpolate `:'var'` and properly quotes the literal.
-- The two statements are stable and safe to re-run.

\set app_writer_pw `echo "${SERVICE_PASSWORD_APP_WRITER:-local_writer_pw}"`
\set app_reader_pw `echo "${SERVICE_PASSWORD_APP_READER:-local_reader_pw}"`
\set grafana_ro_pw `echo "${SERVICE_PASSWORD_GRAFANA_RO:-local_grafana_pw}"`

-- 1) Idempotent role creation (no password yet — set in step 2).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_writer') THEN
        CREATE ROLE app_writer LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_reader') THEN
        CREATE ROLE app_reader LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro LOGIN;
    END IF;
END
$$;

-- 2) Password assignment at the top level — `:'var'` is interpolated and
-- quoted by psql here.
ALTER ROLE app_writer WITH PASSWORD :'app_writer_pw';
ALTER ROLE app_reader WITH PASSWORD :'app_reader_pw';
ALTER ROLE grafana_ro WITH PASSWORD :'grafana_ro_pw';

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
