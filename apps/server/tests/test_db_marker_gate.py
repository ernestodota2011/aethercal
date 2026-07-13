"""The gate on the gate: prove ``pytest -m db`` FAILS without Postgres instead of skipping green.

``pytest -m db`` with no database skips every test and exits **0**. The CI job reports success,
and not one database test has run. That is the silent no-op applied to our own safety net — so
the guard that prevents it (the repository-root ``conftest.py``) needs a test watching it actually
bite, or the guard becomes one more thing that is merely *assumed* to work.

The important test here runs a real ``pytest`` in a SUBPROCESS, with the variable genuinely unset,
and checks the exit code. ==The effective state, not the apparent one:== asserting that a hook
function raises when called by hand would prove only that the function raises. It would not prove
that pytest ever calls it, that the root conftest is loaded at all, or that the process ends
non-zero — which is the only thing CI actually looks at.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _root_conftest() -> ModuleType:
    """Import the repository-root ``conftest.py`` by PATH, under a private name.

    Under a name of its own because pytest already imported it as ``conftest``, and this repo has
    three other ``conftest`` modules — reaching for the bare name would be a coin toss.
    """
    path = REPO_ROOT / "conftest.py"
    spec = importlib.util.spec_from_file_location("aethercal_root_conftest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env_without_the_database_url() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "AETHERCAL_TEST_DATABASE_URL"}


# One flag per SELECTED test: was it db-marked? These are the shapes pytest really hands over.
_ONLY_DB = [True, True, True]
_ONLY_OFFLINE = [False, False, False]
_EVERYTHING = [True, False, True, False]
_NOTHING: list[bool] = []


@pytest.mark.parametrize(
    ("markexpr", "db_marked", "targets"),
    [
        # Asking for the db suite: every selected test is db-marked.
        ("db", _ONLY_DB, True),
        (" db ", _ONLY_DB, True),
        ("db and slow", _ONLY_DB, True),
        # ==The expressions a text-matching guard gets WRONG.== Both of these SELECT EVERYTHING, so
        # they are plain runs however they are spelled — and a plain run on a laptop with no
        # Postgres must stay a quiet green. Reading `not db` out of the string as if it were a
        # global negation, or reading `db` out of it as if it were a request, both misfire here.
        # Deciding on the selection cannot: the non-db tests are right there in it.
        ("db or not db", _EVERYTHING, False),
        ("(not db) or slow", _ONLY_OFFLINE, False),
        # The offline matrix.
        ("not db", _ONLY_OFFLINE, False),
        ("", _EVERYTHING, False),
        # A bare `pytest` collects the db tests too (they skip by fixture) — and must NOT be read as
        # a request for a database, or every developer without Postgres gets a hard error.
        ("", _ONLY_DB, False),
        # An explicit selection that matched nothing is not a database run; it is caught by the
        # zero-collection arm instead.
        ("db", _NOTHING, False),
        ("slow", _NOTHING, False),
    ],
)
def test_which_runs_target_the_database_suite(
    markexpr: str, db_marked: list[bool], targets: bool
) -> None:
    assert _root_conftest().targets_the_db_suite(markexpr, db_marked) is targets


def test_pytest_m_db_without_a_database_exits_non_zero() -> None:
    """==The one that matters.== A real pytest process, the variable really unset, and the exit code
    CI would really read. Without the guard this run exits 0, having tested precisely nothing."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "db", "--collect-only", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=_env_without_the_database_url(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode != 0, (
        "`pytest -m db` exited 0 with no database configured: every db test skipped, nothing ran, "
        "and CI would call that a pass."
    )
    assert "AETHERCAL_TEST_DATABASE_URL" in (result.stderr + result.stdout)


@pytest.mark.parametrize("markexpr", ["db or not db", "(not db) or slow"])
def test_an_expression_that_selects_everything_is_not_a_database_run(markexpr: str) -> None:
    """==The two expressions a text-matching guard misreads, run for real, with no database.==

    Both select tests that are not db-marked, so both are ordinary runs however they happen to be
    spelled, and both must stay green without Postgres. A guard that pattern-matched the string
    ``not db`` — or the string ``db`` — would fire on one of these and turn a legitimate invocation
    into a hard error. Deciding on the SELECTION cannot make that mistake: the non-db tests are
    simply sitting there in it.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            markexpr,
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=_env_without_the_database_url(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_offline_matrix_is_left_alone() -> None:
    """The other half of the contract. A plain run on a laptop with no Postgres must stay an
    perfectly ordinary green run of the offline suite, skipping the db tests without complaint — a
    guard that broke everyday development would simply be switched off."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not db",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=_env_without_the_database_url(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
