"""The IANA-zone rule: what counts as a timezone, decided once, in the domain.

``require_iana_zone`` is the ONE owner of "is this a real timezone". It lives in ``core.tz``
because that is what it is: a pure rule about time, with no transport, no HTTP and no database in
it. ``core`` may not import ``schemas`` (the import-linter "core is pure" contract), and
``core.model.Event`` / ``core.model.Schedule`` need the rule too — so core is the only floor every
surface can stand on. ``schemas.bookings.require_iana_zone`` re-exports this function; it does not
re-implement it.

Two defects are pinned here, and they are the reason this file exists:

* **A zone key is not a filename.** ``ZoneInfo`` resolves a key to a PATH and opens it, so a key
  that is a DIRECTORY of the tz database (``"America"``, ``"Etc"``) or that the filesystem cannot
  name at all (whitespace, a newline, 300 characters) escapes as a raw ``OSError`` —
  ``IsADirectoryError`` / ``PermissionError`` / ``EINVAL`` — which is NOT a
  ``ZoneInfoNotFoundError`` and which no caller of a *validator* is catching. Uncaught, each one is
  a 500 where the contract promised a clean refusal.

* **And it is not a filename on a case-insensitive volume either.** ``"utc"`` opens the file ``UTC``
  on Windows/macOS and ``ZoneInfo`` accepts it; Windows further trims a trailing space and accepts
  ``"UTC "``. Linux refuses both. That made the dev machine and production DISAGREE about what a
  valid timezone is — the cheapest possible way to ship a bug: green on the laptop, green in a CI
  that happens to run the other OS, and a crash (or a silently different zone) in the one place that
  matters.

The rule that kills both: a timezone is a **member of the IANA set**, not a file that happens to
open. ``value in available_timezones()`` is a set lookup — it cannot be answered by a directory, by
a filesystem's opinion on case, or by a trailing space. There is no ``ZoneInfo(...)`` call left in
the rule, so there is no ``OSError`` left to forget to catch.
"""

from __future__ import annotations

from zoneinfo import available_timezones

import pytest

from aethercal.core.tz import require_iana_zone
from aethercal.core.tz.zones import _iana_zones

#: Zones every surface must accept — including the awkward ones (fixed-offset, alias, legacy).
REAL_ZONES = [
    "UTC",
    "America/New_York",
    "Europe/Madrid",
    "Asia/Calcutta",
    "Australia/Sydney",
    "Pacific/Kiritimati",
    "Etc/GMT+5",
    "US/Eastern",
    "GMT",
]

#: Strings that are plainly not zones, refused identically on every platform, before and after.
NOT_ZONES = [
    "Not/AZone",
    "Mars/Phobos",
    "",
    "america/new_york",
    " UTC",
    "Local",
    "localtime",
    "/etc/localtime",
    "..",
    "../UTC",
]

#: Not-zones ``ZoneInfo`` answers with a raw ``OSError``, never a ``ZoneInfoNotFoundError``: a
#: DIRECTORY of the tz database (``"America"``, ``"Etc"`` — folders, not zones) or a key the
#: filesystem cannot name (``EINVAL`` / ``ENAMETOOLONG``). Every one of them used to crash a
#: validator that caught only ``(ZoneInfoNotFoundError, ValueError)``.
UNNAMEABLE_ZONE_KEYS = [
    "America",
    "Etc",
    " ",
    "UTC\n",
    "A" * 300,
]

#: The dev/prod divergence, now closed. On a CASE-INSENSITIVE volume (Windows, macOS) ``ZoneInfo``
#: opened ``UTC`` for the key ``"utc"`` and accepted it; Windows also trims a trailing space and
#: accepted ``"UTC "``. Linux — production — refused both. Membership in the IANA set has no such
#: opinion: these are not zone names, so they are refused on every operating system.
OS_DIVERGENT_KEYS = [
    "utc",
    "utc ",
    "UTC ",
    "america/New_York",
]


@pytest.mark.parametrize("zone", REAL_ZONES)
def test_a_real_zone_is_accepted_and_returned_unchanged(zone: str) -> None:
    assert require_iana_zone(zone) == zone


@pytest.mark.parametrize("zone", NOT_ZONES)
def test_a_string_that_is_not_a_zone_is_refused(zone: str) -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone(zone)


@pytest.mark.parametrize("zone", UNNAMEABLE_ZONE_KEYS)
def test_a_key_the_filesystem_chokes_on_is_refused_not_crashed(zone: str) -> None:
    """Refused in the rule's own currency — a ``ValueError`` — never an escaping ``OSError``.

    The currency matters as much as the refusal: Pydantic turns a ``ValueError`` into a validation
    error (a 422 on the wire) and ``services/users.py`` catches exactly ``ValueError``. An
    ``OSError`` sails straight through all of them and lands as a 500.
    """
    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone(zone)


@pytest.mark.parametrize("zone", OS_DIVERGENT_KEYS)
def test_a_key_only_a_case_insensitive_filesystem_would_accept_is_refused(zone: str) -> None:
    """The same answer on Windows, macOS and Linux — the OS gets no vote.

    ``"utc"`` is not an IANA zone; it is a filename that happens to match ``UTC`` on a volume that
    ignores case. A rule that accepts it on the developer's laptop and refuses it in production is
    not a rule — it is a bug with two behaviours, and the one that breaks is production's.
    """
    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone(zone)


def test_the_refusal_quotes_the_value_the_caller_actually_sent() -> None:
    with pytest.raises(ValueError, match=r"unknown timezone: 'Mars/Phobos'"):
        require_iana_zone("Mars/Phobos")


@pytest.mark.parametrize(
    "zone", [*REAL_ZONES, *NOT_ZONES, *UNNAMEABLE_ZONE_KEYS, *OS_DIVERGENT_KEYS]
)
def test_the_rule_is_exactly_membership_of_the_iana_set(zone: str) -> None:
    """The whole rule, stated as a differential: accepted **iff** IANA knows the name.

    This is the regression guard against the rule drifting back into "whatever ``ZoneInfo`` manages
    to open" — which is the filesystem's answer to a different question, and the source of both
    defects above.
    """
    try:
        require_iana_zone(zone)
    except ValueError:
        accepted = False
    else:
        accepted = True

    assert accepted == (zone in available_timezones())


def test_every_zone_iana_publishes_is_accepted() -> None:
    """Not a sample: the ENTIRE published set round-trips. The net was widened, not the refusal."""
    published = available_timezones()
    assert published, "no tz database available — the environment, not the rule, is broken"
    assert {require_iana_zone(zone) for zone in published} == published


def test_the_zone_set_is_built_once_not_on_every_call() -> None:
    """Cached: ``available_timezones()`` walks the tz tree (or reads the whole ``tzdata`` list).

    Paying that on every booking, every slots request and every model construction would put a
    filesystem walk on the hot path of a validator.
    """
    assert _iana_zones() is _iana_zones()
