#!/bin/sh
# Provision the three database roles for the THROWAWAY E2E stack — and for nothing else.
#
# ============================================================================================
# Why this is legitimate HERE and forbidden in deploy/
# ============================================================================================
#
# `docker-entrypoint-initdb.d/` runs only when the data volume is EMPTY. That makes it a trap for a
# production deployment: it would run perfectly in CI, on a fresh volume, every time — and NEVER run
# on the instance that actually matters, whose volume has data. The suite would go green while the
# live database stayed role-less and unprotected. deploy/sql/provision_roles.sql says exactly that,
# at length, and deploy/docker-compose.yml deliberately mounts nothing here.
#
# The E2E stack is the one place where the premise is not a hope but an invariant: `stack-up.sh`
# runs `compose down -v` before every single run, so the volume is empty BY CONSTRUCTION. And if it
# somehow were not, this script would simply not run, `migrate` would fail to connect as
# `aethercal_owner`, and the whole stack would refuse to come up — loudly, which is the correct
# failure.
#
# The passwords are test-only literals rendered by scripts/stack-up.sh. They are the same value on
# purpose (there is nothing to protect in a stack that is destroyed minutes later), and they must
# match the three URLs in scripts/deploy.env.template.
#
# ==The role DDL is NOT copied here.== It is the SHIPPED file, deploy/sql/provision_roles.sql, run
# with psql variables. A second copy would drift from the first, and the drift would be invisible:
# the E2E would go on passing against roles the product does not actually ship.

set -eu

: "${POSTGRES_DB:?}"
: "${POSTGRES_USER:?}"
: "${AETHERCAL_E2E_ROLE_PASSWORD:?the e2e role password must be passed to the postgres service}"

echo "==> e2e: provisioning the three roles from the SHIPPED deploy/sql/provision_roles.sql"

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER}" \
     --dbname "${POSTGRES_DB}" \
     -v db="${POSTGRES_DB}" \
     -v pw_owner="${AETHERCAL_E2E_ROLE_PASSWORD}" \
     -v pw_app="${AETHERCAL_E2E_ROLE_PASSWORD}" \
     -v pw_worker="${AETHERCAL_E2E_ROLE_PASSWORD}" \
     -f /opt/aethercal/provision_roles.sql
