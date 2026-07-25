"""Unit tests for the pure CalDAV freebusy transforms + the injected query seam (C-03).

These never touch the network. They pin the two risky, standards-facing bits of the CalDAV busy
provider: the ``free-busy-query`` REPORT body we send, and the ``VFREEBUSY`` document we get back ->
``TimeInterval`` mapping -- so the integration is covered without a live server or credentials. The
``query_busy`` seam is exercised against a FAKE client that returns a recorded ``VFREEBUSY``, the
same injection pattern the Google integration uses.

==UNKNOWN IS NEVER FREE (RF-13).== A malformed response, or one that omits ``VFREEBUSY`` entirely,
must RAISE -- treating an unreadable calendar as free is exactly how a double-booking slips through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from aethercal.core.model import TimeInterval
from aethercal.server.integrations.caldav.client import _CaldavHttpClient, query_busy
from aethercal.server.integrations.caldav.parse import (
    build_freebusy_report_body,
    caldav_account_id,
    parse_freebusy,
    validate_caldav_endpoint,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _vfreebusy(freebusy_lines: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Nextcloud//CalDAV//EN\r\n"
        "BEGIN:VFREEBUSY\r\n"
        "DTSTART:20260710T000000Z\r\n"
        "DTEND:20260717T000000Z\r\n"
        f"{freebusy_lines}"
        "END:VFREEBUSY\r\n"
        "END:VCALENDAR\r\n"
    )


# --------------------------------------------------------------------------------------
# build_freebusy_report_body -- the RFC 4791 §7.10 REPORT body.
# --------------------------------------------------------------------------------------


def test_report_body_carries_the_utc_time_range() -> None:
    body = build_freebusy_report_body(
        TimeInterval(start=_utc(2026, 7, 10, 0), end=_utc(2026, 7, 17, 0))
    )
    assert "free-busy-query" in body
    assert 'xmlns:C="urn:ietf:params:xml:ns:caldav"' in body
    # iCalendar UTC form, which is what a CalDAV server's time-range expects.
    assert 'start="20260710T000000Z"' in body
    assert 'end="20260717T000000Z"' in body


def test_report_body_normalizes_a_non_utc_window_to_utc() -> None:
    minus_five = timezone(timedelta(hours=-5))
    body = build_freebusy_report_body(
        TimeInterval(
            start=datetime(2026, 7, 10, 9, 0, tzinfo=minus_five),  # 14:00Z
            end=datetime(2026, 7, 10, 12, 0, tzinfo=minus_five),  # 17:00Z
        )
    )
    assert 'start="20260710T140000Z"' in body
    assert 'end="20260710T170000Z"' in body


# --------------------------------------------------------------------------------------
# parse_freebusy -- VFREEBUSY document -> sorted busy intervals.
# --------------------------------------------------------------------------------------


def test_parse_maps_and_sorts_busy_periods() -> None:
    document = _vfreebusy(
        # Deliberately out of order: the 18:00 block is listed before the 09:00 one.
        "FREEBUSY;FBTYPE=BUSY:20260710T180000Z/20260710T183000Z\r\n"
        "FREEBUSY;FBTYPE=BUSY:20260710T090000Z/20260710T100000Z\r\n"
    )
    assert parse_freebusy(document) == [
        TimeInterval(start=_utc(2026, 7, 10, 9, 0), end=_utc(2026, 7, 10, 10, 0)),
        TimeInterval(start=_utc(2026, 7, 10, 18, 0), end=_utc(2026, 7, 10, 18, 30)),
    ]


def test_parse_handles_comma_separated_periods_and_duration_end() -> None:
    # One FREEBUSY line, two periods, the second expressed as start/DURATION (RFC 5545 period form).
    document = _vfreebusy(
        "FREEBUSY;FBTYPE=BUSY:20260711T090000Z/20260711T100000Z,20260712T140000Z/PT1H\r\n"
    )
    assert parse_freebusy(document) == [
        TimeInterval(start=_utc(2026, 7, 11, 9, 0), end=_utc(2026, 7, 11, 10, 0)),
        TimeInterval(start=_utc(2026, 7, 12, 14, 0), end=_utc(2026, 7, 12, 15, 0)),
    ]


def test_parse_ignores_free_periods() -> None:
    # FBTYPE=FREE is not busy; only BUSY* blocks a slot.
    document = _vfreebusy(
        "FREEBUSY;FBTYPE=FREE:20260710T090000Z/20260710T100000Z\r\n"
        "FREEBUSY;FBTYPE=BUSY:20260710T110000Z/20260710T120000Z\r\n"
    )
    assert parse_freebusy(document) == [
        TimeInterval(start=_utc(2026, 7, 10, 11, 0), end=_utc(2026, 7, 10, 12, 0)),
    ]


def test_parse_defaults_absent_fbtype_to_busy() -> None:
    # RFC 5545: the default FBTYPE is BUSY, so an unqualified FREEBUSY blocks (the safe direction).
    document = _vfreebusy("FREEBUSY:20260710T110000Z/20260710T120000Z\r\n")
    assert parse_freebusy(document) == [
        TimeInterval(start=_utc(2026, 7, 10, 11, 0), end=_utc(2026, 7, 10, 12, 0)),
    ]


def test_parse_empty_but_valid_vfreebusy_is_empty_not_an_error() -> None:
    # A reachable, genuinely-free calendar: a well-formed VFREEBUSY with no busy periods -> [].
    assert parse_freebusy(_vfreebusy("")) == []


def test_parse_raises_on_a_malformed_document() -> None:
    with pytest.raises(RuntimeError):
        parse_freebusy("<html><body>500 Internal Server Error</body></html>")


def test_parse_raises_when_the_response_has_no_vfreebusy() -> None:
    # A well-formed VCALENDAR that omits VFREEBUSY must NOT read as free (double-booking risk).
    no_vfreebusy = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nEND:VCALENDAR\r\n"
    with pytest.raises(RuntimeError, match="VFREEBUSY"):
        parse_freebusy(no_vfreebusy)


# --------------------------------------------------------------------------------------
# query_busy -- the injected client seam, driven offline by a fake.
# --------------------------------------------------------------------------------------


class _FakeCaldavClient:
    """Records the REPORT it was asked to run and returns a canned VFREEBUSY (or raises)."""

    def __init__(self, *, document: str | None = None, error: Exception | None = None) -> None:
        self._document = document
        self._error = error
        self.reports: list[tuple[str, str]] = []

    def report(self, calendar_url: str, *, body: str) -> str:
        if self._error is not None:
            raise self._error
        self.reports.append((calendar_url, body))
        assert self._document is not None
        return self._document


def test_query_busy_reports_against_the_calendar_and_parses_the_response() -> None:
    client = _FakeCaldavClient(
        document=_vfreebusy("FREEBUSY;FBTYPE=BUSY:20260711T090000Z/20260711T100000Z\r\n")
    )
    window = TimeInterval(start=_utc(2026, 7, 10, 0), end=_utc(2026, 7, 17, 0))
    calendar_url = "https://cloud.example/remote.php/dav/calendars/host/personal/"

    busy = query_busy(client, calendar_url, window)

    assert busy == [
        TimeInterval(start=_utc(2026, 7, 11, 9, 0), end=_utc(2026, 7, 11, 10, 0)),
    ]
    # It reported against the calendar URL it was given, with the time-range body for the window.
    (url, body) = client.reports[0]
    assert url == calendar_url
    assert 'start="20260710T000000Z"' in body


def test_query_busy_propagates_an_unreachable_server() -> None:
    client: Any = _FakeCaldavClient(error=RuntimeError("connection refused"))
    window = TimeInterval(start=_utc(2026, 7, 10, 0), end=_utc(2026, 7, 17, 0))

    # "Unreachable -> raise": the service layer catches this and degrades (RF-13); it never becomes
    # an empty (all-free) busy set here.
    with pytest.raises(RuntimeError, match="connection refused"):
        query_busy(client, "https://cloud.example/cal/", window)


# --------------------------------------------------------------------------------------
# validate_caldav_endpoint -- the app-password must never travel in cleartext or to a foreign host.
# --------------------------------------------------------------------------------------


def test_validate_accepts_https_same_origin_and_relative_calendars() -> None:
    # Absolute, same origin.
    validate_caldav_endpoint("https://cloud.example", "https://cloud.example/dav/cal/")
    # Same host with the explicit default TLS port is the same origin.
    validate_caldav_endpoint("https://cloud.example", "https://cloud.example:443/dav/cal/")
    # A relative calendar_url resolves against server_url -> same origin by construction.
    validate_caldav_endpoint("https://cloud.example", "/dav/cal/")


def test_validate_rejects_a_cleartext_server() -> None:
    # HTTP Basic over http would put the app-password on the wire in the clear.
    with pytest.raises(ValueError, match="https"):
        validate_caldav_endpoint("http://cloud.example", "http://cloud.example/dav/cal/")


def test_validate_rejects_a_cleartext_calendar_on_an_https_server() -> None:
    with pytest.raises(ValueError, match="https"):
        validate_caldav_endpoint("https://cloud.example", "http://cloud.example/dav/cal/")


def test_validate_rejects_a_foreign_host_calendar() -> None:
    # A calendar URL on another host would send the host's credentials there.
    with pytest.raises(ValueError, match="origin"):
        validate_caldav_endpoint("https://cloud.example", "https://evil.example/dav/cal/")


def test_validate_rejects_a_hostless_server() -> None:
    # ``https:///dav`` has a scheme but no host; a RELATIVE calendar_url would otherwise skip the
    # origin check and persist an endpoint that only fails opaquely at connect time.
    with pytest.raises(ValueError, match="host"):
        validate_caldav_endpoint("https:///dav", "/dav/cal/")
    with pytest.raises(ValueError, match="host"):
        validate_caldav_endpoint("https:///dav", "https://cloud.example/dav/cal/")


def test_validate_accepts_a_relative_calendar_against_a_valid_https_server() -> None:
    # The valid pairing the hostless check must not disturb: real server + relative calendar path.
    validate_caldav_endpoint("https://cloud.example", "/remote.php/dav/calendars/host/personal/")


def test_account_id_folds_in_the_server_origin() -> None:
    # The same username on two servers -> two distinct identities (no collision, RF-30).
    assert caldav_account_id("https://cloud-a.example", "admin") == "admin@cloud-a.example"
    assert caldav_account_id("https://cloud-b.example", "admin") == "admin@cloud-b.example"
    # Default TLS port is not part of the string; a non-default one is (still a distinct origin).
    assert caldav_account_id("https://cloud.example:443", "h") == "h@cloud.example"
    assert caldav_account_id("https://cloud.example:8443", "h") == "h@cloud.example:8443"
    # Deterministic + case-normalized host, so a re-store maps to the same row (idempotent).
    assert caldav_account_id("https://Cloud.Example", "h") == "h@cloud.example"


# --------------------------------------------------------------------------------------
# The live transport -- driven through an httpx.MockTransport so the REAL request it builds is
# asserted offline: method REPORT, Depth: 1 (NOT 0), the freebusy body, and Basic auth.
# --------------------------------------------------------------------------------------


def test_live_report_sends_a_depth_1_freebusy_report_and_returns_the_body() -> None:
    document = _vfreebusy("FREEBUSY;FBTYPE=BUSY:20260711T090000Z/20260711T100000Z\r\n")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=document)

    client = _CaldavHttpClient(
        server_url="https://cloud.example",
        username="host",
        password="app-password",
        transport=httpx.MockTransport(handler),
    )
    window = TimeInterval(start=_utc(2026, 7, 10, 0), end=_utc(2026, 7, 17, 0))

    out = client.report(
        "/remote.php/dav/calendars/host/personal/", body=build_freebusy_report_body(window)
    )

    assert out == document  # the transport's response body is returned verbatim for parsing
    request = seen[0]
    assert request.method == "REPORT"
    # ==Depth MUST be 1, never 0 (RFC 4791 §7.10).== The response covers the calendar object
    # resources "that satisfy the Depth value"; Depth: 0 applies to the collection resource itself
    # (not a VEVENT), so a conformant server returns an EMPTY VFREEBUSY -> the host reads as free
    # while booked -> a silent double-booking (RF-13). This assertion stops it regressing to 0.
    assert request.headers["Depth"] == "1"
    assert request.headers["Content-Type"].startswith("application/xml")
    # HTTP Basic: the app-password rides every request (why the endpoint is origin-gated).
    assert request.headers["Authorization"].startswith("Basic ")
    assert "free-busy-query" in request.content.decode("utf-8")
    assert str(request.url) == "https://cloud.example/remote.php/dav/calendars/host/personal/"
