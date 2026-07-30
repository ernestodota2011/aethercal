#!/usr/bin/env bash
# ==The ONE place the operator token is put into a `docker compose` environment.== Sourced by
# run.sh and stack-down.sh.
#
# `compose.sim.yml` declares
#
#     AETHERCAL_METRICS_TOKEN: ${AETHERCAL_SIM_METRICS_TOKEN:?set by simulation/scripts/stack-up.sh}
#
# — REQUIRED, with no default, so the worker and the harness can never end up holding different
# strings. That requirement is about `up`; but compose interpolates the WHOLE file on every
# subcommand, so `down` inherits an exigency written for its opposite. Without the variable the
# teardown dies with `required variable AETHERCAL_SIM_METRICS_TOKEN is missing a value` and the
# throwaway stack stays up.
#
# ==`aethercal_sim.__main__._compose` already solved this, and only for itself.== Its docstring
# spells the trap out — "the harness inherits its shell from whoever launched it and that variable
# is normally NOT set there" — and the fix went into the Python call site alone, while the two
# shell call sites went on running `docker compose down` out of a shell that had never heard of it.
# So neither teardown had ever worked, on any published run; until run.sh learned to propagate its
# exit code it failed silently as well. A rule enforced at some of its call sites is a rule with a
# hole in it, which is the sentence this directory's README already carried about C3.
#
# The value is CARRIED from `.stack.json` — which `stack-up.sh` wrote from the very variable it
# exported to compose — rather than re-derived here. One decision, in one place, exactly as the
# harness does it. Re-declaring the default literal in a second script is the drift that produces
# a stack booted under one string and addressed under another.

# Export AETHERCAL_SIM_METRICS_TOKEN for a `docker compose` call against the simulation project.
#
#   $1 - path to this run's .stack.json (need not exist)
#
# ==`down` never READS that value: no container is started, so nothing authenticates with it.== What
# compose needs is that the variable be *present* to interpolate. So when there is no stack file to
# carry a value from — a teardown after a boot that died before writing one, or the "safe to run
# when nothing is up" case stack-down.sh advertises — an unmistakable placeholder is exported and
# the cleanup proceeds. Refusing to tear down over a variable nobody will consume would leave live
# containers behind out of tidiness, which is the worse failure; naming the placeholder for what it
# is keeps it from ever being mistaken for a secret in a log.
aethercal_export_compose_token() {
  local stack_file="${1:-}" carried=""

  if [[ -n "${stack_file}" && -f "${stack_file}" ]]; then
    carried="$(
      python3 -c 'import json, sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("metricsToken", "") or "")
except Exception:
    pass' "${stack_file}" 2>/dev/null
    )" || carried=""
  fi

  export AETHERCAL_SIM_METRICS_TOKEN="${carried:-${AETHERCAL_SIM_METRICS_TOKEN:-unused-by-compose-down}}"
}
