#!/usr/bin/env bash
# Tear the THROWAWAY simulation stack down and give `deploy/.env` back.
#
#   simulation/scripts/stack-down.sh
#
# ==This exists because `run.sh --keep` deliberately leaves two things behind==: the stack (so the
# database, the mailbox and the sink can be inspected) and the developer's original `deploy/.env`,
# parked in a backup while the running stack goes on reading the test-only one. `run.sh` restores
# that backup from a `trap` on the way out — but a `--keep` run has no way out, so until this
# script existed the only documented teardown was a bare `docker compose down -v`, which drops the
# containers and leaves shared repo configuration replaced by simulation values.
#
# It is also the instruction `stack-up.sh` prints when it refuses to start on top of a leftover
# backup, which is the other half of the same defect: a second `stack-up.sh` used to copy the
# simulation's own env OVER the saved original, destroying the only copy of it inside the step
# whose entire purpose is to preserve it.
#
# Safe to run twice, and safe to run when nothing is up: every step is conditional on finding
# something to undo.
#
# ==Every `docker compose` here carries `-f simulation/compose.sim.yml`, which renames the project
# to `aethercal-sim`.== This script runs `down -v`, which DELETES A DATABASE. Under the shipping
# project name that command would delete a real instance's volume on any host that had one. Any
# future edit that drops that `-f` turns a safe script into a destructive one — the same warning
# stack-up.sh carries, for the same reason, against the same risk.

set -euo pipefail

SIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${SIM_DIR}/.." && pwd)"
E2E_DIR="${REPO_ROOT}/e2e"
ENV_FILE="${REPO_ROOT}/deploy/.env"
ENV_BACKUP="${SIM_DIR}/.env.deploy-backup"
STACK_FILE="${SIM_DIR}/.stack.json"

# ==The teardown's exit status is the whole point of running it.== It was `|| true` with its
# output sent to /dev/null, so a `down` that failed — a container that would not stop, a volume
# still in use, a daemon that had gone away — printed "stack is down" and removed
# `.stack.json` anyway. That file is the ONLY trace that a stack exists; deleting it while the
# stack is alive loses the stack AND the `--keep` guard that protects the developer's
# `deploy/.env` backup (S6/S26), because that guard keys on the state this script just threw
# away.
#
# So: restore the environment ALWAYS (safe, and it must happen either way), announce success
# ONLY on a clean teardown, and exit non-zero so no wrapper mistakes the failure for a stop.
echo "==> tearing down the throwaway stack (project: aethercal-sim)"
# ==The token has to be in the environment or compose refuses to interpolate the overlay at all.==
# The same hole run.sh had, for the same reason, which is why the fix is one sourced function
# rather than two paragraphs: see scripts/compose-env.sh. This is `--keep`'s only way out, so it
# failing meant a stack left up with nothing left to take it down.
# shellcheck source=compose-env.sh
source "${SIM_DIR}/scripts/compose-env.sh"
aethercal_export_compose_token "${STACK_FILE}"
teardown_status=0
docker compose \
  -f "${REPO_ROOT}/deploy/docker-compose.yml" \
  -f "${E2E_DIR}/compose.e2e.yml" \
  -f "${SIM_DIR}/compose.sim.yml" \
  down -v --remove-orphans || teardown_status=$?

# The same restore `run.sh` performs from its trap, deliberately identical: two ways to put the
# file back that drift apart are worse than one, because the path nobody exercises is the one that
# is broken on the day it is needed.
# shellcheck source=restore-env.sh
source "${SIM_DIR}/scripts/restore-env.sh"
if [[ -f "${ENV_BACKUP}.state" ]]; then
  aethercal_restore_env "${ENV_FILE}" "${ENV_BACKUP}"
else
  echo "==> no deploy/.env backup to restore"
fi

if ((teardown_status != 0)); then
  echo "ERROR: docker compose down exited ${teardown_status}. The stack may still be UP." >&2
  echo "       ${STACK_FILE} is being KEPT so the stack is not lost track of, and so that" >&2
  echo "       stack-up.sh goes on refusing to start on top of it." >&2
  echo "       deploy/.env has been restored regardless." >&2
  exit "${teardown_status}"
fi

rm -f "${STACK_FILE}"
echo "==> stack is down"
