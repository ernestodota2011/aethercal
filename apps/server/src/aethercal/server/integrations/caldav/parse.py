"""Pure transforms for the CalDAV freebusy busy-check (C-03): no network, fully testable.

Two standards-facing edges, kept pure so they are unit-tested without a live server:

* :func:`build_freebusy_report_body` shapes the RFC 4791 §7.10 ``free-busy-query`` REPORT body.
* :func:`parse_freebusy` maps the ``VFREEBUSY`` document a server answers with into
  ``aethercal-core`` ``TimeInterval``s.

A ``free-busy-query`` REPORT is deliberately chosen over a ``calendar-query`` of raw ``VEVENT``s:
the SERVER expands recurrences and merges overlaps and hands back the busy periods already computed,
so this code never reasons about ``RRULE`` -- the correct-by-construction tool for a busy-check.

==UNKNOWN IS NEVER FREE (RF-13).== A response we cannot parse as iCalendar, or one that omits the
``VFREEBUSY`` component entirely, RAISES rather than returning an empty (and dangerously "all-free")
busy set -- treating an unreadable calendar as free is exactly how a double-booking slips through.
Only a well-formed ``VFREEBUSY`` that genuinely lists no busy periods maps to an empty result. The
untyped ``icalendar`` parser (already a server dependency, used to BUILD the .ics invite) is reused
here to READ freebusy, so we do not hand-roll an iCalendar parser.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from icalendar import Calendar

from aethercal.core.model import TimeInterval

# iCalendar UTC instant form (RFC 5545 ``DATE-TIME`` with the ``Z`` designator) -- what a CalDAV
# server's ``<C:time-range>`` expects for both bounds.
_ICAL_UTC = "%Y%m%dT%H%M%SZ"

# The default TLS port, so ``https://h`` and ``https://h:443`` compare as the same origin.
_HTTPS_PORT = 443


def validate_caldav_endpoint(server_url: str, calendar_url: str) -> None:
    """Refuse an endpoint that would leak the Basic-auth secret over cleartext or to a foreign host.

    HTTP Basic sends the app-password on EVERY request -- base64-encoded, NOT encrypted -- so where
    that request goes is a credential-exposure decision, not a cosmetic one:

    * ``server_url`` must be ``https``: a cleartext CalDAV leaks the password on the wire.
    * an ABSOLUTE ``calendar_url`` must be ``https`` AND share ``server_url``'s origin (host+port):
      a calendar URL pointing at another host would send the host's credentials THERE. A RELATIVE
      ``calendar_url`` is resolved against ``server_url`` by the client, so it is same-origin by
      construction and allowed.

    Raises :class:`ValueError` on a violation. Called at connect time (the door, where operator
    input enters) AND again by the live client before it sends, so a value tampered with after
    storage is caught too (defense in depth).
    """
    server = urlsplit(server_url)
    if server.scheme != "https":
        raise ValueError(
            "CalDAV server_url must use https: HTTP Basic-auth credentials must not travel in "
            "cleartext"
        )
    if not server.hostname:
        # e.g. ``https:///dav`` -- a valid scheme but no host. Caught here because a RELATIVE
        # calendar_url returns early below (it trusts server_url's origin), so a hostless server_url
        # would otherwise be persisted and only fail, opaquely, when the client tried to connect.
        raise ValueError("CalDAV server_url must be an absolute https URL with a host")
    calendar = urlsplit(calendar_url)
    if not calendar.scheme and not calendar.netloc:
        return  # a relative calendar_url resolves against server_url -> same origin by construction
    if calendar.scheme != "https":
        raise ValueError("CalDAV calendar_url must use https")
    server_origin = (server.hostname, server.port or _HTTPS_PORT)
    calendar_origin = (calendar.hostname, calendar.port or _HTTPS_PORT)
    if calendar_origin != server_origin:
        raise ValueError(
            "CalDAV calendar_url must share the server_url origin: the host's credentials must not "
            "be sent to a different host"
        )


def caldav_account_id(server_url: str, username: str) -> str:
    """A stable account identity for a CalDAV connection: ``username@server-origin``.

    A host may connect the SAME username to DIFFERENT servers — ``admin`` on their own Nextcloud and
    ``admin`` on a work Radicale. The ``ExternalConnection`` identity is
    ``tenant+user+provider+account_email``, so keying only on the username would collapse the two
    into ONE row: the second connect would overwrite the first, and its busy set would silently
    vanish — and a missing busy set is a double-booking (RF-30 exists to union EVERY connection).
    Folding the server ORIGIN (host, and the port when non-default) into the identity keeps distinct
    servers distinct. Deterministic, so re-storing the same ``(server_url, username)`` maps to the
    same row (idempotent). ``server_url`` is validated ``https`` before this is called, so it has a
    host.
    """
    origin = urlsplit(server_url)
    host = (origin.hostname or "").lower()
    netloc = host if origin.port in (None, _HTTPS_PORT) else f"{host}:{origin.port}"
    return f"{username}@{netloc}"


def build_freebusy_report_body(window: TimeInterval) -> str:
    """Build the CalDAV ``free-busy-query`` REPORT body for ``window`` (RFC 4791 §7.10).

    The bounds are normalized to UTC because the iCalendar time-range wants ``Z`` instants; the
    ``TimeInterval`` may be built from bounds in any zone (they compare by absolute instant).
    """
    start = window.start.astimezone(UTC).strftime(_ICAL_UTC)
    end = window.end.astimezone(UTC).strftime(_ICAL_UTC)
    return (
        '<?xml version="1.0" encoding="utf-8" ?>\n'
        '<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
        f'  <C:time-range start="{start}" end="{end}"/>\n'
        "</C:free-busy-query>\n"
    )


def parse_freebusy(document: str) -> list[TimeInterval]:
    """Map a ``VFREEBUSY`` REPORT response into sorted UTC busy ``TimeInterval``s.

    Raises :class:`RuntimeError` if ``document`` does not parse as iCalendar or carries no
    ``VFREEBUSY`` component, rather than silently returning an empty (all-free) result -- treating
    an unknown calendar as free is a double-booking waiting to happen (RF-13). A well-formed
    ``VFREEBUSY`` with no busy periods (a genuinely free calendar) correctly returns ``[]``.

    Only ``FBTYPE`` values other than ``FREE`` count as busy: the RFC default (absent ``FBTYPE``) is
    ``BUSY``, and any ``BUSY-*`` variant blocks -- the safe direction is to over-block, never to
    treat something we do not recognize as free.
    """
    try:
        calendar = Calendar.from_ical(document)
    except (ValueError, IndexError, KeyError) as exc:
        raise RuntimeError(
            "could not parse the CalDAV freebusy response as iCalendar; refusing to treat the "
            "calendar as free (that would risk a double-booking)"
        ) from exc

    components = list(calendar.walk("VFREEBUSY"))
    if not components:
        raise RuntimeError(
            "CalDAV freebusy response contained no VFREEBUSY component; refusing to treat the "
            "calendar as free (that would risk a double-booking)"
        )

    intervals: list[TimeInterval] = []
    for component in components:
        periods = component.get("FREEBUSY")
        if periods is None:
            continue
        for period in periods if isinstance(periods, list) else [periods]:
            fbtype = str(period.params.get("FBTYPE", "BUSY")).upper()
            if fbtype == "FREE":
                continue
            start, raw_end = period.dt
            end = start + raw_end if isinstance(raw_end, timedelta) else raw_end
            intervals.append(TimeInterval(start=_as_utc(start), end=_as_utc(end)))
    intervals.sort(key=lambda interval: (interval.start, interval.end))
    return intervals


def _as_utc(moment: datetime) -> datetime:
    """Normalize an iCalendar instant to aware UTC; a floating (naive) instant is refused.

    A CalDAV freebusy response should carry UTC instants; a floating one has no anchored meaning, so
    it is rejected rather than guessed at -- a guessed zone could place a busy block in the wrong
    hour and either hide a conflict or blank a good slot.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise RuntimeError(
            "CalDAV freebusy period carried a floating (timezone-naive) instant; refusing it "
            "rather than guessing a zone"
        )
    return moment.astimezone(UTC)
