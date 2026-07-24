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

## How to propose a change

1. **Open an issue first for anything non-trivial** — a bug, a behaviour change, a new capability.
   Trivial fixes (a typo, a broken link) can go straight to a pull request. Discussing scope before
   you write code avoids a large diff being turned away on direction.
2. **Fork the repository and branch** from `main`. Keep the branch focused on one concern.
3. **Work test-first** where the change touches scheduling, and keep `aethercal-core` pure and
   I/O-free (both are enforced in CI — see below).
4. **Run the full local gate before you push:**

   ```bash
   uv run poe check     # ruff format, ruff check, pyright, import contracts, pytest
   ```

   The same checks run in CI (`.github/workflows/ci.yml`): lint + type-check, the JS calendar
   bundle drift guard, the test matrix across Python 3.11–3.13 on Linux and Windows, the
   PostgreSQL-backed `-m db` suite, and a `docker build` of the deploy image. **A pull request does
   not merge until CI is green.**
5. **Open the pull request** and fill in the [template](.github/pull_request_template.md): a
   one-line summary, the linked issue, and your test evidence. One concern per PR; no secrets or
   generated artifacts (`dist/`, `.venv/`, lockfile churn) committed.
6. **A maintainer reviews and merges.** Every path has a required reviewer
   (see [Project governance](#project-governance)); expect review comments and be ready to iterate.

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

## Reporting a security issue

Do **not** open a public issue or pull request for a vulnerability. Report it privately — the
process, scope, and what to expect are in [SECURITY.md](SECURITY.md).

## License and sign-off (DCO)

AetherCal is [MIT-licensed](LICENSE). Contributions are **inbound = outbound**: by opening a pull
request you agree that your contribution is licensed to the project and its users under the same MIT
license, and that you have the right to grant it.

We use the [Developer Certificate of Origin](https://developercertificate.org/) rather than a CLA.
Certify each commit by signing it off — this appends a `Signed-off-by: Your Name <you@example.com>`
line using the identity in your Git config:

```bash
git commit -s -m "fix: correct DST boundary in slot expansion"
```

Sign-off says you wrote the change, or have the right to submit it under the project's license. Use
your real name and a reachable email.

## Project governance

AetherCal is maintained by a small maintainer team, listed as the required reviewers in
[`.github/CODEOWNERS`](.github/CODEOWNERS). Today that is a single maintainer; the model is
deliberately lightweight for a pre-alpha project and will grow as contributors do.

- **Decisions are made in the open** — in issues and pull requests, not in private. Substantial
  changes start as an issue so direction is agreed before code is written.
- **Every path has a required reviewer**, and the highest-risk paths (the pure scheduling engine
  `packages/aethercal-core/` and everything under `.github/`) are reviewed by a maintainer directly.
  See [CODEOWNERS](.github/CODEOWNERS) for the current mapping.
- **The core rules of the codebase** — correctness-first with tests, a pure I/O-free `core`, one
  concern per PR, no secrets in source — are enforced by CI, not by memory. A change that weakens a
  guard is expected to explain why in the pull request.
- **A maintainer has the final say** on scope and direction, and is responsible for keeping the
  released stack coherent. Disagreement is resolved in the thread; if it cannot be, the maintainer
  decides and records the reasoning.
