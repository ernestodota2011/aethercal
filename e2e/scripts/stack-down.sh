#!/usr/bin/env bash
# Tear the E2E stack down, volumes and all. The database is disposable by design — see stack-up.sh.

set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${E2E_DIR}/.." && pwd)"

docker compose \
  -f "${REPO_ROOT}/deploy/docker-compose.yml" \
  -f "${E2E_DIR}/compose.e2e.yml" \
  down -v --remove-orphans

rm -f "${E2E_DIR}/.stack.json" "${E2E_DIR}/.run.json"
echo "==> stack is down"
