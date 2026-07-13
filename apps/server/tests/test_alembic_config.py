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

import pytest

from aethercal.server.db.migrate import make_alembic_config

_PLAIN = "postgresql+psycopg://u:p@localhost:5432/db"
_PASSWORD_WITH_PERCENT = "postgresql+psycopg://u:p%25ss@localhost:5432/db"
_QUERY_WITH_PERCENT = "postgresql+psycopg://u:p@localhost:5432/db?options=-csearch_path%3Dmine"


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
