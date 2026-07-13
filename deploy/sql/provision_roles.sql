-- AetherCal — provision the three database roles. RUN THIS AS A SUPERUSER, ONCE, BEFORE MIGRATING.
--
-- ============================================================================================
-- Why this is not an Alembic migration
-- ============================================================================================
--
-- `CREATE ROLE ... BYPASSRLS` requires SUPERUSER, and Alembic runs as `aethercal_owner`, which is
-- not one. A migration therefore cannot mint these roles — so they are provisioned here, out of
-- band, and migration 0008 fails loudly against a database where they do not exist.
--
-- That failure is the correct one. A migration that "succeeded" against a role-less database would
-- leave an instance whose policies exist, whose application still connects as the table owner, and
-- whose isolation is consequently a placebo. It would deploy green and protect nothing.
--
-- ============================================================================================
-- The risk this file exists to close: the roles never reaching production
-- ============================================================================================
--
-- The `postgres` image's init scripts only run when the data volume is EMPTY. A production database
-- has data. So a role-provisioning step living in `docker-entrypoint-initdb.d/` would run perfectly
-- in CI (a fresh database, every time) and **never run at all on the instance that matters** — and
-- the test suite would go green while the live instance stayed unprotected. That is the silent no-op
-- at the scale of a deployment. So this is a runbook step a human executes, with evidence pasted
-- back, and not a file we hope somebody's volume is empty enough to notice.
--
-- ============================================================================================
-- Usage
-- ============================================================================================
--
--   psql "$SUPERUSER_URL" -v ON_ERROR_STOP=1 \
--        -v db=aethercal -v pw_owner=... -v pw_app=... -v pw_worker=... \
--        -f deploy/sql/provision_roles.sql
--
-- Then, and only then:
--
--   AETHERCAL_OWNER_DATABASE_URL=postgresql://aethercal_owner:...@host/aethercal \
--     aethercal-admin db upgrade
--
-- The three passwords are DIFFERENT secrets and belong in the deployment's secret store. They are
-- never committed: this repository is public.

\set ON_ERROR_STOP on

-- --------------------------------------------------------------------------------------------
-- The roles.
--
-- BYPASSRLS is an ATTRIBUTE of a LOGIN role and is *not* inherited through group membership (it
-- behaves like SUPERUSER in that respect). Granting it via a group would leave `guest purge` reading
-- zero rows and exiting green — erasure of personal data, reporting success, having erased nothing.
-- So it goes on the login roles themselves.
-- --------------------------------------------------------------------------------------------

-- Owns every table. Alembic and the CLI connect as this. It needs BYPASSRLS because migration 0008
-- sets FORCE ROW LEVEL SECURITY, under which even the owner becomes subject to its own policies —
-- and `guest purge` has to reach every row of one business, including rows a policy would hide.
CREATE ROLE aethercal_owner LOGIN BYPASSRLS PASSWORD :'pw_owner';

-- The request path and the admin. ==Never BYPASSRLS.== This is the identity that must not be able to
-- see another business's rows, and the whole belt is the statement that it cannot.
CREATE ROLE aethercal_app LOGIN NOBYPASSRLS PASSWORD :'pw_app';

-- The worker's SCAN pool only: finding work whose business is not yet known (what is due, which
-- leases expired, which row can I claim) plus the operator's instance-wide gauges. The worker
-- EXECUTES every effect on `aethercal_app`, under RLS, bound to the business of the row it holds.
CREATE ROLE aethercal_worker LOGIN BYPASSRLS PASSWORD :'pw_worker';

-- --------------------------------------------------------------------------------------------
-- Connect + schema privileges. The GRANTs on the TABLES are the migration's job (it owns them), as
-- is `ALTER DEFAULT PRIVILEGES FOR ROLE aethercal_owner` — which is what stops every FUTURE
-- migration from shipping a table the application cannot read.
-- --------------------------------------------------------------------------------------------

GRANT CONNECT ON DATABASE :"db" TO aethercal_owner, aethercal_app, aethercal_worker;
GRANT CREATE ON DATABASE :"db" TO aethercal_owner;

\connect :"db"

GRANT USAGE ON SCHEMA public TO aethercal_app, aethercal_worker;
GRANT CREATE, USAGE ON SCHEMA public TO aethercal_owner;

-- `alembic_version` is created by Alembic, not by Base.metadata, so it cannot be derived from the
-- models and the migration's metadata-driven GRANT loop does not reach it. The WEB process reads it
-- at boot to refuse a schema it has outgrown. Without this grant that refusal fails loudly instead
-- of working — survivable, but "loud and wrong" is still a support ticket. Grant it.
-- (It does not exist yet on a virgin database; the DO block keeps this file idempotent.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'alembic_version')
    THEN
        EXECUTE 'GRANT SELECT ON alembic_version TO aethercal_app, aethercal_worker';
    END IF;
END
$$;

-- --------------------------------------------------------------------------------------------
-- Backups. `pg_dump` over tables with RLS FAILS without BYPASSRLS — loudly, which is right, but it
-- would leave the instance with no backup until somebody noticed. Dump as `aethercal_owner` (which
-- has the attribute), or give the backup role it explicitly. Do not discover this at 3 a.m.
-- --------------------------------------------------------------------------------------------

-- --------------------------------------------------------------------------------------------
-- Post-deploy smoke. Paste the output into the runbook: with no evidence, this step is not done.
--
--   SELECT current_user;                            -- once per configured URL: app / owner / worker
--   SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
--    WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY relname;
--   -- as aethercal_app, with NO GUC set:
--   SELECT count(*) FROM bookings;                  -- must be 0
-- --------------------------------------------------------------------------------------------
