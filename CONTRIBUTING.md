# Contributing to AetherCal

Thanks for your interest. AetherCal is in pre-alpha; the most valuable help right now is trying
the engine and reporting incorrect scheduling results.

## Ground rules

- **Correctness first.** The scheduling engine (`aethercal-core`) is developed test-first. Every
  change to date, recurrence, timezone, availability, or slot logic ships with tests —
  property-based where the invariant is general (see `packages/aethercal-core/tests/`).
- **Keep `core` pure.** `aethercal-core` must not import any other internal package and must not
  perform I/O. This is enforced by import contracts in CI.
- **One concern per pull request.** Small, reviewable diffs.
- **Conventional Commits.** Commit messages follow the Conventional Commits specification; the
  changelog is generated from them.

## Local setup

```bash
uv sync
uv run poe check
```

## Database-backed tests (`-m db`)

Most of the suite runs offline against in-memory SQLite. A smaller set — migration parity, the boot
advisory lock, the partial unique index, the outbox's real atomicity — can only be proved against a
real PostgreSQL, and is marked `db`. Those tests **skip** in the default run and need a server:

```bash
AETHERCAL_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aethercal_test \
  uv run pytest -m db
```

Asking for that suite by name with no database is a hard error, not a green run of nothing (see the
repository-root `conftest.py`).

**Concurrent `-m db` runs are safe.** Each run creates a PostgreSQL **schema of its own** — named
for the process, the `pytest-xdist` worker and a random suffix — points its `search_path` at it, and
drops it on the way out (`apps/server/tests/conftest.py::pg_url`). Two worktrees, or two xdist
workers, can therefore share one database without seeing each other. They could not before: the
suite recreates the schema around every test, so concurrent runs used to `DROP TABLE` out from under
one another, yielding deadlocks and failures that moved from test to test and belonged to nobody.

If the isolation cannot be established, the suite **refuses to start** rather than quietly fall back
to the shared schema. A leftover `aethercal_test_*` schema is only possible if a run was killed
outright; the next run sweeps it.

## Reporting scheduling bugs

A great report includes the recurrence rule (or availability configuration), the query window, the
timezone, and the occurrences you expected versus what you got. If it reproduces in
`aethercal-core`, that is where the fix and its regression test belong.
