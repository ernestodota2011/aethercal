"""Tests for the booking app's environment-driven settings."""

from __future__ import annotations

from collections.abc import Mapping

from aethercal.booking.settings import (
    BOOKING_SECRET_ENV,
    DEFAULT_API_URL,
    DEFAULT_BASE_URL,
    DEFAULT_EMBED_ALLOWED_ORIGINS,
    DEFAULT_TRUSTED_PROXIES,
    BookingSettings,
)

# Synthetic. Not a redaction of anything.
SECRET = "not-a-real-booking-secret-for-tests"


def _env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment plus the session secret, which is REQUIRED and has no default.

    Every test below is about some OTHER variable, so the secret is supplied here rather than
    repeated a dozen times. The secret's own contract — that its absence fails the boot — is
    ``tests/test_session_key.py``'s job, and it is deliberately NOT weakened here: this helper adds
    the variable, it does not add a default.
    """
    return {BOOKING_SECRET_ENV: SECRET, **(extra or {})}


def test_from_env_uses_defaults_when_absent() -> None:
    settings = BookingSettings.from_env(_env())
    assert settings.api_url == DEFAULT_API_URL
    # ==No ``api_key`` any more.== The page held a full-permission key in the most exposed
    # process in the system; the public API removed the need, so the field is DELETED, not
    # defaulted.
    assert not hasattr(settings, "api_key")
    assert settings.tenant_slug is None
    assert settings.turnstile_site_key is None
    assert settings.default_locale == "es"


def test_from_env_reads_provided_values() -> None:
    settings = BookingSettings.from_env(
        _env(
            {
                "AETHERCAL_API_URL": "https://api.aethercal.test",
                "AETHERCAL_TENANT_SLUG": "acme",
                "AETHERCAL_TURNSTILE_SITE_KEY": "0x_public_site_key",
                "AETHERCAL_BOOKING_DEFAULT_LOCALE": "en",
            }
        )
    )
    assert settings.api_url == "https://api.aethercal.test"
    assert settings.tenant_slug == "acme"
    assert settings.turnstile_site_key == "0x_public_site_key"
    assert settings.default_locale == "en"


def test_from_env_strips_trailing_slash_from_api_url() -> None:
    settings = BookingSettings.from_env(_env({"AETHERCAL_API_URL": "https://api.test/"}))
    assert settings.api_url == "https://api.test"


def test_from_env_treats_a_blank_tenant_slug_as_absent() -> None:
    settings = BookingSettings.from_env(_env({"AETHERCAL_TENANT_SLUG": "   "}))
    assert settings.tenant_slug is None


def test_from_env_treats_a_blank_turnstile_site_key_as_absent() -> None:
    """A blank public key renders no widget — which is not a bypass: the API verifies the token
    server-side and refuses a booking with none. The page can fail to ask; it cannot answer."""
    settings = BookingSettings.from_env(_env({"AETHERCAL_TURNSTILE_SITE_KEY": "   "}))
    assert settings.turnstile_site_key is None


def test_from_env_falls_back_to_es_for_unsupported_locale() -> None:
    settings = BookingSettings.from_env(_env({"AETHERCAL_BOOKING_DEFAULT_LOCALE": "fr"}))
    assert settings.default_locale == "es"


def test_from_env_uses_default_base_url_when_absent() -> None:
    settings = BookingSettings.from_env(_env())
    assert settings.base_url == DEFAULT_BASE_URL


def test_from_env_reads_base_url_and_strips_trailing_slash() -> None:
    settings = BookingSettings.from_env(
        _env({"AETHERCAL_BOOKING_BASE_URL": "https://book.example.com/"})
    )
    assert settings.base_url == "https://book.example.com"


def test_from_env_defaults_trusted_proxies_to_empty_secure_by_default() -> None:
    # Secure-by-default: with no explicit AETHERCAL_BOOKING_TRUSTED_PROXIES, NO peer is trusted, so
    # CF-Connecting-IP is never honored and the transport address is always used. Production behind
    # a reverse proxy MUST set the var with the proxy's concrete CIDR (done in the deploy).
    settings = BookingSettings.from_env(_env())
    assert settings.trusted_proxies == ()
    assert DEFAULT_TRUSTED_PROXIES == ()


def test_from_env_parses_trusted_proxies_csv_and_drops_blanks() -> None:
    settings = BookingSettings.from_env(
        _env({"AETHERCAL_BOOKING_TRUSTED_PROXIES": " 203.0.113.0/24 , ,198.51.100.7 "})
    )
    assert settings.trusted_proxies == ("203.0.113.0/24", "198.51.100.7")


def test_from_env_blank_trusted_proxies_falls_back_to_empty_default() -> None:
    settings = BookingSettings.from_env(_env({"AETHERCAL_BOOKING_TRUSTED_PROXIES": "   "}))
    assert settings.trusted_proxies == ()


# ---------------------------------------------------------------------------------------
# B0: the /embed/* CSP `frame-ancestors` allow-list (empty by default -> `*` in app.py).
# ---------------------------------------------------------------------------------------


def test_from_env_defaults_embed_allowed_origins_to_empty() -> None:
    settings = BookingSettings.from_env(_env())
    assert settings.embed_allowed_origins == ()
    assert DEFAULT_EMBED_ALLOWED_ORIGINS == ()


def test_from_env_parses_embed_allowed_origins_csv_and_drops_blanks() -> None:
    settings = BookingSettings.from_env(
        _env(
            {
                "AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS": (
                    " https://a.example , ,https://b.example:8443 "
                )
            }
        )
    )
    assert settings.embed_allowed_origins == ("https://a.example", "https://b.example:8443")


def test_from_env_blank_embed_allowed_origins_falls_back_to_empty_default() -> None:
    settings = BookingSettings.from_env(_env({"AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS": "   "}))
    assert settings.embed_allowed_origins == ()
