"""What a timezone IS, and wall-time <-> instant conversion with explicit DST gap/fold handling.

A "wall time" is a naive datetime interpreted in a given IANA zone. Because of DST some wall
times are *ambiguous* (they occur twice, at a fall-back) and some are *imaginary* (they never
occur, at a spring-forward). These helpers make that explicit so the recurrence and slot engines
never silently produce a wrong instant.

:func:`require_iana_zone` is the ONE definition of "this string names a real timezone", and it lives
here — in the domain — because that is what it is: a rule about time, with no transport, no HTTP and
no database in it. Every surface answers to this one function: the core models (``Event``,
``Schedule``), the booking contract (``schemas.bookings``, which RE-EXPORTS it), the host CRUD
(``server/services/users.py``), the ``GET /slots`` endpoint and the guest booking page. ``core`` may
not import ``schemas`` (the "core is pure" import contract), so core is the only floor all of them
can stand on — and four private copies of the check is precisely how they came to disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


@lru_cache(maxsize=1)
def _iana_zones() -> frozenset[str]:
    """The published IANA zone names, resolved once.

    Cached deliberately: ``available_timezones()`` reads the whole ``tzdata`` zone list, or walks
    the system tz tree, on every call — a filesystem walk has no business on the hot path of a
    validator that runs for every booking, every slots request and every model construction. The
    set is a property of the installed tz database, which does not change under a running process.
    """
    return frozenset(available_timezones())


def require_iana_zone(value: str) -> str:
    """Return ``value`` if it NAMES a real IANA zone; raise ``ValueError`` if it does not.

    ==Membership, not "does a file open".== ``ZoneInfo(key)`` does not answer this question: it
    resolves the key to a PATH and opens it, so its verdict is the *filesystem's* — and the
    filesystem answers a different question, one whose answer changes with the operating system:

    * a key naming a DIRECTORY of the tz database (``"America"``, ``"Etc"`` — folders, not zones)
      raises a raw ``OSError`` (``IsADirectoryError`` on Linux, ``PermissionError`` on Windows), and
      so does a key the filesystem cannot even name (whitespace, a newline, 300 characters:
      ``EINVAL`` / ``ENAMETOOLONG``). ``zoneinfo`` converts only ``(ImportError, FileNotFoundError,
      UnicodeEncodeError)`` into ``ZoneInfoNotFoundError``, so none of these becomes one — a
      validator catching ``(ZoneInfoNotFoundError, ValueError)`` lets every one through as an
      unhandled crash: a 500 on ``GET /slots?tz=America``, on a booking whose ``guest_timezone`` is
      ``"Etc"``, on an ``Event`` or a ``Schedule`` built from either.
    * on a CASE-INSENSITIVE volume (Windows, macOS) the key ``"utc"`` opens the file ``UTC`` and is
      ACCEPTED; Windows further trims a trailing space and accepts ``"UTC "``. Linux — production —
      refuses both. Development and production then disagree about what a valid timezone is, and the
      one that breaks is production. (The ``tzdata`` wheel, pinned only on Windows, is what kept the
      whole family latent: without it, Linux CI turns the same key into an ``ImportError``, which
      ``zoneinfo`` DOES convert. Green in CI, crash in production.)

    A timezone is a **member of the set IANA publishes** — ``available_timezones()``. A set lookup
    cannot be answered by a directory, by a volume's opinion on case, or by a trailing space; it is
    the same answer on every operating system, and it accepts every real zone the renderers can
    later construct (aliases and fixed offsets included: ``US/Eastern``, ``Etc/GMT+5``, ``GMT``).
    There is no ``ZoneInfo(...)`` call left in this rule, so there is no ``OSError`` left to forget
    to catch — the broken ``except`` is not patched here, it is gone.

    The refusal is a ``ValueError`` because that is the currency every caller already speaks:
    Pydantic turns it into a validation error (a 422 on the wire), ``services/users.py`` catches
    exactly ``ValueError`` to word its ``InvalidUserError``, and ``api/slots.py`` translates it into
    that endpoint's 422 envelope.
    """
    if value not in _iana_zones():
        raise ValueError(f"unknown timezone: {value!r}")
    return value


def _require_naive(walltime: datetime) -> None:
    if walltime.tzinfo is not None:
        raise ValueError("wall time must be naive (the timezone is passed separately)")


def localize(walltime: datetime, tz: str, *, fold: int = 0) -> datetime:
    """Attach the IANA ``tz`` to a naive ``walltime``.

    ``fold`` selects which instant an ambiguous wall time maps to (0 = the earlier, first
    occurrence; 1 = the later one), per PEP 495.
    """
    _require_naive(walltime)
    return walltime.replace(tzinfo=ZoneInfo(tz), fold=fold)


def to_instant(walltime: datetime, tz: str, *, fold: int = 0) -> datetime:
    """Convert a naive ``walltime`` in ``tz`` to its absolute UTC instant."""
    return localize(walltime, tz, fold=fold).astimezone(UTC)


def is_ambiguous(walltime: datetime, tz: str) -> bool:
    """True if ``walltime`` occurs twice in ``tz`` (a DST fall-back)."""
    _require_naive(walltime)
    zone = ZoneInfo(tz)
    return (
        walltime.replace(tzinfo=zone, fold=0).utcoffset()
        != walltime.replace(tzinfo=zone, fold=1).utcoffset()
    )


def is_imaginary(walltime: datetime, tz: str) -> bool:
    """True if ``walltime`` never occurs in ``tz`` (a DST spring-forward gap)."""
    _require_naive(walltime)
    zone = ZoneInfo(tz)
    aware = walltime.replace(tzinfo=zone)
    roundtrip = aware.astimezone(UTC).astimezone(zone)
    return roundtrip.replace(tzinfo=None) != walltime
