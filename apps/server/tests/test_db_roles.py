"""The three database roles, and the boot assertion that is the ONLY thing able to see a mis-wire.

Under RLS almost nothing raises: a query on the wrong role does not fail, it returns **zero rows**.
A ``AETHERCAL_WORKER_DATABASE_URL`` pointing at the app role therefore produces a worker that drains
nothing, reports nothing and logs nothing — for ever. There is no exception to catch, so the only
detector available is to ASK the connection who it is and refuse to start when the answer is wrong.

These are the offline halves (the validators, the fail-closed accessors, the error message). The
effective-state halves — ``SELECT current_user`` over a real engine, per sessionmaker and per
process — are in ``tests/test_rls_isolation.py`` and are ``db``-marked, because a role does not
exist in SQLite.
"""

from __future__ import annotations

import pytest

from aethercal.server.db.config import MissingDatabaseUrlError
from aethercal.server.db.roles import DatabaseRoleError, DbRole, check_role
from aethercal.server.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"database_url": "postgresql://u:p@h/db", "app_secret": "s" * 16}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestTheRoleNames:
    def test_the_three_roles_are_exactly_these(self) -> None:
        assert {role.value for role in DbRole} == {
            "aethercal_app",
            "aethercal_owner",
            "aethercal_worker",
        }


class TestCheckRole:
    def test_the_expected_role_passes(self) -> None:
        check_role("aethercal_worker", DbRole.WORKER, url_env="AETHERCAL_WORKER_DATABASE_URL")

    def test_the_wrong_role_raises_and_names_the_variable_to_fix(self) -> None:
        with pytest.raises(DatabaseRoleError) as caught:
            check_role("aethercal_app", DbRole.WORKER, url_env="AETHERCAL_WORKER_DATABASE_URL")

        message = str(caught.value)
        assert "AETHERCAL_WORKER_DATABASE_URL" in message
        assert "aethercal_worker" in message
        assert "aethercal_app" in message


class TestTheOwnerUrlIsFailClosed:
    """Absent ``AETHERCAL_OWNER_DATABASE_URL``, the CLI and Alembic must NOT run.

    Falling back to the app role would make ``guest purge --tenant X`` touch **zero rows and exit
    green**: erasure of personal data reporting success without erasing anything.
    """

    def test_owner_config_without_the_url_raises(self) -> None:
        with pytest.raises(MissingDatabaseUrlError) as caught:
            _settings().owner_database_config()
        assert "AETHERCAL_OWNER_DATABASE_URL" in str(caught.value)

    def test_owner_config_with_the_url_normalizes_the_driver(self) -> None:
        config = _settings(owner_database_url="postgresql://o:p@h/db").owner_database_config()
        assert config.url.startswith("postgresql+psycopg://")


class TestTheWorkerUrlIsFailClosed:
    """Absent ``AETHERCAL_WORKER_DATABASE_URL``, the worker must NOT start.

    Falling back to the app role means ``select_due`` sees zero rows: confirmations, reminders and
    refunds stop, without one line of error.
    """

    def test_worker_config_without_the_url_raises(self) -> None:
        with pytest.raises(MissingDatabaseUrlError) as caught:
            _settings().worker_database_config()
        assert "AETHERCAL_WORKER_DATABASE_URL" in str(caught.value)

    def test_worker_config_with_the_url_normalizes_the_driver(self) -> None:
        config = _settings(worker_database_url="postgresql://w:p@h/db").worker_database_config()
        assert config.url.startswith("postgresql+psycopg://")


class TestRetiredToggles:
    """``run_scheduler`` and ``auto_migrate`` are RETIRED — and retiring them silently is the bug.

    ``extra="ignore"`` means an unknown ``AETHERCAL_*`` variable is simply dropped. So deleting the
    fields would leave an operator's ``AETHERCAL_RUN_SCHEDULER=1`` (which the shipped image sets!)
    quietly doing nothing, while they believe the drain is running. The fields therefore SURVIVE, as
    tripwires: a truthy value is a hard boot failure that names the replacement.
    """

    def test_run_scheduler_true_refuses_to_build_settings(self) -> None:
        with pytest.raises(ValueError, match="aethercal-worker"):
            _settings(run_scheduler=True)

    def test_run_scheduler_false_is_still_accepted(self) -> None:
        assert _settings(run_scheduler=False).run_scheduler is False

    def test_auto_migrate_true_refuses_to_build_settings(self) -> None:
        with pytest.raises(ValueError, match="db upgrade"):
            _settings(auto_migrate=True)

    def test_auto_migrate_defaults_to_off(self) -> None:
        assert _settings().auto_migrate is False
