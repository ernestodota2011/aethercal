"""The branding contract: what a colour IS, what a logo URL IS, and which name a guest reads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethercal.schemas.branding import (
    TenantBrandingRead,
    TenantBrandingUpdate,
    require_accent_color,
    require_logo_url,
    resolve_display_name,
)


class TestAccentColor:
    @pytest.mark.parametrize("value", ["#e0894b", "#E0894B", "#abc", "#ABC"])
    def test_a_hex_triplet_is_accepted(self, value: str) -> None:
        assert require_accent_color(value) == value

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert require_accent_color("  #e0894b  ") == "#e0894b"

    @pytest.mark.parametrize(
        "value",
        [
            "red",  # a keyword: valid CSS, but not a colour we can bound
            "rgb(1,2,3)",  # a function: parentheses inside a declaration
            "#e0894",  # five digits: neither #rgb nor #rrggbb
            "#gggggg",  # not hex
            "e0894b",  # no leading '#'
            "",
        ],
    )
    def test_anything_that_is_not_a_hex_triplet_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="accent colour"):
            require_accent_color(value)

    @pytest.mark.parametrize(
        "value",
        [
            "#fff; } body { display: none } .x {",  # closes the declaration and the rule
            "#fff</style><script>alert(1)</script>",  # closes the element
            "#fff;background:url(https://evil.example/x)",  # a second declaration
        ],
    )
    def test_a_css_injection_payload_is_refused(self, value: str) -> None:
        """==The belt is the FORMAT, not an escape.==

        ``accent_color`` is interpolated into a ``<style>`` block as ``--accent: <value>``. A value
        carrying ``;``, ``}`` or ``<`` can close the declaration, the rule, or the element itself.
        Escaping is the wrong answer (there is no single escaping that is right inside a CSS
        declaration AND inside an HTML element), so the value is constrained to a shape in which
        none of those characters can appear at all.
        """
        with pytest.raises(ValueError, match="accent colour"):
            require_accent_color(value)


class TestLogoUrl:
    def test_an_https_url_is_accepted(self) -> None:
        assert require_logo_url("https://cdn.example.com/logo.png") == (
            "https://cdn.example.com/logo.png"
        )

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert require_logo_url(" https://cdn.example.com/logo.png ") == (
            "https://cdn.example.com/logo.png"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "http://cdn.example.com/logo.png",  # cleartext: mixed content on an https page
            "javascript:alert(1)",
            "data:image/svg+xml;base64,PHN2Zy8+",  # an SVG data URL is a script vector
            "//cdn.example.com/logo.png",  # protocol-relative: inherits http on an http page
            "/static/logo.png",  # relative: no scheme at all
            "https://",  # no host
            "ftp://cdn.example.com/logo.png",
            "",
        ],
    )
    def test_anything_that_is_not_an_https_url_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="logo url"):
            require_logo_url(value)

    def test_credentials_in_the_url_are_refused(self) -> None:
        """A logo URL is rendered into a PUBLIC page. Credentials in it would be published."""
        with pytest.raises(ValueError, match="logo url"):
            require_logo_url("https://user:secret@cdn.example.com/logo.png")


class TestResolveDisplayName:
    def test_the_public_name_wins_when_it_is_set(self) -> None:
        assert resolve_display_name("Clinica Sol", "Sol Holdings LLC") == "Clinica Sol"

    @pytest.mark.parametrize("public_name", [None, "", "   "])
    def test_it_falls_back_to_the_legal_name(self, public_name: str | None) -> None:
        """A blank ``public_name`` is *no* public name — it is what an admin form submits for
        "I left this alone", and a booking page headed by an empty string is a broken page."""
        assert resolve_display_name(public_name, "Sol Holdings LLC") == "Sol Holdings LLC"


class TestWireModels:
    def test_the_read_model_carries_the_resolved_name(self) -> None:
        brand = TenantBrandingRead(
            display_name="Clinica Sol",
            logo_url="https://cdn.example.com/sol.png",
            accent_color="#e0894b",
            timezone="America/New_York",
        )
        assert brand.display_name == "Clinica Sol"

    def test_the_read_model_needs_no_logo_no_colour(self) -> None:
        brand = TenantBrandingRead(display_name="Sol", timezone="UTC")
        assert brand.logo_url is None
        assert brand.accent_color is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("accent_color", "#fff; } body { display: none } .x {"),
            ("logo_url", "javascript:alert(1)"),
            ("timezone", "America/Mars"),
        ],
    )
    def test_the_READ_model_validates_too_not_only_the_write_model(
        self, field: str, value: str
    ) -> None:
        """==The rule is enforced at BOTH edges, and the second one is not paranoia.==

        The booking page is a separate process. It does not read the database — it parses whatever
        the API's HTTP response says, into this model, and interpolates ``accent_color`` straight
        into a ``<style>`` block. If the rule lived only on the write path, then a row written by
        any means that is not the admin — the runbook's ``psql``, a restored dump, a future service
        — would sail through the API and into that f-string.

        Validating here means the page's existing "the API answered something I can't use" branch
        catches it, and the guest gets an unbranded page instead of an injected one.
        """
        payload: dict[str, str] = {
            "display_name": "Sol",
            "timezone": "UTC",
            field: value,
        }
        with pytest.raises(ValidationError):
            TenantBrandingRead(**payload)

    def test_the_update_model_validates_every_field(self) -> None:
        update = TenantBrandingUpdate(
            public_name="Clinica Sol",
            logo_url="https://cdn.example.com/sol.png",
            accent_color="#E0894B",
            timezone="America/New_York",
        )
        assert update.public_name == "Clinica Sol"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("accent_color", "red"),
            ("logo_url", "http://cdn.example.com/logo.png"),
            ("timezone", "America"),  # a tz-database DIRECTORY, not a zone
            ("timezone", "America/Mars"),
        ],
    )
    def test_the_update_model_refuses_a_bad_value_at_the_edge(self, field: str, value: str) -> None:
        payload: dict[str, str] = {"timezone": "UTC", field: value}
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(**payload)

    def test_blank_optional_fields_are_normalized_to_none(self) -> None:
        """The admin form submits "" for a field the operator cleared. "" is not a logo."""
        update = TenantBrandingUpdate(public_name="", logo_url="", accent_color="", timezone="UTC")
        assert update.public_name is None
        assert update.logo_url is None
        assert update.accent_color is None

    def test_the_timezone_is_required(self) -> None:
        """Unlike the other three, a business ALWAYS has a timezone — the column is NOT NULL."""
        with pytest.raises(ValidationError):
            TenantBrandingUpdate(public_name="Sol")  # type: ignore[call-arg]
