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


@pytest.mark.parametrize(
    ("markexpr", "requested"),
    [
        ("db", True),
        (" db ", True),
        ("db and slow", True),
        ("slow and db", True),
        ("not db", False),
        ("not  db", False),
        ("", False),
        ("slow", False),
        # `db` as a SUBSTRING of another marker is not a request for the database suite: a word
        # boundary, not an `in` test — otherwise `-m dbt` would start demanding a Postgres server.
        ("dbt", False),
        ("nodb", False),
    ],
)
def test_which_invocations_count_as_asking_for_the_database_suite(
    markexpr: str, requested: bool
) -> None:
    assert _root_conftest().db_suite_requested(markexpr) is requested


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
