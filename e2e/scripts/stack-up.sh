#!/usr/bin/env bash
# Bring up the E2E stack — the shipping compose stack plus a mailbox and a webhook sink — and
# bootstrap the one thing the suite cannot create for itself: a tenant with an API key.
#
# The sequence is the one deploy/README.md prescribes for a human self-hoster, which is the point:
# if the quickstart is broken, this script breaks with it.
#
#   1. render deploy/.env (test-only values)
#   2. start postgres + app (+ mailpit + the sink); the app migrates on boot
#   3. create the tenant and issue an API key with the admin CLI
#   4. write the key back into deploy/.env and start the booking page with it
#   5. write e2e/.stack.json — the suite reads it and REFUSES to run without it
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

echo "==> rendering ${ENV_FILE} (test-only values)"
cp "${E2E_DIR}/scripts/deploy.env.template" "${ENV_FILE}"

# A clean database every time: a stack carrying yesterday's rows would make "the slot was released"
# depend on history nobody can see.
echo "==> resetting any previous stack"
compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> starting postgres + app + mailpit + webhook sink"
compose up -d --build postgres app mailpit hooks

wait_for_http "the API" "${API_URL}/api/v1/health" 240
wait_for_http "mailpit" "${MAILPIT_URL}/api/v1/messages?limit=1" 60
wait_for_http "the webhook sink" "${SINK_URL}/_health" 60

echo "==> creating the tenant and issuing an API key"
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
