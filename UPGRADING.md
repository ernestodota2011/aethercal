# Upgrading

Breaking changes since **v0.1.0**, and what to do about each one before you pull. Additive changes —
per-business branding, the calendar's resource-timeline view, and so on — are not repeated here; see
[CHANGELOG.md](CHANGELOG.md) for the full list.

---

## The three database roles (and the two retired boot flags)

**What changed.** AetherCal used to run as a single PostgreSQL role that owned every table and
migrated the schema on the web process's own boot (`AETHERCAL_AUTO_MIGRATE=1`, the default). It now
runs as **three** roles across **three** processes, with row-level security enforced by the database
(migration `0008_rls_roles_and_policies`) — the API and admin can no longer see another business's
rows, even if a query forgot its own `WHERE tenant_id`.

**What breaks if you just `git pull` and restart:**

1. `AETHERCAL_AUTO_MIGRATE` and `AETHERCAL_RUN_SCHEDULER` are **retired tripwires**: a truthy value now
   **fails the boot** instead of being honoured (`settings.py`). If your `.env` sets either, the app
   refuses to start until you remove it.
2. The web process needs two **new**, required environment variables —
   `AETHERCAL_OWNER_DATABASE_URL` and `AETHERCAL_WORKER_DATABASE_URL` — and the two PostgreSQL roles
   they point at do not exist until you create them by hand.

**What to do, in order:**

1. Add three new passwords to `.env`: one each for `AETHERCAL_OWNER_DATABASE_URL`
   (`aethercal_owner`) and `AETHERCAL_WORKER_DATABASE_URL` (`aethercal_worker`), alongside the
   existing `AETHERCAL_DATABASE_URL` (`aethercal_app`).
2. With PostgreSQL up, run the role-provisioning script **once**, as a superuser — it cannot be a
   migration (`CREATE ROLE ... BYPASSRLS` needs one):

   ```bash
   docker compose up -d postgres
   set -a; source .env; set +a
   docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v db="$POSTGRES_DB" -v pw_owner=... -v pw_app=... -v pw_worker=... \
     < deploy/sql/provision_roles.sql
   ```

3. Remove `AETHERCAL_AUTO_MIGRATE` and `AETHERCAL_RUN_SCHEDULER` from `.env` if either is set.
4. `docker compose up --build`. A one-shot `migrate` service now runs the schema to head as the
   owner, before `app` or `worker` starts. The `worker` service (reminders, outbound webhooks, the
   Google busy-cache refresh) must run in **exactly one** replica for the whole deployment.

Full reference: [deploy/README.md](deploy/README.md).

---

## A business's WhatsApp/SMS now sends from its own account, not the operator's

**What changed.** A business with no phone credential of its own used to send WhatsApp/SMS from the
**instance operator's** number, silently. It no longer does — those steps are `skipped`, with a
reason recorded in the outbox row and the worker's log.

**What breaks.** Any multi-business instance that relied on the old fallback stops sending
WhatsApp/SMS for a business that never configured its own credential.

**What to do:**

- **Single-business self-host** (the operator *is* the business): set
  `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=true` to restore the old behaviour.
- **Multi-business instance:** give each business its own credential —
  `aethercal-admin credentials set --provider whatsapp` (or `sms`) — one per business.

Full detail: [CHANGELOG.md](CHANGELOG.md) (`[Unreleased]`) and
[docs/byok-credentials.md](docs/byok-credentials.md).

---

## Not breaking, but new since v0.1.0

- **Payments** (Stripe, Mercado Pago) — additive; an event type with no price keeps working exactly
  as before. Read [docs/byok-credentials.md](docs/byok-credentials.md) before taking a real charge —
  neither provider has been verified against a live account, and partial refunds are not modelled.
- **Per-business branding** (`public_name`, `logo_url`, `accent_color`, `timezone`) — additive;
  existing rows keep behaving exactly as before (no logo, no colour, `UTC`).
