#!/usr/bin/env bash
# One command: bring up a throwaway stack, run the two-week simulation, tear it down.
#
#   simulation/scripts/run.sh [--keep] [-- <extra args for the harness>]
#
# ==This is the only supported way to run the simulation, because it is the only path that guarantees
# the stack is a throwaway.== The harness itself refuses to start without a `.stack.json` (see
# aethercal_sim/world.py) precisely so that nobody can point it at a live instance by hand.
#
# `--keep` leaves the stack up afterwards so the database, the mailbox and the sink can be inspected.
# The report is written either way, before the teardown.
#
# ==The exit code is the verdict.== The harness exits non-zero when a control failed (VOID) or did
# not run (INCOMPLETE), and `set -e` carries that out through this script — so a run that proved
# nothing can never be mistaken for a green one by whatever wraps it.

set -euo pipefail

SIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${SIM_DIR}/.." && pwd)"
E2E_DIR="${REPO_ROOT}/e2e"

KEEP=0
if [[ "${1:-}" == "--keep" ]]; then
  KEEP=1
  shift
fi
if [[ "${1:-}" == "--" ]]; then
  shift
fi

COMPOSE_CMD="docker compose -f ${REPO_ROOT}/deploy/docker-compose.yml -f ${E2E_DIR}/compose.e2e.yml -f ${SIM_DIR}/compose.sim.yml"

ENV_FILE="${REPO_ROOT}/deploy/.env"
ENV_BACKUP="${SIM_DIR}/.env.deploy-backup"

# ==Give the developer their `deploy/.env` back, whatever happened.==
#
# `stack-up.sh` overwrites that file because the shipping compose stack genuinely reads it
# (`env_file: .env` on each service), and it backs the original up first. Restoring is THIS
# script's job because it owns the whole lifecycle — the stack must go on reading the simulation's
# values until it is torn down. The trap fires on success, on failure and on Ctrl-C, so a boot that
# dies halfway no longer leaves shared repo configuration replaced by test-only values.
restore_env() {
  [[ -f "${ENV_BACKUP}.state" ]] || return 0
  if [[ "$(cat "${ENV_BACKUP}.state")" == "existed" ]]; then
    mv -f "${ENV_BACKUP}" "${ENV_FILE}" 2>/dev/null || true
    echo "==> restored the previous deploy/.env"
  else
    rm -f "${ENV_FILE}"
    echo "==> removed the deploy/.env this run created (there was none before)"
  fi
  rm -f "${ENV_BACKUP}.state" "${ENV_BACKUP}"
}

cleanup() {
  if ((KEEP == 1)); then
    echo "==> --keep: leaving the stack up. Tear it down with:"
    echo "    ${COMPOSE_CMD} down -v"
    echo "    deploy/.env stays in place for it; your original is at ${ENV_BACKUP}"
    return
  fi
  echo "==> tearing down the throwaway stack"
  # shellcheck disable=SC2086 - COMPOSE_CMD is a command line, and must word-split here.
  ${COMPOSE_CMD} down -v --remove-orphans >/dev/null 2>&1 || true
  restore_env
}
trap cleanup EXIT

"${SIM_DIR}/scripts/stack-up.sh"

echo "==> running the simulation"
cd "${SIM_DIR}"
# ==The harness runs on the system Python with no dependencies.== That is not a shortcut, it is the
# point: a throwaway host needs a stack and an interpreter, and nothing else has to be installed
# before a measurement can happen.
python3 -m aethercal_sim \
  --compose-cmd "${COMPOSE_CMD}" \
  --out "${SIM_DIR}/simulation-report.md" \
  --json-out "${SIM_DIR}/simulation-report.json" \
  "$@"
