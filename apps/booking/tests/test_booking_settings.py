"""Tests for the booking app's environment-driven settings."""

from __future__ import annotations

from aethercal.booking.settings import (
    DEFAULT_API_URL,
    DEFAULT_BASE_URL,
    DEFAULT_TRUSTED_PROXIES,
    BookingSettings,
)


def test_from_env_uses_defaults_when_absent() -> None:
    settings = BookingSettings.from_env({})
    assert settings.api_url == DEFAULT_API_URL
    assert settings.api_key is None
    assert settings.default_locale == "es"


def test_from_env_reads_provided_values() -> None:
    settings = BookingSettings.from_env(
        {
            "AETHERCAL_API_URL": "https://api.aethercal.test",
            "AETHERCAL_API_KEY": "ack_live_secret",
            "AETHERCAL_BOOKING_DEFAULT_LOCALE": "en",
        }
    )
    assert settings.api_url == "https://api.aethercal.test"
    assert settings.api_key == "ack_live_secret"
    assert settings.default_locale == "en"


def test_from_env_strips_trailing_slash_from_api_url() -> None:
    settings = BookingSettings.from_env({"AETHERCAL_API_URL": "https://api.test/"})
    assert settings.api_url == "https://api.test"


def test_from_env_treats_blank_api_key_as_absent() -> None:
    settings = BookingSettings.from_env({"AETHERCAL_API_KEY": "   "})
    assert settings.api_key is None


def test_from_env_falls_back_to_es_for_unsupported_locale() -> None:
    settings = BookingSettings.from_env({"AETHERCAL_BOOKING_DEFAULT_LOCALE": "fr"})
    assert settings.default_locale == "es"


def test_from_env_uses_default_base_url_when_absent() -> None:
    settings = BookingSettings.from_env({})
    assert settings.base_url == DEFAULT_BASE_URL


def test_from_env_reads_base_url_and_strips_trailing_slash() -> None:
    settings = BookingSettings.from_env({"AETHERCAL_BOOKING_BASE_URL": "https://book.example.com/"})
    assert settings.base_url == "https://book.example.com"


def test_from_env_defaults_trusted_proxies_to_empty_secure_by_default() -> None:
    # Secure-by-default: with no explicit AETHERCAL_BOOKING_TRUSTED_PROXIES, NO peer is trusted, so
    # CF-Connecting-IP is never honored and the transport address is always used. Production behind
    # a reverse proxy MUST set the var with the proxy's concrete CIDR (done in the deploy).
    settings = BookingSettings.from_env({})
    assert settings.trusted_proxies == ()
    assert DEFAULT_TRUSTED_PROXIES == ()


def test_from_env_parses_trusted_proxies_csv_and_drops_blanks() -> None:
    settings = BookingSettings.from_env(
        {"AETHERCAL_BOOKING_TRUSTED_PROXIES": " 203.0.113.0/24 , ,198.51.100.7 "}
    )
    assert settings.trusted_proxies == ("203.0.113.0/24", "198.51.100.7")


def test_from_env_blank_trusted_proxies_falls_back_to_empty_default() -> None:
    settings = BookingSettings.from_env({"AETHERCAL_BOOKING_TRUSTED_PROXIES": "   "})
    assert settings.trusted_proxies == ()
