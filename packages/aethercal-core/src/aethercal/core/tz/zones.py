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
from importlib import resources
from zoneinfo import ZoneInfo


def _published_zones() -> frozenset[str]:
    """The zone names the IANA tz database PUBLISHES, read from the ``tzdata`` distribution.

    ==This is the authority, and it is a data file — not a directory.== ``tzdata`` ships IANA's own
    zone listing verbatim; CPython's ``available_timezones()`` reads this very resource, then
    UNIONS a walk of ``TZPATH`` into it — and that union is the defect (see
    :func:`require_iana_zone`). Reading the listing alone is the same answer on every operating
    system, because a wheel's contents do not vary by host: a claim ``available_timezones()``
    cannot make.

    The cost is that the accepted set is pinned to the INSTALLED ``tzdata`` version rather than
    floating with the host's tz tree. That is the intended trade: a zone IANA has published but the
    wheel has not shipped yet is refused until the dependency is bumped — a reviewable, versioned
    act — where the alternative is accepting whatever a distribution drops in a directory. It
    constrains only what is ACCEPTED: ``ZoneInfo`` still reads the host's tz data when it has it.

    ==Fail closed.== A missing listing must never degrade into "ask the filesystem": that fallback
    IS the defect, and it would come back in the one environment nobody tests.
    """
    try:
        listing = resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8")
    except (ImportError, OSError) as exc:  # not installed, or installed without its data
        raise RuntimeError(
            "the IANA zone listing is unavailable: the 'tzdata' distribution is required to decide "
            "what a timezone IS. Refusing to fall back to a walk of the system tz tree, which "
            "answers a different question and differs between operating systems."
        ) from exc
    return frozenset(line.strip() for line in listing.splitlines() if line.strip())


@lru_cache(maxsize=1)
def _iana_zones() -> frozenset[str]:
    """The published IANA zone names, resolved once.

    Cached deliberately: :func:`_published_zones` reads a file, and a file read has no business on
    the hot path of a validator that runs for every booking, every slots request and every model
    construction. The set is a property of the installed tz database, which does not change under a
    running process.
    """
    return _published_zones()


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

    A timezone is a **member of the set IANA publishes** — :func:`_published_zones`. A set lookup
    cannot be answered by a directory, by a volume's opinion on case, or by a trailing space, and it
    accepts every real zone the renderers can later construct (aliases and fixed offsets included:
    ``US/Eastern``, ``Etc/GMT+5``, ``GMT``). There is no ``ZoneInfo(...)`` call left in this rule,
    so there is no ``OSError`` left to forget to catch — the broken ``except`` is not patched here,
    it is gone.

    * ==and the set is NOT ``available_timezones()``, which is where this rule went wrong twice.==
      This docstring used to name it, and to promise that a set lookup "is the same answer on every
      operating system". ==That was false, and CI proved it the first time it ran on Linux.==
      ``available_timezones()`` is the published listing UNIONED WITH A WALK OF ``TZPATH``: it is
      *also* derived from the filesystem, so it inherits the host's inventory. Debian and Ubuntu
      ship ``/usr/share/zoneinfo/localtime``; the ``tzdata`` wheel does not. ``"localtime"`` was
      therefore refused on a Windows developer's box and ACCEPTED in Linux production — where it
      silently means the SERVER's local time, not a zone the guest named, which in a calendar is an
      appointment at the wrong hour. Escaping ``ZoneInfo()`` for reading the filesystem only to land
      on a function that reads the filesystem is the same defect wearing the next mask; the prose
      was right about case and trailing spaces and wrong about WHICH ZONES EXIST, and no amount of
      re-reading it on Windows could have said so.

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
