<!-- Keep each PR to a single concern. Small, reviewable diffs get merged faster. -->

## Summary

<!-- What does this PR change, and why? One concern per PR. -->

## Linked task / RF

<!-- e.g. F0-02, RF-XX, or the issue number this closes. -->

Closes #

## Test evidence

<!-- Paste the relevant output: new/updated tests, `uv run poe check`, or manual verification steps. -->

## Checklist

- [ ] Tests added or updated for this change
- [ ] `uv run poe check` passes locally (ruff format, ruff check, pyright, import contracts, pytest)
- [ ] This PR covers one concern only
- [ ] No secrets, credentials, or generated artifacts (`dist/`, `.venv/`, lockfile churn) committed
- [ ] Docs updated if behavior or public API changed
