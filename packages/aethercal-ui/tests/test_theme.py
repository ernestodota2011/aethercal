"""Tests for the calendar color-theme model (F2-E theming; AetherCal-06 §7).

``Theme`` is the SINGLE SOURCE OF TRUTH for the calendar's ``--ac-*`` color tokens. It is a Pydantic
model with four named presets (light/dark/midnight/high_contrast); ``scripts/gen_theme_presets.py``
serializes the presets to ``theme-presets.json`` which the TypeScript layer imports (a Python drift
test keeps the committed JSON in sync, and a vitest test locks the TS side against it — same
generate-and-lock pattern as ``calendar-props.schema.json``).
"""

from __future__ import annotations

import pytest

from aethercal.ui.theme import PRESET_NAMES, PRESETS, Theme


def test_light_preset_exposes_the_expected_css_var_names() -> None:
    css_vars = Theme.light().to_css_vars()
    # Every key is an --ac-* custom property; nothing else leaks in.
    assert all(name.startswith("--ac-") for name in css_vars)
    assert "--ac-fg" in css_vars
    assert "--ac-bg" in css_vars
    assert "--ac-today-marker-bg" in css_vars
    assert "--ac-cell-bg-outside" in css_vars
    assert "--ac-tg-now" in css_vars


def test_snake_case_fields_map_to_kebab_case_css_vars() -> None:
    css_vars = Theme.light().to_css_vars()
    # header_fg -> --ac-header-fg, tg_now -> --ac-tg-now (the mapping is deterministic).
    assert "--ac-header-fg" in css_vars
    assert "--ac-more-fg" in css_vars
    assert "--ac-event-accent" in css_vars
    assert "--ac-tg-now" in css_vars
    # No stray underscores survived into a CSS variable name.
    assert not any("_" in name for name in css_vars)


def test_all_four_presets_exist_and_are_distinct() -> None:
    assert set(PRESET_NAMES) == {"light", "dark", "midnight", "high_contrast"}
    assert set(PRESETS) == set(PRESET_NAMES)
    # The two dark modes are genuinely dark backgrounds, distinct from light and from each other
    # (a real dark mode, not a recolored light one). high_contrast is a light theme by design and
    # may share the white surface with light — it is distinguished by its borders/accents.
    assert PRESETS["dark"].bg != PRESETS["light"].bg
    assert PRESETS["midnight"].bg != PRESETS["light"].bg
    assert PRESETS["dark"].bg != PRESETS["midnight"].bg


def test_dark_and_midnight_are_actually_dark() -> None:
    # A real dark mode: the surface is dark and the foreground is light (inverted vs light preset).
    for name in ("dark", "midnight"):
        theme = PRESETS[name]
        assert _luminance(theme.bg) < 0.2, f"{name}.bg should be dark"
        assert _luminance(theme.fg) > 0.6, f"{name}.fg should be light on a dark surface"


def test_high_contrast_maximizes_text_contrast() -> None:
    hc = PRESETS["high_contrast"]
    # Pure black text on a white surface is the maximum-contrast pairing.
    assert _contrast_ratio(hc.fg, hc.bg) >= 7.0


def test_every_preset_has_readable_body_text() -> None:
    # WCAG AA for normal text is >= 4.5:1 — hold every preset to it (fg on bg).
    for name, theme in PRESETS.items():
        assert _contrast_ratio(theme.fg, theme.bg) >= 4.5, f"{name} fails AA fg-on-bg"


def test_preset_lookup_matches_the_classmethods() -> None:
    assert Theme.preset("dark") == Theme.dark()
    assert Theme.preset("midnight") == Theme.midnight()
    assert Theme.preset("high_contrast") == Theme.high_contrast()


def test_unknown_preset_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown theme preset"):
        Theme.preset("neon")  # type: ignore[arg-type]


def test_theme_is_immutable() -> None:
    theme = Theme.light()
    with pytest.raises((TypeError, ValueError)):
        theme.bg = "#000000"  # type: ignore[misc]


def test_css_breaking_token_value_is_rejected() -> None:
    # A token value must not be able to smuggle in extra declarations/rules when serialized.
    fields = Theme.light().model_dump()
    fields["bg"] = "#fff; } body { display: none"
    with pytest.raises(ValueError):
        Theme(**fields)


def test_extra_fields_are_forbidden() -> None:
    fields = Theme.light().model_dump()
    fields["surprise"] = "#000000"
    with pytest.raises(ValueError):
        Theme(**fields)


def test_custom_theme_round_trips_through_css_vars() -> None:
    base = Theme.light().model_dump()
    base["event_accent"] = "#2b6cb0"
    theme = Theme(**base)
    assert theme.to_css_vars()["--ac-event-accent"] == "#2b6cb0"


# --- test-local color helpers (kept out of production; verifying, not shipping) -------------


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = (_srgb_to_linear(c) for c in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _luminance(hex_color: str) -> float:
    return _relative_luminance(hex_color)


def _contrast_ratio(fg: str, bg: str) -> float:
    lf, lb = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(lf, lb), min(lf, lb)
    return (lighter + 0.05) / (darker + 0.05)
