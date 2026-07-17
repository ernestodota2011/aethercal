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

* **And ``available_timezones()`` is not the published set either — it is a filesystem walk too.**
  ==This one shipped, and only CI on Linux could see it.== That function returns IANA's listing
  UNIONED WITH A WALK OF ``TZPATH``, so it inherits the host's inventory: Debian and Ubuntu ship
  ``/usr/share/zoneinfo/localtime``, the ``tzdata`` wheel does not, and the wheel was pinned
  ``platform_system == 'Windows'``. So ``timezone: "localtime"`` was refused on the laptop and
  ACCEPTED in production, where it means the SERVER's local time rather than the zone the guest
  named — an appointment at the wrong hour. The rule had escaped ``ZoneInfo()`` for reading the
  filesystem and landed on a function that reads the filesystem.

The rule that kills all three: a timezone is a **member of the set IANA PUBLISHES** —
``core.tz.zones._published_zones``, the listing shipped inside ``tzdata`` — not a file that happens
to open, and not a file that happens to be present. A set lookup cannot be answered by a directory,
by a filesystem's opinion on case, or by a trailing space; and a listing read out of a wheel cannot
be answered by the host's distribution either. There is no ``ZoneInfo(...)`` call left in the rule,
so there is no ``OSError`` left to forget to catch.

