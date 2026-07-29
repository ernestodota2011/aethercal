#!/usr/bin/env bash
# Bring up the THROWAWAY simulation stack and bootstrap the several businesses the run needs.
#
# This is `e2e/scripts/stack-up.sh`'s sibling, and it exists rather than reusing it because the two
# bootstrap different worlds: the E2E needs ONE tenant (its subject is a single guest's journey) and
# the simulation needs SEVERAL (its subject is a whole instance under load, and a single-business
# run would never exercise the cross-tenant aggregation the operator metrics do).
#
# The sequence is otherwise the one deploy/README.md prescribes for a human self-hoster, which is
# the point: if the quickstart is broken, this breaks with it.
#
#   1. render deploy/.env (test-only values, from the E2E's own template)
#   2. down -v the PREVIOUS simulation — see the safety note below
#   3. start postgres → the one-shot `migrate` → app + WORKER + mailpit + the sink
#   4. create N tenants and issue an API key for each, with the admin CLI
#   5. write simulation/.stack.json — the harness reads it and REFUSES to run without it
#
# ==Every `docker compose` here carries `-f simulation/compose.sim.yml`, which renames the project to
# `aethercal-sim`.== Step 2 deletes a database. Under the shipping project name that command would
# delete a real instance's volume on any host that had one. The rename makes that structurally
# impossible; it is not a convention this script is trusted to remember. Any future edit that drops
# that `-f` turns a safe script into a destructive one.
#
# Every wait has a deadline and a non-zero exit. Nothing here degrades to "carry on anyway".

set -euo pipefail

SIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${SIM_DIR}/.." && pwd)"
E2E_DIR="${REPO_ROOT}/e2e"
ENV_FILE="${REPO_ROOT}/deploy/.env"
STACK_FILE="${SIM_DIR}/.stack.json"
ENV_BACKUP="${SIM_DIR}/.env.deploy-backup"

API_URL="http://localhost:8000"
WORKER_URL="http://127.0.0.1:${SIM_WORKER_HOST_PORT:-8001}"
BOOKING_URL="http://localhost:5001"
MAILPIT_URL="http://localhost:8025"
SINK_URL="http://localhost:9099"
# What the SERVER dials: the sink's name on the compose network. See e2e/compose.e2e.yml for why
# that address must be globally routable — the delivery worker's SSRF guard rejects private ones.
SINK_WEBHOOK_URL="http://hooks:9099/hook"

# ==The one place the operator token is decided.== compose.sim.yml REQUIRES this variable and
# declares no default of its own, so the worker and the harness cannot end up holding different
# strings — which would boot a perfectly healthy stack whose every metrics scrape answered 401, and
# report a backlog nobody had ever managed to read.
#
# At least 32 characters, or `Settings` refuses to boot: the endpoint is instance-wide and publicly
# reachable, so a guessable token is as good as no token. Test-only; destroyed with the stack.
METRICS_TOKEN="${AETHERCAL_SIM_METRICS_TOKEN:-sim-local-only-metrics-token-not-a-real-secret}"
export AETHERCAL_SIM_METRICS_TOKEN="${METRICS_TOKEN}"

