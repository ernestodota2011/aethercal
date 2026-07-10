"""Tests for locale selection and the bilingual string catalog (RNF-1: ES primary + EN)."""

from __future__ import annotations

from aethercal.booking.i18n import MESSAGES, SUPPORTED_LOCALES, select_locale, t


def test_default_locale_is_spanish() -> None:
    assert select_locale(query_lang=None, accept_language=None) == "es"


def test_query_param_selects_english() -> None:
    assert select_locale(query_lang="en", accept_language=None) == "en"


def test_query_param_is_case_insensitive() -> None:
    assert select_locale(query_lang="EN", accept_language=None) == "en"


def test_unsupported_query_param_falls_through() -> None:
    assert select_locale(query_lang="fr", accept_language="en-US,en;q=0.9") == "en"


def test_accept_language_header_is_used_when_no_query() -> None:
    assert select_locale(query_lang=None, accept_language="en-US,en;q=0.9") == "en"


def test_accept_language_picks_first_supported() -> None:
    assert select_locale(query_lang=None, accept_language="fr-FR,es;q=0.8,en;q=0.5") == "es"


def test_query_param_beats_header() -> None:
    assert select_locale(query_lang="es", accept_language="en-US") == "es"


def test_unknown_everything_falls_back_to_default() -> None:
    assert select_locale(query_lang="de", accept_language="fr-FR,it;q=0.5") == "es"


def test_catalog_has_both_locales_for_every_key() -> None:
    assert set(MESSAGES["es"]) == set(MESSAGES["en"])
    assert MESSAGES["es"]  # non-empty


def test_translate_returns_locale_specific_string() -> None:
    assert t("es", "book_cta") != t("en", "book_cta")


def test_translate_interpolates_kwargs() -> None:
    rendered = t("en", "confirmed_heading", title="Intro Call")
    assert "Intro Call" in rendered


def test_supported_locales_are_es_and_en() -> None:
    assert set(SUPPORTED_LOCALES) == {"es", "en"}