==The tests carry the same scar.== Two of them used ``available_timezones()`` as their ORACLE, which
made them true on Windows and false on Linux: a test that shares the rule's mistake cannot judge it.
The oracle is now the published listing, and the test that actually discriminates —
:func:`test_a_file_sitting_on_the_tz_path_is_not_a_zone_name` — plants a TZif on a tz path it owns
instead of naming known-bad strings, so it reproduces the Linux defect on any OS and still bites the
day a distribution invents an artifact nobody has heard of.
"""

from __future__ import annotations

import zoneinfo
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

import pytest

from aethercal.core.tz import require_iana_zone
from aethercal.core.tz import zones as zones_module
from aethercal.core.tz.zones import _iana_zones, _published_zones

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


@pytest.fixture
def private_tz_tree(tmp_path: Path) -> object:
    """A tz path this test OWNS, restored afterwards — with the zone cache cleared either way."""
    zoneinfo.reset_tzpath([str(tmp_path)])
    _iana_zones.cache_clear()
    try:
        yield tmp_path
    finally:
        zoneinfo.reset_tzpath()
        _iana_zones.cache_clear()


def test_a_file_sitting_on_the_tz_path_is_not_a_zone_name(private_tz_tree: Path) -> None:
    """==A zone name is PUBLISHED, not present.== The bug this pins shipped for exactly one reason.

    ``available_timezones()`` is not the set IANA publishes: it is that set UNIONED WITH A WALK OF
    ``TZPATH``, so every TZif file a distribution happens to drop in its tz tree becomes a "zone".
    Debian and Ubuntu ship ``/usr/share/zoneinfo/localtime``; the ``tzdata`` wheel does not — so
    ``timezone: "localtime"`` was refused on the Windows laptop and ACCEPTED on the Linux
    deployment, where it resolves to the SERVER's local time instead of a zone the guest named. In a
    calendar that is an appointment at the wrong hour.

    The rule had already escaped ``ZoneInfo()`` for reading the filesystem — and landed on
    ``available_timezones()``, which reads the filesystem too.

    ==This test derives the condition rather than naming the string.== It plants a REAL TZif file on
    a tz path it owns, so it reproduces the Linux-only defect on any operating system — and it will
    still fail the day a distribution invents an artifact nobody has heard of, which a list of known
    bad names (``localtime``, ``posixrules``, …) could not do. The name below is incidental; that a
    file EXISTS is the whole point.
    """
    # A real zone's bytes, so the file is a genuine TZif — `available_timezones()` sniffs the magic
    # and would skip anything else, which would make this test pass while proving nothing.
    payload = resources.files("tzdata").joinpath("zoneinfo/UTC").read_bytes()
    (private_tz_tree / "localtime").write_bytes(payload)

    # ==The sabotage must be proven to have taken.== If the planting silently failed, every
    # assertion below would hold for the wrong reason and this test would be a green no-op.
    assert "localtime" in available_timezones(), (
        "the planted TZif was not picked up — this test cannot discriminate and must not pass"
    )

    with pytest.raises(ValueError, match="unknown timezone"):
        require_iana_zone("localtime")


def test_the_refusal_quotes_the_value_the_caller_actually_sent() -> None:
    with pytest.raises(ValueError, match=r"unknown timezone: 'Mars/Phobos'"):
        require_iana_zone("Mars/Phobos")


@pytest.mark.parametrize(
    "zone", [*REAL_ZONES, *NOT_ZONES, *UNNAMEABLE_ZONE_KEYS, *OS_DIVERGENT_KEYS]
)
def test_the_rule_is_exactly_membership_of_the_published_set(zone: str) -> None:
    """The whole rule, stated as a differential: accepted **iff** IANA PUBLISHES the name.

    ==The oracle used to be ``available_timezones()``, and that is precisely the bug this file now
    pins.== Stated that way the differential was true on Windows and FALSE on Linux — where
    ``available_timezones()`` also contains whatever TZif files the distribution's tz tree holds
    (``localtime``). A test cannot be the judge of a rule while sharing the rule's own mistake.

    This pins the CONTRACT (accepted ⟺ published); it is deliberately close to the implementation
    and cannot, by itself, catch the rule drifting back to the filesystem. The test that CAN is
    :func:`test_a_file_sitting_on_the_tz_path_is_not_a_zone_name`, which sabotages the tz tree, and
    :func:`test_every_zone_the_rule_accepts_can_be_constructed_by_the_renderers`, whose oracle is
    not a list at all.
    """
    try:
        require_iana_zone(zone)
    except ValueError:
        accepted = False
    else:
        accepted = True

    assert accepted == (zone in _published_zones())


def test_every_zone_iana_publishes_is_accepted() -> None:
    """Not a sample: the ENTIRE published set round-trips. The net was widened, not the refusal."""
    published = _published_zones()
    assert published, "no tz database available — the environment, not the rule, is broken"
    assert {require_iana_zone(zone) for zone in published} == published


def test_every_zone_the_rule_accepts_can_be_constructed_by_the_renderers() -> None:
    """==The promise the rule makes to everything downstream, checked against a DIFFERENT oracle.==

    ``require_iana_zone`` is a gate, and a gate that admits a name nothing can build would move the
    500 rather than remove it: ``localize``, ``is_ambiguous`` and ``is_imaginary`` all call
    ``ZoneInfo(tz)`` on whatever it waved through, as do the availability and recurrence engines.
    So the oracle here is not another list — it is ``zoneinfo`` itself actually constructing every
    accepted zone. That is what makes this test independent of the rule's own definition, and what
    would catch a published listing drifting out of step with the shipped tz DATA.
    """
    for zone in _published_zones():
        ZoneInfo(require_iana_zone(zone))


def test_the_rule_fails_closed_when_the_published_listing_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==Fail closed, loudly.== The tempting fallback is the one that caused all of this.

    Without the listing the "obvious" repair is to ask ``available_timezones()`` — i.e. the
    filesystem — which is the exact defect, and it would come back in whichever environment lost the
    wheel: silently, and only there. So the absence is an error, not a degradation.
    """

    def no_tzdata(_name: str) -> object:
        raise ModuleNotFoundError("No module named 'tzdata'")

    monkeypatch.setattr(zones_module.resources, "files", no_tzdata)
    with pytest.raises(RuntimeError, match="Refusing to fall back"):
        _published_zones()


def test_the_zone_set_is_built_once_not_on_every_call() -> None:
    """Cached: ``available_timezones()`` walks the tz tree (or reads the whole ``tzdata`` list).

    Paying that on every booking, every slots request and every model construction would put a
    filesystem walk on the hot path of a validator.
    """
    assert _iana_zones() is _iana_zones()
