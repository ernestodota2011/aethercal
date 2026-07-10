"""Tests for the booking app's environment-driven settings."""

from __future__ import annotations

from aethercal.booking.settings import DEFAULT_API_URL, DEFAULT_BASE_URL, BookingSettings


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
