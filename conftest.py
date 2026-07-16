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
from collections.abc import Sequence

import pytest

# ==The network guard travels as a NAMED plugin, not as more of this file.== Fixtures must live in
# a conftest or a registered plugin, and every `conftest.py` in this tree imports under the same
# top-level name — so a test could never import the guard's exception unambiguously. Registering it
# here gives it one importable name (`pytest_network_guard`) and keeps this file about the db gate.
pytest_plugins = ["pytest_network_guard"]

DB_URL_ENV = "AETHERCAL_TEST_DATABASE_URL"
DB_MARKER = "db"


def targets_the_db_suite(markexpr: str, db_marked: Sequence[bool]) -> bool:
    """Is this run TARGETING the database suite? Decided on what pytest actually SELECTED.

    ==Not by reading the marker expression.== A guard that parses ``-m`` by hand has to reimplement
    pytest's boolean grammar, and it will get it wrong: treating the mere appearance of the text
    ``not db`` as a global negation misreads ``db or not db`` (which selects EVERYTHING) and
    ``(not db) or slow`` (which selects everything unmarked). This is the safety net's own safety
    net, so it has to be exact — and the exact answer is already sitting in front of us, in the item
    list pytest handed over after doing its own deselection. We read that instead of second-guessing
    it.

    ``db_marked`` is "is this selected item db-marked?", one flag per SELECTED test. The run targets
    the db suite when **every** selected test is db-marked and there is at least one:

    * ``-m db`` → all 64 selected are db-marked → **True**. Asking for the db suite is asking for a
      database, so a missing URL is an error, not a skip.
    * ``-m "db and slow"`` → whatever it selects is db-marked → **True**, still a database run.
    * ``-m "db or not db"`` → selects everything, so non-db tests are in there → **False**. That
      expression IS a plain run, however it is spelled, and skipping the Postgres tests is right.
    * ``-m "(not db) or slow"`` → selects the unmarked ones → **False**.
    * ``-m "not db"``, or a bare ``pytest`` (empty expression) → **False**. The offline matrix,
      which must stay a quiet green run on a laptop with no Postgres — a guard that broke everyday
      development would simply be switched off.
    """
    if not markexpr.strip():
        # No `-m` at all: the offline matrix. The db tests are collected and skip by fixture, which
        # is exactly what should happen on a developer's machine.
        return False
    return bool(db_marked) and all(db_marked)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Refuse to pretend: the db suite without a database is an ERROR, not an empty success.

    ``trylast`` so that pytest's own marker plugin has already DESELECTED — ``items`` is therefore
    the real selection, which is the whole basis of the decision above.
    """
    markexpr: str = config.option.markexpr
    db_marked = [item.get_closest_marker(DB_MARKER) is not None for item in items]

    if targets_the_db_suite(markexpr, db_marked):
        if os.environ.get(DB_URL_ENV):
            return
        raise pytest.UsageError(
            f"`pytest -m {markexpr}` targets the database suite, but {DB_URL_ENV} is not set.\n"
            "\n"
            "Every db-marked test would SKIP and pytest would exit 0 — a green gate that ran "
            "nothing, which is the exact silent no-op this suite exists to catch, turned on the "
            "suite itself.\n"
            "\n"
            f"Point {DB_URL_ENV} at a real PostgreSQL, e.g.\n"
            f"  {DB_URL_ENV}=postgresql://postgres:postgres@localhost:5432/aethercal_test\n"
            "\n"
            f"(A plain `pytest`, or `pytest -m 'not {DB_MARKER}'`, still skips them quietly. That "
            "is the offline matrix, and it is meant to.)"
        )

    # And an explicit selection must MATCH something. A marker somebody renames, or a test file
    # somebody moves, leaves `-m db` selecting nothing — and a run of zero tests is simply another
    # way of exiting green having proved nothing at all.
    if markexpr.strip() and not items:
        raise pytest.UsageError(
            f"`pytest -m {markexpr}` collected ZERO tests.\n"
            "\n"
            "An explicit marker selection that matches nothing still exits 0. If this was the "
            f"database suite, the `{DB_MARKER}` marker has been renamed or its tests have moved."
        )
