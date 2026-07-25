"""Live CalDAV client for the busy-check (C-03): the untyped HTTP transport behind an ``Any`` seam.

READ-ONLY. It issues one request -- a ``free-busy-query`` REPORT -- and never writes: there is no
event create/delete here by design (RNF-9 gives AetherCal a second BUSY provider; it does not give
CalDAV a booking write path). The request shaping and the response parsing are the pure, tested
helpers in ``parse.py``; the network edge (``build_service`` and ``_CaldavHttpClient``) is
``# pragma: no cover - live`` exactly like ``integrations/google/calendar.py``.

The client is INJECTED into :func:`query_busy` (a real one from :func:`build_service`, a fake in the
tests), so the busy-read is driven entirely offline against a recorded ``VFREEBUSY`` -- the same
injection seam the Google integration uses.
"""

from __future__ import annotations

from typing import Any

import httpx

from aethercal.core.model import TimeInterval

from .parse import build_freebusy_report_body, parse_freebusy, validate_caldav_endpoint

# The underlying HTTP call is BLOCKING; callers offload it with ``asyncio.to_thread`` and wrap it in
# the outbox's ``asyncio.timeout``. Cancelling that await cannot kill the worker thread, so a
# bounded transport is what actually stops a stalled socket -- and it MUST sit well under the outbox
# lease (5 min) so a hung CalDAV read cannot outlive its lease and be re-drained. Mirrors
# ``GOOGLE_HTTP_TIMEOUT_SECONDS`` in the Google integration.
CALDAV_HTTP_TIMEOUT_SECONDS = 20.0

# ``Depth: 1`` -- NOT 0, and this is grounded in the spec, not a preference. RFC 4791 §7.10 says the
# response "describes the busy time intervals for the calendar object resources ... that satisfy the
# Depth value ... If no calendar object resources are found to satisfy these conditions, a VFREEBUSY
# component with no FREEBUSY property MUST be returned." A ``free-busy-query`` REPORT runs against
# the COLLECTION; with ``Depth: 0`` it applies to the collection resource itself -- not a calendar
# object resource -- so ZERO resources satisfy the Depth and a conformant server returns an EMPTY
# VFREEBUSY: the host reads as entirely FREE while genuinely booked, the exact silent double-booking
# RF-13 exists to prevent. The §7.10.1 worked example uses ``Depth: 1`` against a calendar
# collection and returns two busy intervals. A test pins this value to ``"1"``.
CALDAV_REPORT_DEPTH = "1"
_FREEBUSY_REPORT_HEADERS = {
    "Depth": CALDAV_REPORT_DEPTH,
    "Content-Type": 'application/xml; charset="utf-8"',
}


def query_busy(service: Any, calendar_url: str, window: TimeInterval) -> list[TimeInterval]:
    """Query freebusy for ``calendar_url`` over ``window`` and return its busy intervals.

    ``service`` only transports: it takes a REPORT body and returns the response text. The body and
    the parse are the pure helpers in ``parse.py``, so this whole function runs offline against a
    fake client (a recorded ``VFREEBUSY``). A malformed or ``VFREEBUSY``-less response raises inside
    ``parse_freebusy`` (RF-13: unknown is never free); an unreachable server raises from the
    transport. Both propagate to the service layer, which catches them and degrades safely.
    """
    document = service.report(calendar_url, body=build_freebusy_report_body(window))
    return parse_freebusy(document)


class _CaldavHttpClient:
    """The real HTTP transport: HTTP Basic auth + a bounded-timeout REPORT. Untyped, seam-hidden.

    Holds the connection SETTINGS, not an open ``httpx.Client``: each ``report`` opens a short-lived
    client inside a ``with`` block and closes it, so a refresh cycle (which builds a fresh service
    per connection) never leaks a connection pool. A busy-check is one request per calendar, so
    there is nothing to gain from a persistent pool and a socket leak to lose.

    ``transport`` is injected ONLY by the tests (an :class:`httpx.MockTransport`), so the real
    request the live path builds -- method ``REPORT``, ``Depth: 1``, the freebusy body, the
    ``Authorization`` header -- is asserted offline without a network. In production it is ``None``
    and httpx uses its default (real) transport.
    """

    def __init__(
        self,
        *,
        server_url: str,
        username: str,
        password: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._server_url = server_url
        self._auth = (username, password)
        self._transport = transport

    def report(self, calendar_url: str, *, body: str) -> str:
        """Issue the ``free-busy-query`` REPORT against ``calendar_url``; return the body text."""
        # Re-validate against the SETTINGS actually about to be used: the app-password rides every
        # request, so a value tampered with in storage must not send it over cleartext or to a
        # foreign host, even though connect-time already checked (defense in depth).
        validate_caldav_endpoint(self._server_url, calendar_url)
        with httpx.Client(
            base_url=self._server_url,
            auth=self._auth,
            timeout=CALDAV_HTTP_TIMEOUT_SECONDS,
            transport=self._transport,
        ) as client:
            response = client.request(
                "REPORT",
                calendar_url,
                content=body.encode("utf-8"),
                headers=_FREEBUSY_REPORT_HEADERS,
            )
        response.raise_for_status()
        return response.text


def build_service(  # pragma: no cover - live
    *, server_url: str, username: str, password: str
) -> Any:
    """Build a live CalDAV client (HTTP Basic auth, bounded transport) for a stored connection.

    Wired by ``services.calendars.build_live_service`` when a connection's provider is ``caldav``.
    Kept out of the tested path because it constructs the client with the REAL (default) transport.

    LIVE VERIFICATION (devops, Nextcloud CT 201): the offline contract tests never touch a server;
    the one-time live smoke check against the agency Nextcloud (``drive.aetherlogik.com``) is run by
    devops with a real app-password (unavailable in the build environment) -- see
    ``docs/caldav-busy-check.md`` for the steps (connect, place a known busy block, confirm it lands
    in the host's busy set, and that a 401 degrades the host to ``UNAVAILABLE``, never "free").
    """
    return _CaldavHttpClient(server_url=server_url, username=username, password=password)
