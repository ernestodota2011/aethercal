"""Offline unit tests for the IANA-zone rule behind ``GET /slots`` (F1-04, RF-16).

The ``tz`` query param must name a real IANA zone. That rule is NOT this module's to define — it
belongs to :func:`aethercal.schemas.bookings.require_iana_zone`, the one public owner the guest
booking contract and the host CRUD (``services/users.py``) already answer to. ``api/slots.py`` only
adapts it: it catches the rule's refusal at the edge and dresses it in the endpoint's 422 envelope.

These tests are the executable spec for that seam, and they exist to hold two separate lines:

* **The 422 ``message`` is an API contract string.** The booking page and the SDK read it. Reusing
  the shared rule must not have reworded it, so the exact text is pinned here character for
  character — the rule's own ``ValueError`` says something different ("unknown timezone: ..."), and
  letting that leak to the wire would be a silent, breaking contract change.
* **One owner, forever.** The differential below asserts the endpoint accepts a zone if and only if
  the public rule does. It is the regression guard against a fourth copy of ``ZoneInfo(...)`` being
  re-implemented here the next time someone needs a 422 — which is how two surfaces come to disagree
  about what a real zone is.

DB-free (the rule is a pure string check), so these run in the offline matrix, like
``test_slots_window_cap.py``. The HTTP-level status contract lives in the db-marked
``test_slots_api.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from aethercal.schemas.bookings import require_iana_zone
from aethercal.server.api.slots import _require_iana_zone

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

#: Strings that are plainly not zones, refused identically on every platform.
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

#: Not-zones that ``ZoneInfo`` answers with an **OSError**, not the ``ZoneInfoNotFoundError`` its
#: docs lead you to expect — so a validator catching only ``ZoneInfoNotFoundError`` and
#: ``ValueError`` lets them through as an unhandled crash. Two shapes reach it:
#:
#: * a key naming a DIRECTORY of the tz database (``"America"``, ``"Etc"`` — folders, not zones):
#:   opening one raises ``IsADirectoryError`` on Linux, ``PermissionError`` on Windows;
#: * a key the filesystem cannot even name (whitespace, a newline, 300 characters): ``EINVAL`` /
#:   ``ENAMETOOLONG``.
#:
#: They are refused cleanly now, on both platforms and from either zone source (the system tz tree
#: or the ``tzdata`` wheel). Kept as their own list because that is the bug this file was born from:
#: before the fix, every one of these was a 500 on a request the contract answers with a 422.
CRASHED_BEFORE = [
    "America",
    "Etc",
    " ",
    "UTC\n",
    "A" * 300,
]

# DELIBERATELY NOT ASSERTED: "utc", "UTC ". Whether they are zones depends on the FILESYSTEM, not on
# the rule — ``ZoneInfo`` resolves a single-component key by opening a file, so a case-insensitive
# (Windows, macOS) volume accepts ``"utc"``, and Windows additionally trims a trailing space and
# accepts ``"UTC "``. Linux refuses both. Pinning either answer would just make this suite fail on
# the other operating system. The divergence is real and worth closing, but it belongs to the rule's
# own definition (see the report), not to this endpoint's translation of it.


def test_the_422_message_is_the_exact_contract_string() -> None:
    """The wording of the 422 is a published contract — the booking page and the SDK read it.

    Pinned character for character. The shared rule refuses with a DIFFERENT sentence (lowercase,
    "unknown timezone: ..."); the endpoint must translate, not forward. If this test fails because
    the message changed, that is not a refactor — it is a breaking API change.
    """
    with pytest.raises(HTTPException) as exc_info:
        _require_iana_zone("Not/AZone")

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail == {
        "error": "invalid_timezone",
        "message": "Unknown timezone: 'Not/AZone'",
    }


def test_the_message_quotes_the_zone_the_caller_actually_sent() -> None:
    """The rejected value is echoed back with ``!r`` — the caller sees exactly what they typed."""
    with pytest.raises(HTTPException) as exc_info:
        _require_iana_zone("Mars/Phobos")

    assert exc_info.value.detail["message"] == "Unknown timezone: 'Mars/Phobos'"


@pytest.mark.parametrize("zone", REAL_ZONES)
def test_a_real_zone_is_accepted(zone: str) -> None:
    _require_iana_zone(zone)


@pytest.mark.parametrize("zone", NOT_ZONES)
def test_a_string_that_is_not_a_zone_is_refused_with_a_clean_422(zone: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_iana_zone(zone)

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail["error"] == "invalid_timezone"


@pytest.mark.parametrize("zone", CRASHED_BEFORE)
def test_a_zone_key_the_filesystem_chokes_on_is_refused_not_crashed(zone: str) -> None:
    """The regression guard for the 500 this endpoint used to return (see ``CRASHED_BEFORE``).

    ``GET /slots?tz=America`` asks about a FOLDER of the tz database. It is not a zone, so the only
    correct answer is the same clean 422 every other bad zone gets — not an unhandled ``OSError``
    surfacing as a 500 and paging someone.
    """
    with pytest.raises(HTTPException) as exc_info:
        _require_iana_zone(zone)

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail["error"] == "invalid_timezone"
    assert exc_info.value.detail["message"] == f"Unknown timezone: {zone!r}"


@pytest.mark.parametrize("zone", [*REAL_ZONES, *NOT_ZONES, *CRASHED_BEFORE])
def test_the_endpoint_agrees_with_the_public_rule_exactly(zone: str) -> None:
    """One owner: ``/slots`` accepts a zone **iff** the public rule does.

    This is the guard against the third copy growing back. Two surfaces that each decide for
    themselves what a real zone is will eventually decide differently — so the endpoint is not
    allowed an opinion of its own, only a translation of the rule's.
    """
    try:
        require_iana_zone(zone)
    except ValueError:
        rule_accepts = False
    else:
        rule_accepts = True

    try:
        _require_iana_zone(zone)
    except HTTPException:
        endpoint_accepts = False
    else:
        endpoint_accepts = True

    assert endpoint_accepts == rule_accepts
