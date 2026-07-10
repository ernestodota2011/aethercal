"""Tests for mapping SDK/API errors to friendly, localized, non-leaking messages."""

from __future__ import annotations

from aethercal.booking.errors import friendly_api_error, friendly_unexpected
from aethercal.client import AetherCalAPIError


def test_slot_conflict_is_friendly_and_localized() -> None:
    exc = AetherCalAPIError(409, "slot_unavailable", "That time is no longer available")
    es = friendly_api_error(exc, "es")
    en = friendly_api_error(exc, "en")
    assert "horario" in es.lower()
    assert "available" in en.lower()
    # It never surfaces the raw internal message verbatim as the whole reply.
    assert es != exc.message
    assert en != exc.message


def test_expired_token_maps_to_link_message() -> None:
    exc = AetherCalAPIError(403, "forbidden", "Invalid or expired link")
    assert "enlace" in friendly_api_error(exc, "es").lower()
    assert "link" in friendly_api_error(exc, "en").lower()


def test_unknown_error_code_gets_generic_message() -> None:
    exc = AetherCalAPIError(500, "boom", "Traceback (most recent call last): secret internals")
    message = friendly_api_error(exc, "es")
    assert "secret internals" not in message
    assert "Traceback" not in message
    assert message  # non-empty, friendly


def test_availability_unavailable_message() -> None:
    exc = AetherCalAPIError(
        503, "availability_unavailable", "Host availability is temporarily down"
    )
    assert friendly_api_error(exc, "en")
    assert "down" not in friendly_api_error(exc, "en")  # no internal phrasing leaked


def test_unexpected_error_is_friendly() -> None:
    assert friendly_unexpected("es")
    assert friendly_unexpected("en")
