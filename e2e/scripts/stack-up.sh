#!/usr/bin/env bash
# Bring up the E2E stack — the shipping compose stack plus a mailbox and a webhook sink — and
# bootstrap the one thing the suite cannot create for itself: a tenant with an API key.
#
# The sequence is the one deploy/README.md prescribes for a human self-hoster, which is the point:
# if the quickstart is broken, this script breaks with it.
#
#   1. render deploy/.env (test-only values: THREE role URLs, and neither retired flag)
#   2. start postgres — which provisions the three roles on its first boot — then the one-shot
#      `migrate` (as the OWNER), then app + WORKER (+ mailpit + the sink)
#   3. create the tenant and issue an API key with the admin CLI (itself an owner-role tool)
#   4. write the key back into deploy/.env and start the booking page with it
#   5. write e2e/.stack.json — the suite reads it and REFUSES to run without it
#
# ==The worker is started BY NAME, and waited for.== It is a dependency of nothing, so `up app` alone
# leaves it down — and a down worker drains no outbox and delivers no webhook. The stack would look
# perfectly healthy while producing not one of the effects this suite exists to observe, and every
# spec would time out blaming itself. (That is precisely what the isolation batch left behind here:
# it moved the drain out of `app` and into its own process, and this script had never heard of it.)
#
# Every wait has a deadline and a non-zero exit. Nothing here degrades to "carry on anyway".

set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${E2E_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/deploy/.env"
STACK_FILE="${E2E_DIR}/.stack.json"

API_URL="http://localhost:8000"
BOOKING_URL="http://localhost:5001"
MAILPIT_URL="http://localhost:8025"
SINK_URL="http://localhost:9099"
# What the SERVER dials: the sink's name on the compose network (see compose.e2e.yml for why its
# address must be globally routable — the delivery worker's SSRF guard rejects private ones).
SINK_WEBHOOK_URL="http://hooks:9099/hook"

TENANT_SLUG="e2e"
HOST_EMAIL="host@e2e.test"

compose() {
  docker compose \
    -f "${REPO_ROOT}/deploy/docker-compose.yml" \
    -f "${E2E_DIR}/compose.e2e.yml" \
    "$@"
}

wait_for_http() {
  local name="$1" url="$2" deadline=$((SECONDS + ${3:-180}))
  printf 'waiting for %s (%s) ' "${name}" "${url}"
  while ((SECONDS < deadline)); do
    if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
      printf ' up\n'
      return 0
    fi
    printf '.'
    sleep 2
  done
  printf '\n'
  echo "ERROR: ${name} never became reachable at ${url}" >&2
  compose logs --tail 60 >&2 || true
  return 1
}

# The worker's operator surface (:8001) is NOT published to the host — it reports every business on
# the instance. So it is probed from INSIDE the container, which also proves the container is up at
# all. A silent, absent worker is the one failure this stack cannot survive and would not report.
wait_for_worker() {
  local deadline=$((SECONDS + ${1:-180}))
  printf 'waiting for the worker (the process that drains the outbox and delivers the webhooks) '
  while ((SECONDS < deadline)); do
    if compose exec -T worker python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/v1/health', timeout=3).status == 200 else 1)" \
      >/dev/null 2>&1; then
      printf ' up\n'
      return 0
    fi
    printf '.'
    sleep 2
  done
  printf '\n'
  echo "ERROR: the worker never became healthy." >&2
  echo "       Nothing would have been sent: no confirmation email, no outbound webhook — and the" >&2
  echo "       specs would have timed out looking like product bugs. Failing here instead." >&2
  compose logs --tail 80 worker >&2 || true
  return 1
}

echo "==> rendering ${ENV_FILE} (test-only values)"
cp "${E2E_DIR}/scripts/deploy.env.template" "${ENV_FILE}"

# A clean database every time: a stack carrying yesterday's rows would make "the slot was released"
# depend on history nobody can see.
echo "==> resetting any previous stack"
compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> starting postgres + migrate + app + worker + mailpit + webhook sink"
# `migrate` is a one-shot: `app` and `worker` both wait on it completing successfully, so nothing
# ever serves or drains against a half-migrated schema. `worker` is listed explicitly because
# NOTHING depends on it — compose would not start it otherwise.
compose up -d --build postgres migrate app worker mailpit hooks

# The migration ran as aethercal_owner, in its own container, and then exited. If it failed, `app`
# never starts (`service_completed_successfully`) — but say so HERE, where the reason is legible,
# instead of letting it surface as a mysterious API timeout below.
migrate_exit="$(compose ps -a --format '{{.Service}} {{.ExitCode}}' 2>/dev/null | awk '$1 == "migrate" {print $2; exit}')"
if [[ -n "${migrate_exit}" && "${migrate_exit}" != "0" ]]; then
  echo "ERROR: the one-shot 'migrate' service exited ${migrate_exit} — the schema was NOT migrated." >&2
  echo "       It runs as aethercal_owner; if that role does not exist, the postgres volume was not" >&2
  echo "       empty on boot and e2e/sql/00-provision-roles.sh never ran." >&2
  compose logs --tail 80 migrate >&2 || true
  exit 1
fi

wait_for_http "the API" "${API_URL}/api/v1/health" 240
wait_for_worker 120
wait_for_http "mailpit" "${MAILPIT_URL}/api/v1/messages?limit=1" 60
wait_for_http "the webhook sink" "${SINK_URL}/_health" 60

echo "==> creating the tenant and issuing an API key (the admin CLI runs as aethercal_owner)"
tenant_output="$(compose exec -T app aethercal-admin create-tenant \
  --slug "${TENANT_SLUG}" --name "E2E" --email "${HOST_EMAIL}" --timezone UTC)"
tenant_id="$(printf '%s\n' "${tenant_output}" | sed -n 's/^tenant_id=//p' | tr -d '\r')"
host_user_id="$(printf '%s\n' "${tenant_output}" | sed -n 's/^user_id=//p' | tr -d '\r')"

api_key="$(compose exec -T app aethercal-admin issue-api-key \
  --tenant-slug "${TENANT_SLUG}" --name "e2e" | tr -d '\r' | tail -n 1)"

if [[ -z "${tenant_id}" || -z "${host_user_id}" || -z "${api_key}" ]]; then
  echo "ERROR: the admin CLI did not return a tenant id, a host user id and an API key" >&2
  printf '%s\n' "${tenant_output}" >&2
  exit 1
fi

# The booking page presents this key on the guest's behalf, so it must exist BEFORE that container
# boots — which is why the page starts last.
echo "==> starting the booking page with the issued key"
sed -i.bak "s|^AETHERCAL_API_KEY=.*|AETHERCAL_API_KEY=${api_key}|" "${ENV_FILE}"
rm -f "${ENV_FILE}.bak"
compose up -d booking
wait_for_http "the booking page" "${BOOKING_URL}/healthz" 120

cat >"${STACK_FILE}" <<JSON
{
  "apiUrl": "${API_URL}",
  "bookingUrl": "${BOOKING_URL}",
  "mailpitUrl": "${MAILPIT_URL}",
  "sinkUrl": "${SINK_URL}",
  "sinkWebhookUrl": "${SINK_WEBHOOK_URL}",
  "apiKey": "${api_key}",
  "tenantId": "${tenant_id}",
  "hostUserId": "${host_user_id}"
}
JSON

echo "==> stack is up; wrote ${STACK_FILE}"
