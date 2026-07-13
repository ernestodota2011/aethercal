"""Calendar color theme — the single source of truth for the ``--ac-*`` color tokens (F2-E, §7).

``Theme`` is a Pydantic model whose fields are the themeable color tokens of the calendar; four
named presets ship with the component: ``light`` (the neutral-premium default), a real ``dark``,
a deeper ``midnight``, and a maximum-contrast ``high_contrast``. The palette is DELIBERATELY
brand-neutral — grayscale surfaces with a restrained slate accent, no lavender/violet/cyan accents
and no glows (the #1 AI-slop tell, ``reference_anti_ai_slop_doctrina``). The AetherLogik brand is
applied by passing a custom ``Theme`` to the instance, not by hardcoding it into the OSS component.

This model is the origin of the token VALUES for BOTH languages: ``scripts/gen_theme_presets.py``
serializes ``PRESETS`` to ``js/packages/react/src/theme-presets.json``, which the TypeScript theming
layer imports. A Python drift test keeps the committed JSON current, and a vitest test locks the TS
presets against it — the same generate-and-lock contract as ``calendar-props.schema.json`` — so the
Python and TypeScript sides can never silently diverge. Only STRUCTURAL tokens (font, radii, grid
sizes) live TS-side; they are layout, not color, and are constant across every preset.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, field_validator

ThemePresetName = Literal["light", "dark", "midnight", "high_contrast"]

#: The preset names, in a stable order (used by the generator and the docs).
PRESET_NAMES: tuple[ThemePresetName, ...] = get_args(ThemePresetName)

# Characters that could break out of a `--token: value;` declaration (or an inline style attribute)
# when the value is serialized into CSS. A themeable color value never legitimately contains these.
_UNSAFE_TOKEN_CHARS = frozenset(";{}<>")


class Theme(BaseModel):
    """A calendar color theme: themeable ``--ac-*`` tokens, serialized to CSS custom properties.

    Field names are snake_case and map 1:1 to kebab-case CSS variables under the ``--ac-`` prefix
    (``header_fg`` -> ``--ac-header-fg``, ``tg_now`` -> ``--ac-tg-now``). The model is frozen and
    forbids extra fields, so a theme is an immutable, exhaustively-specified token set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fg: str
    """Primary foreground / body text."""
    muted: str
    """Secondary text (event times, day numbers)."""
    faint: str
    """Tertiary text (out-of-month day numbers, hour axis)."""
    bg: str
    """The calendar's outer surface."""
    header_fg: str
    """Weekday / column-header text."""
    border: str
    """Grid lines and the outer border."""
    cell_bg: str
    """A day cell / time column background."""
    cell_bg_outside: str
    """An out-of-month day cell background (month view)."""
    today_marker_bg: str
    """The 'today' pill background."""
    today_marker_fg: str
    """The 'today' pill text."""
    event_bg: str
    """An event chip / time block fill."""
    event_fg: str
    """An event chip / time block text."""
    event_accent: str
    """The event's left accent bar (a per-event ``color`` overrides it)."""
    more_fg: str
    """The '+N more' overflow control text (month view)."""
    focus: str
    """Focus ring / drop-target / keyboard-active affordance."""
    rollback: str
    """The rejected-mutation rollback flash (optimistic reconciliation)."""
    tg_now: str
    """The 'now' line on the week/day time grid."""

    @field_validator("*")
    @classmethod
    def _token_value_is_css_safe(cls, value: str) -> str:
        """Reject an empty or CSS-breaking token value (defense against style injection)."""
        if not value or not value.strip():
            msg = "theme token value must be a non-empty string"
            raise ValueError(msg)
        if _UNSAFE_TOKEN_CHARS.intersection(value):
            msg = f"theme token value contains an unsafe CSS character: {value!r}"
            raise ValueError(msg)
        return value

    def to_css_vars(self) -> dict[str, str]:
        """Return this theme as ``{ "--ac-<kebab-field>": value }``, in field-declaration order."""
        return {
            f"--ac-{name.replace('_', '-')}": getattr(self, name)
            for name in type(self).model_fields
        }

    # --- Presets ---------------------------------------------------------------------------------
    # The palette is neutral-premium grayscale with a slate accent. Dark modes are genuinely dark
    # (dark surface + light text), not a re-tinted light theme.

    @classmethod
    def light(cls) -> Theme:
        """Neutral-premium default: grayscale surfaces, near-black today marker, slate accent."""
        return cls(
            fg="#1f2328",
            # Secondary/tertiary text darkened to clear WCAG AA (>=4.5:1) on the light surfaces they
            # render on: muted (event times, day numbers) is worst-case on the event-chip fill
            # (--ac-event-bg #eef1f4); faint (out-of-month numbers, axis, legend) is worst-case on
            # the out-of-month cell (--ac-cell-bg-outside #fafafa). Neutral slate, no lavender/cyan,
            # and muted stays darker than faint (secondary vs tertiary).
            muted="#5f6672",
            faint="#676e79",
            bg="#ffffff",
            header_fg="#4b5563",
            border="#e5e7eb",
            cell_bg="#ffffff",
            cell_bg_outside="#fafafa",
            today_marker_bg="#111827",
            today_marker_fg="#ffffff",
            event_bg="#eef1f4",
            event_fg="#1f2328",
            event_accent="#64748b",
            more_fg="#4b5563",
            focus="#2563eb",
            rollback="#b91c1c",
            tg_now="#dc2626",
        )

    @classmethod
    def dark(cls) -> Theme:
        """A real dark mode: neutral near-black surfaces, light text, restrained slate accent."""
        return cls(
            fg="#e6e8eb",
            muted="#9aa1ab",
            # Tertiary text (hour axis, "all day" rowheader, out-of-month numbers,
            # demo legends/footer). Lightened from #6b7280 (3.6:1, < AA) to clear WCAG
            # AA (>= 4.5:1) on every dark surface it renders on — the same AA bar the
            # light preset already holds muted/faint to. Stays a neutral cool slate
            # (no cyan/violet) and dimmer than `muted`, preserving the tertiary hierarchy.
            faint="#868e99",
            bg="#14161a",
            header_fg="#b3b9c2",
            border="#2a2e35",
            cell_bg="#171a1f",
            cell_bg_outside="#111318",
            today_marker_bg="#e6e8eb",
            today_marker_fg="#14161a",
            event_bg="#242a32",
            event_fg="#e6e8eb",
            event_accent="#8b98a9",
            more_fg="#b3b9c2",
            focus="#6ea8fe",
            rollback="#f87171",
            tg_now="#f87171",
        )

    @classmethod
    def midnight(cls) -> Theme:
        """A deeper dark mode: cooler, darker neutral slate — still no violet/cyan, no glow."""
        return cls(
            fg="#dfe4ea",
            muted="#8b95a1",
            # Lightened from #5b6675 (3.2:1, < AA) to clear WCAG AA on midnight's darker
            # surfaces — same rationale as the dark preset's `faint` (neutral cool slate,
            # still dimmer than muted).
            faint="#828a95",
            bg="#0b0f14",
            header_fg="#a7b0bd",
            border="#1c232c",
            cell_bg="#0e131a",
            cell_bg_outside="#090d12",
            today_marker_bg="#dfe4ea",
            today_marker_fg="#0b0f14",
            event_bg="#17212c",
            event_fg="#dfe4ea",
            event_accent="#7f8ea3",
            more_fg="#a7b0bd",
            focus="#74a9ff",
            rollback="#fb7185",
            tg_now="#fb7185",
        )

    @classmethod
    def high_contrast(cls) -> Theme:
        """Maximum-contrast light theme: black text on white, black grid lines, strong focus."""
        return cls(
            fg="#000000",
            muted="#000000",
            faint="#1a1a1a",
            bg="#ffffff",
            header_fg="#000000",
            border="#000000",
            cell_bg="#ffffff",
            cell_bg_outside="#ffffff",
            today_marker_bg="#000000",
            today_marker_fg="#ffffff",
            event_bg="#e0e0e0",
            event_fg="#000000",
            event_accent="#000000",
            more_fg="#000000",
            focus="#0033cc",
            rollback="#b00000",
            tg_now="#d00000",
        )

    @classmethod
    def preset(cls, name: ThemePresetName) -> Theme:
        """Return a preset by name. Raises ``ValueError`` for an unknown name."""
        try:
            return PRESETS[name]
        except KeyError:
            valid = ", ".join(PRESET_NAMES)
            msg = f"unknown theme preset {name!r}; expected one of: {valid}"
            raise ValueError(msg) from None


#: The four shipped presets, keyed by name — the source of truth serialized to theme-presets.json.
PRESETS: dict[ThemePresetName, Theme] = {
    "light": Theme.light(),
    "dark": Theme.dark(),
    "midnight": Theme.midnight(),
    "high_contrast": Theme.high_contrast(),
}
