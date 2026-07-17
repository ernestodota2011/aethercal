"""The boot-migration advisory lock is bounded: a stuck holder is a named error, not a hang.

``run_migrations`` used to take the lock with ``pg_advisory_lock``, which blocks forever behind a
holder that never releases -- a migration that hung, or a crashed booter whose session Postgres has
not reaped. That wedged the one-shot ``migrate`` job with no diagnostic. ``_acquire_migration_lock``
polls ``pg_try_advisory_lock`` to a deadline instead; these tests drive it with a fake connection
and an injected clock, no PostgreSQL required.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from aethercal.server.db.migrate import (
    MigrationLockUnavailableError,
    _acquire_migration_lock,
)


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _FakeLockConn:
    """Grants the lock (``pg_try_advisory_lock`` -> True) only after ``grant_after`` refusals."""

    def __init__(self, grant_after: int) -> None:
        self._grant_after = grant_after
        self.attempts = 0
        self.statements: list[str] = []

    def exec_driver_sql(self, sql: str, params: Any = None) -> _FakeResult:
        self.attempts += 1
        self.statements.append(sql)
        return _FakeResult(self.attempts > self._grant_after)


def _clock(values: list[float]) -> Any:
    """A monotonic() that yields each value in turn, then repeats the last one forever."""
    remaining = list(values)

    def monotonic() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return monotonic


def test_acquires_on_the_first_try() -> None:
    conn = _FakeLockConn(grant_after=0)
    slept: list[float] = []

    _acquire_migration_lock(conn, monotonic=_clock([0.0]), sleep=slept.append)

    assert conn.attempts == 1
    assert slept == []  # no waiting when the lock is free
    assert "pg_try_advisory_lock" in conn.statements[0]


def test_retries_until_the_lock_frees() -> None:
    conn = _FakeLockConn(grant_after=2)
    slept: list[float] = []

    _acquire_migration_lock(
        conn,
        timeout=timedelta(seconds=60),
        poll_interval=timedelta(seconds=1),
        monotonic=_clock([0.0, 1.0, 2.0]),  # well inside the deadline on every check
        sleep=slept.append,
    )

    assert conn.attempts == 3  # two refusals, then granted
    assert slept == [1.0, 1.0]  # polled once per refusal


def test_raises_a_named_error_when_the_holder_never_frees_it() -> None:
    conn = _FakeLockConn(grant_after=10_000)  # never grants
    slept: list[float] = []

    with pytest.raises(MigrationLockUnavailableError) as excinfo:
        _acquire_migration_lock(
            conn,
            timeout=timedelta(seconds=5),
            poll_interval=timedelta(seconds=1),
            # first check is inside the window, second is past the deadline
            monotonic=_clock([0.0, 6.0]),
            sleep=slept.append,
        )

    assert "pg_stat_activity" in str(excinfo.value)  # tells the operator where to look
    assert conn.attempts >= 1