# ==The run NONCE: 128 bits that tie this file to the database created below.==
#
# The harness refuses to touch a target that cannot prove it is this stack, and this is how the
# proof is planted: the nonce goes into .stack.json AND into a marker schedule inside the fresh
# database. A hand-edited .stack.json cannot forge the second half, and a stale simulation stack
# from an earlier run carries a different nonce — so both are refused. Isolation stops being a
# promise in a README and becomes something the code derives.
RUN_NONCE="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d " 
")"
if [[ ${#RUN_NONCE} -lt 32 ]]; then
  echo "ERROR: could not generate a 128-bit nonce from /dev/urandom" >&2
  exit 1
fi

# The businesses this run simulates: slug:Name:Timezone. Deliberately a MIX of timezones — a
# single-timezone instance never exercises the per-tenant conversion the slot grid does, and the
# pilot's own businesses are not all in one zone either.
BUSINESSES=(
  "clinica-sonrisa:Clinica Sonrisa:America/New_York"
  "katy-hvac:Katy HVAC Services:America/Chicago"
  "estudio-legal:Estudio Legal Marina:America/New_York"
)

compose() {
  docker compose \
    -f "${REPO_ROOT}/deploy/docker-compose.yml" \
    -f "${E2E_DIR}/compose.e2e.yml" \
    -f "${SIM_DIR}/compose.sim.yml" \
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

# ==deploy/.env is SHARED repo configuration, and this overwrites it.== The base compose file
# declares `env_file: .env` per service, so the stack genuinely needs it there; what was missing was
# any way back. Back it up first and record whether it existed, so `run.sh` can restore the
# developer's file on the way out even if the boot fails halfway.
#
# ==A LEFTOVER BACKUP IS A HARD STOP.== `run.sh --keep` deliberately leaves the stack up AND the
# backup in place, because the stack that is still running goes on reading the test-only
# `deploy/.env`. Start this script again in that state and the `cp` below would copy the
# SIMULATION's env over the saved original — destroying the only copy of the developer's file,
# silently, in the very step whose name is "back it up". The `--keep` and the next boot are far
# enough apart in time that nobody connects them, which is exactly the shape of accident worth
# making impossible instead of documenting.
if [[ -e "${ENV_BACKUP}.state" ]]; then
  echo "ERROR: ${ENV_BACKUP}.state already exists, so a previous run (most likely" >&2
  echo "       \`run.sh --keep\`) still holds your original deploy/.env. Starting now would" >&2
  echo "       overwrite that backup with this simulation's own env file and lose the original." >&2
  echo "" >&2
  echo "       Restore it and tear the leftover stack down with:" >&2
  echo "" >&2
  echo "         ${SIM_DIR}/scripts/stack-down.sh" >&2
  echo "" >&2
  echo "       If you are certain the backup is stale, remove ${ENV_BACKUP}.state by hand." >&2
  exit 1
fi

echo "==> rendering ${ENV_FILE} (test-only values; the previous file is backed up)"
if [[ -f "${ENV_FILE}" ]]; then
  cp "${ENV_FILE}" "${ENV_BACKUP}"
  echo "existed" >"${ENV_BACKUP}.state"
else
  echo "absent" >"${ENV_BACKUP}.state"
fi
cp "${E2E_DIR}/scripts/deploy.env.template" "${ENV_FILE}"

echo "==> resetting any previous SIMULATION stack (project: aethercal-sim)"
compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> starting postgres + migrate + app + worker + mailpit + webhook sink"
compose up -d --build postgres migrate app worker mailpit hooks

# The migration ran as aethercal_owner, in its own container, and then exited. If it failed, `app`
# never starts — but say so HERE, where the reason is legible, instead of letting it surface as a
# mysterious API timeout below.
migrate_exit="$(compose ps -a --format '{{.Service}} {{.ExitCode}}' 2>/dev/null | awk '$1 == "migrate" {print $2; exit}')"
if [[ -n "${migrate_exit}" && "${migrate_exit}" != "0" ]]; then
  echo "ERROR: the one-shot 'migrate' service exited ${migrate_exit} — the schema was NOT migrated." >&2
  compose logs --tail 80 migrate >&2 || true
  exit 1
fi

wait_for_http "the API" "${API_URL}/api/v1/health" 240
wait_for_http "mailpit" "${MAILPIT_URL}/api/v1/messages?limit=1" 60
wait_for_http "the webhook sink" "${SINK_URL}/_health" 60

# ==The operator surface is probed WITH its token, because that is the only probe that means
# anything.== Unauthenticated it answers 401, and with no token configured it answers 503 — both of
# which are "reachable" to a naive check. The harness scrapes this endpoint for the outbox backlog;
# if the token compose sets and the one this script hands the harness ever disagree, every scrape
# would fail and the backlog would read as "never measured". Assert the working combination once,
# here, loudly.
printf 'waiting for the worker operator surface (%s/api/v1/metrics/summary) ' "${WORKER_URL}"
metrics_deadline=$((SECONDS + 180))
metrics_ok=0
while ((SECONDS < metrics_deadline)); do
  if curl -fsS --max-time 3 -H "Authorization: Bearer ${METRICS_TOKEN}" \
    "${WORKER_URL}/api/v1/metrics/summary" >/dev/null 2>&1; then
    metrics_ok=1
    printf ' up\n'
    break
  fi
  printf '.'
  sleep 2
done
if ((metrics_ok == 0)); then
  printf '\n'
  echo "ERROR: the worker's /metrics/summary never answered 200 with the configured token." >&2
  echo "       Without it the simulation cannot measure the outbox backlog at all — and a harness" >&2
  echo "       reporting 'backlog: 0' would be describing a queue it never managed to read." >&2
  compose logs --tail 80 worker >&2 || true
  exit 1
fi

echo "==> creating ${#BUSINESSES[@]} businesses and issuing an API key for each"
business_json=""
first_key=""
first_slug="${BUSINESSES[0]%%:*}"
for entry in "${BUSINESSES[@]}"; do
  slug="${entry%%:*}"
  rest="${entry#*:}"
  name="${rest%%:*}"
  timezone="${rest#*:}"

  tenant_output="$(compose exec -T app aethercal-admin create-tenant \
    --slug "${slug}" --name "${name}" --email "host@${slug}.sim.test" --timezone "${timezone}")"
  tenant_id="$(printf '%s\n' "${tenant_output}" | sed -n 's/^tenant_id=//p' | tr -d '\r')"
  host_user_id="$(printf '%s\n' "${tenant_output}" | sed -n 's/^user_id=//p' | tr -d '\r')"
  api_key="$(compose exec -T app aethercal-admin issue-api-key \
    --tenant-slug "${slug}" --name "sim" | tr -d '\r' | tail -n 1)"

  if [[ -z "${tenant_id}" || -z "${host_user_id}" || -z "${api_key}" ]]; then
    echo "ERROR: the admin CLI did not return a tenant id, host user id and API key for ${slug}" >&2
    printf '%s\n' "${tenant_output}" >&2
    exit 1
  fi
  [[ -z "${first_key}" ]] && first_key="${api_key}"

  [[ -n "${business_json}" ]] && business_json+=","
  business_json+="$(printf '\n    {"slug": "%s", "name": "%s", "timezone": "%s", "tenantId": "%s", "hostUserId": "%s", "apiKey": "%s"}' \
    "${slug}" "${name}" "${timezone}" "${tenant_id}" "${host_user_id}" "${api_key}")"
done

# ==Plant this run's nonce INSIDE the database, as a schedule the harness reads back.==
#
# This is the half of the identity proof a hand-edited .stack.json cannot forge: only the script
# that just created this database could have written a schedule carrying a nonce generated seconds
# ago. `aethercal_sim.world.assert_disposable_stack` refuses to create, purge or modify anything
# until it has read this marker back through the API — which is what turns "never run this against
# a real instance" from a sentence in a README into something the code derives.
#
# The marker carries EMPTY rules, so it offers no availability and cannot affect a single number in
# the report; it exists only to be recognised.
echo "==> planting the stack marker (nonce ${RUN_NONCE:0:8}...) in the first business"
marker_status="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer ${first_key}" -H "Content-Type: application/json" \
  --data "{\"name\": \"sim-marker-${RUN_NONCE}\", \"timezone\": \"UTC\", \"rules\": {}}" \
  "${API_URL}/api/v1/schedules/")"
if [[ "${marker_status}" != "201" ]]; then
  echo "ERROR: could not plant the stack marker (HTTP ${marker_status})." >&2
  echo "       Without it the harness cannot prove this is a throwaway stack and will refuse to" >&2
  echo "       run — which is correct behaviour, so this is a hard failure here." >&2
  exit 1
fi

# The booking page is started because the shipping stack has one and a simulation that quietly
# dropped a service would not be running the shipping stack any more. It is pointed at the first
# business. ==The harness drives the API, not the browser== — a simulation's subject is the server's
# behaviour under concurrency, and one headless browser per simulated guest would measure Playwright.
echo "==> starting the booking page with the first business's key"
sed -i.bak "s|^AETHERCAL_API_KEY=.*|AETHERCAL_API_KEY=${first_key}|" "${ENV_FILE}"
sed -i.bak "s|^AETHERCAL_TENANT_SLUG=.*|AETHERCAL_TENANT_SLUG=${first_slug}|" "${ENV_FILE}"
rm -f "${ENV_FILE}.bak"
# ==A `sed` that matches nothing exits 0.== Assert the EFFECT on the file rather than trusting the
# command's exit code (the same trap e2e/scripts/stack-up.sh documents).
for required in AETHERCAL_API_KEY AETHERCAL_TENANT_SLUG; do
  if [[ -z "$(sed -n "s|^${required}=||p" "${ENV_FILE}")" ]]; then
    echo "ERROR: ${required} is empty in ${ENV_FILE}: its placeholder line is missing from" >&2
    echo "       e2e/scripts/deploy.env.template, so the substitution above did nothing." >&2
    exit 1
  fi
done
compose up -d booking
wait_for_http "the booking page" "${BOOKING_URL}/healthz" 120

cat >"${STACK_FILE}" <<JSON
{
  "apiUrl": "${API_URL}",
  "workerUrl": "${WORKER_URL}",
  "bookingUrl": "${BOOKING_URL}",
  "mailpitUrl": "${MAILPIT_URL}",
  "sinkUrl": "${SINK_URL}",
  "sinkWebhookUrl": "${SINK_WEBHOOK_URL}",
  "metricsToken": "${METRICS_TOKEN}",
  "composeProject": "aethercal-sim",
  "nonce": "${RUN_NONCE}",
  "businesses": [${business_json}
  ]
}
JSON

echo "==> stack is up; wrote ${STACK_FILE}"
