"""The Alembic config must survive a URL containing a ``%`` (RF-19: the operator's own password).

Found while trying to run the ``-m db`` suite against an isolated schema, which needs a
``?options=-csearch_path=...`` query parameter; SQLAlchemy renders the ``=`` as ``%3D`` and the boot
migrator died with::

    ValueError: invalid interpolation syntax in '...aethercal_test?options=-csearch_path%3D...'

That is not a quirk of the test harness. ``Config.set_main_option`` writes through **configparser**,
which treats ``%`` as its interpolation sigil — and ``URL.render_as_string`` percent-encodes every
reserved character in the **password**. So a self-hoster whose Postgres password contains a ``%``,
an ``@``, a ``/`` or a ``:`` — which is to say, most generated passwords — cannot migrate at all.
The database never comes up, and the traceback names configparser rather than the password, so
nobody would guess why.

It was outside this cut's scope. It is fixed at the root anyway, and proven to round-trip.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import aethercal.server.db as db_pkg
from aethercal.server.db.migrate import make_alembic_config

_PLAIN = "postgresql+psycopg://u:p@localhost:5432/db"
_PASSWORD_WITH_PERCENT = "postgresql+psycopg://u:p%25ss@localhost:5432/db"
_QUERY_WITH_PERCENT = "postgresql+psycopg://u:p@localhost:5432/db?options=-csearch_path%3Dmine"

VERSION_NUM_MAX_LENGTH = 32
"""``alembic_version.version_num`` is created as ``VARCHAR(32)``. That is Alembic's own schema."""

_REVISION_RE = re.compile(r"^revision:\s*str\s*=\s*['\"](?P<id>[^'\"]+)['\"]", re.MULTILINE)


@pytest.mark.parametrize(
    "url",
    [_PLAIN, _PASSWORD_WITH_PERCENT, _QUERY_WITH_PERCENT],
    ids=["plain", "percent-in-password", "percent-in-query"],
)
def test_the_url_round_trips_through_the_alembic_config(url: str) -> None:
    """What goes in must come back out. A ``%`` is the operator's data, not configparser syntax."""
    config = make_alembic_config(url)

    assert config.get_main_option("sqlalchemy.url") == url


def test_a_percent_in_the_password_does_not_raise() -> None:
    """The regression itself: this used to raise ValueError deep inside configparser, at boot, with
    a message that never once mentioned the password."""
    make_alembic_config(_PASSWORD_WITH_PERCENT)


def _revision_ids() -> list[tuple[str, str]]:
    """Every ``(filename, revision id)`` declared under ``migrations/versions``.

    Resolved from ``aethercal.server.db``, not from ``...db.migrations``: the migrations directory
    has no ``__init__.py`` (Alembic loads it by path), so it imports as a namespace package whose
    ``__file__`` is ``None`` — and a guard that silently globs an empty directory is a guard that
    passes by measuring nothing. Hence the ``assert found`` below, too."""
    versions = Path(str(db_pkg.__file__)).parent / "migrations" / "versions"
    assert versions.is_dir(), f"migrations/versions not found at {versions}"
    found: list[tuple[str, str]] = []
    for script in sorted(versions.glob("[0-9]*.py")):
        match = _REVISION_RE.search(script.read_text(encoding="utf-8"))
        assert match is not None, f"{script.name} declares no `revision: str = ...`"
        found.append((script.name, match.group("id")))
    assert found, "no migration scripts found — this guard would pass by measuring nothing"
    return found


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    """==A long revision id passes every offline test and breaks the PRODUCTION boot migration.==

    ``alembic_version.version_num`` is ``VARCHAR(32)``. **SQLite does not enforce a VARCHAR
    length**, so an over-long id sails through the entire offline suite — models, migration parity,
    the lot — and then dies on PostgreSQL at the one statement nobody exercises locally: the UPDATE
    that stamps the new version. The failure lands during a self-hoster's upgrade, from inside the
    boot migrator, naming ``StringDataRightTruncation`` rather than the id somebody chose.

    Written after ``0006_webhook_delivery_error_reason`` (34 chars) did exactly that. The ``-m db``
    suite caught it — which is the whole argument for that suite failing rather than skipping when
    Postgres is absent — but it should never have needed a database to catch. Now it does not.
    """
    too_long = [
        (name, revision, len(revision))
        for name, revision in _revision_ids()
        if len(revision) > VERSION_NUM_MAX_LENGTH
    ]
    assert not too_long, (
        "these revision ids do not fit alembic_version.version_num (VARCHAR(32)) and will break "
        f"the boot migration on PostgreSQL: {too_long}"
    )
