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

## Reporting scheduling bugs

A great report includes the recurrence rule (or availability configuration), the query window, the
timezone, and the occurrences you expected versus what you got. If it reproduces in
`aethercal-core`, that is where the fix and its regression test belong.
