"""The safety net's own safety net: ``pytest -m db`` must FAIL when it cannot run, never skip.

==A ``pytest -m db`` that skips every test exits 0.== The CI job goes green, the badge goes
green, and not one database test has run. It is this project's signature defect — the silent no-op
— aimed squarely at the thing whose entire job is to catch silent no-ops. Everything the db suite
guards (the advisory lock, the partial unique index, migration parity, the outbox's real atomicity
under PostgreSQL) would then be protected by a gate that opens itself.

And the trigger is one missing environment variable. Rename it, drop the ``env:`` block from the
workflow, or misspell the URL, and the ``pg_url`` / ``app`` fixtures call ``pytest.skip`` — which is
exactly right when somebody runs the whole suite on a laptop with no Postgres, and a lie when the
job *asked for the db suite by name*.

So the gate lives here, in pytest:

* asking for the db suite is asking for a database. With ``AETHERCAL_TEST_DATABASE_URL`` unset, that
  is a hard, loud configuration error (exit 4) — never a skip;
* and the selection must actually match something. Zero collected tests means the marker was renamed
  or the tests moved, and a suite of nothing also exits 0.

Living here rather than in ``.github/workflows/ci.yml`` is the point: the guard travels with the
test suite. It holds for whoever edits the workflow next, for a developer running the command by
hand, and for any future job that reuses it — and the workflow needs no change at all, so there is
no shell incantation to be dropped by accident, quietly taking the gate with it.
"""

from __future__ import annotations

import os
import re

import pytest

DB_URL_ENV = "AETHERCAL_TEST_DATABASE_URL"
DB_MARKER = "db"

_NEGATED = re.compile(rf"\bnot\s+{DB_MARKER}\b")
_MENTIONED = re.compile(rf"\b{DB_MARKER}\b")


def db_suite_requested(markexpr: str) -> bool:
    """Did this invocation ASK for the db-marked tests?

    ``-m db`` and ``-m "db and slow"`` did. ``-m "not db"`` asked for precisely the opposite, and a
    bare ``pytest`` asked for nothing in particular — in both of those, quietly skipping the
    Postgres tests on a machine without Postgres is correct and friendly, and it stays as it was.
    """
    expression = markexpr.strip()
    if not expression or _NEGATED.search(expression):
        return False
    return _MENTIONED.search(expression) is not None


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to pretend: the db suite without a database is an ERROR, not an empty success."""
    if not db_suite_requested(config.option.markexpr):
        return
    if os.environ.get(DB_URL_ENV):
        return
    raise pytest.UsageError(
        f"`pytest -m {DB_MARKER}` was asked for, but {DB_URL_ENV} is not set.\n"
        "\n"
        "Every db-marked test would SKIP and pytest would exit 0 — a green gate that ran nothing, "
        "which is the exact silent no-op this suite exists to catch, turned on the suite itself.\n"
        "\n"
        f"Point {DB_URL_ENV} at a real PostgreSQL, e.g.\n"
        f"  {DB_URL_ENV}=postgresql://postgres:postgres@localhost:5432/aethercal_test\n"
        "\n"
        f"(A plain `pytest`, or `pytest -m 'not {DB_MARKER}'`, still skips them quietly. That is "
        "the offline matrix, and it is meant to.)"
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """And the selection must MATCH something. ``trylast``, so the deselection has already happened.

    A marker somebody renames, or a test file somebody moves, leaves ``-m db`` selecting nothing —
    and a run of zero tests is simply another way of exiting green having proved nothing.
    """
    if not db_suite_requested(config.option.markexpr) or items:
        return
    raise pytest.UsageError(
        f"`pytest -m {DB_MARKER}` collected ZERO tests.\n"
        "\n"
        f"{DB_URL_ENV} is set, so the database suite was expected to RUN. An empty selection means "
        f"the `{DB_MARKER}` marker was renamed, or its tests moved. A run of nothing exits 0 and "
        "proves nothing."
    )
