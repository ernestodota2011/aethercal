"""The guest-facing copy is a contract, and a missing string is a quiet break of it.

``t()`` falls back to Spanish when the active catalog has no key — the right runtime behaviour (a
guest sees one translated sentence instead of a raw key or a 500) and, left alone, a silent one:
nobody would ever learn that the English page shipped a Spanish paragraph. So the catalogs are
pinned here, key for key, and the keys the views actually ask for are pinned to the catalogs. A
typo in a ``t(locale, "...")`` call fails CI instead of reaching a guest.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from aethercal.booking.i18n import DEFAULT_LOCALE, MESSAGES, SUPPORTED_LOCALES, t

_BOOKING_SRC = Path(__file__).resolve().parents[1] / "src" / "aethercal" / "booking"


def _keys_used_in_source() -> set[str]:
    """Every literal key passed to ``t(...)`` in the app's own source, found on the AST.

    Literals only, deliberately: a key built at runtime cannot be pinned, and there is none — the
    catalog is a fixed vocabulary and this asserts the codebase treats it as one.
    """
    used: set[str] = set()
    for path in sorted(_BOOKING_SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "t":
                continue
            # ``t(locale, "key", ...)`` — the key is the second positional argument.
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                key = node.args[1].value
                if isinstance(key, str):
                    used.add(key)
    return used


def test_every_locale_has_exactly_the_same_keys() -> None:
    """A key present in one catalog and not the other is a page that silently mixes languages."""
    reference = set(MESSAGES[DEFAULT_LOCALE])
    for locale in SUPPORTED_LOCALES:
        assert set(MESSAGES[locale]) == reference, (
            f"{locale} and {DEFAULT_LOCALE} disagree: "
            f"missing {sorted(reference - set(MESSAGES[locale]))}, "
            f"extra {sorted(set(MESSAGES[locale]) - reference)}"
        )


def test_no_message_is_blank() -> None:
    """An empty string renders as a hole in the page — worse than the key it replaced."""
    for locale in SUPPORTED_LOCALES:
        blanks = sorted(key for key, value in MESSAGES[locale].items() if not value.strip())
        assert blanks == [], f"{locale} has blank messages: {blanks}"


def test_every_key_the_views_ask_for_exists_in_every_catalog() -> None:
    """==A typo in a key is invisible at runtime== (the Spanish fallback covers it) and obvious
    here."""
    used = _keys_used_in_source()
    assert used, "the AST walk found no t(...) keys at all — the detector broke, not the copy"
    for locale in SUPPORTED_LOCALES:
        missing = sorted(key for key in used if key not in MESSAGES[locale])
        assert missing == [], f"{locale} is missing keys the views ask for: {missing}"


def test_a_missing_key_is_LOUD_at_runtime(caplog: pytest.LogCaptureFixture) -> None:
    """The fallback still happens (a guest must never see a raw key), but it says so.

    The test suite catches drift first; this line is what makes a production deployment carrying an
    unknown catalog visible in the logs instead of in a guest's mixed-language page.
    """
    with caplog.at_level(logging.WARNING):
        message = t("en", "this_key_does_not_exist")

    assert message == "this_key_does_not_exist"  # visible, not a crash
    assert any(
        "missing" in record.getMessage() and "this_key_does_not_exist" in record.getMessage()
        for record in caplog.records
    )
