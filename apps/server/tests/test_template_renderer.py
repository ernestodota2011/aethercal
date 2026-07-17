"""The workflow-template renderer (RF-24): strict allow-list substitution, and the body is DATA.

Two independent properties are asserted here, and they are NOT the same property:

* **the allow-list governs WHICH variables exist** — an unknown ``{{name}}`` is a loud error, never
  a silently-empty hole and never an evaluated expression;
* **the escaping governs WHAT THEY MAY CONTAIN** — ``{{guest_name}}`` and the form answers come from
  the GUEST, and they are rendered into an email **to the host**, into WhatsApp/SMS, and into the
  admin panel. A guest who can smuggle markup, a link, or a fake signature block into that body
  phishes the host under this product's own branding.

The second is the one a "strict allow-list" lulls you into skipping. Most of the cases below are
hostile guest payloads, not template-syntax errors.
"""

from __future__ import annotations

import pytest

from aethercal.server.channels import Channel
from aethercal.server.services.templates import (
    ALLOWED_VARIABLES,
    MAX_VARIABLE_LENGTH,
    TemplateContext,
    TemplateError,
    UnknownVariableError,
    render_template,
    validate_template_body,
)


def _context(**overrides: str) -> TemplateContext:
    """A complete render context; every allow-listed variable has a value (see below)."""
    base = dict(
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        event_title="Discovery call",
        start_local="2026-07-20 10:00",
        end_local="2026-07-20 10:30",
        timezone="America/New_York",
        host_name="Grace Hopper",
        cancel_url="https://book.example.com/c/abc",
        reschedule_url="https://book.example.com/r/abc",
        meeting_url="https://meet.example.com/xyz",
    )
    base.update(overrides)
    return TemplateContext(**base)


# --------------------------------------------------------------------------------------
# The allow-list: which variables exist.
# --------------------------------------------------------------------------------------


def test_the_allowlist_is_exactly_the_documented_vocabulary() -> None:
    assert (
        frozenset(
            {
                "guest_name",
                "guest_email",
                "event_title",
                "start_local",
                "end_local",
                "timezone",
                "host_name",
                "cancel_url",
                "reschedule_url",
                "meeting_url",
            }
        )
        == ALLOWED_VARIABLES
    )


def test_a_context_must_supply_every_allowlisted_variable() -> None:
    """The CONTEXT is always complete; a VALUE may be empty.

    This is what stops a typo in the context builder from silently blanking a variable in every
    message a tenant ever sends — the hole would render as "" and nothing would say so."""
    assert set(_context().as_mapping()) == ALLOWED_VARIABLES


def test_it_substitutes_the_allowlisted_variables() -> None:
    rendered = render_template(
        "Hi {{guest_name}}, your {{event_title}} is at {{start_local}}.",
        subject=None,
        context=_context(),
        channel=Channel.SMS,
    )

    assert rendered.body == "Hi Ada Lovelace, your Discovery call is at 2026-07-20 10:00."


def test_it_tolerates_inner_whitespace_in_the_placeholder() -> None:
    rendered = render_template(
        "Hi {{ guest_name }}.", subject=None, context=_context(), channel=Channel.SMS
    )

    assert rendered.body == "Hi Ada Lovelace."


def test_an_unknown_variable_is_a_loud_error_not_an_empty_hole() -> None:
    """A silently-empty substitution is the project's disease: the message goes out with a hole and
    nothing reports it."""
    with pytest.raises(UnknownVariableError, match="account_balance"):
        render_template(
            "Hi {{account_balance}}", subject=None, context=_context(), channel=Channel.SMS
        )


def test_validate_reports_every_unknown_variable_at_authoring_time() -> None:
    """The admin CRUD validates a template when it is SAVED, so a tenant learns at authoring time
    rather than by a message that never arrives."""
    with pytest.raises(UnknownVariableError) as excinfo:
        validate_template_body("{{nope}} and {{also_nope}} and {{guest_name}}")

    message = str(excinfo.value)
    assert "nope" in message
    assert "also_nope" in message


def test_a_valid_body_passes_validation() -> None:
    assert validate_template_body("Hi {{guest_name}}, see you at {{start_local}}.") is None


# --------------------------------------------------------------------------------------
# The body is DATA, never instructions: no Jinja, no eval, no expression evaluation.
# --------------------------------------------------------------------------------------


def test_a_substituted_value_is_never_rescanned_for_placeholders() -> None:
    """The template-injection hole. A guest books as ``{{host_name}}``; a renderer that loops until
    no placeholders remain (or re-scans its own output) would expand it and leak the host's name —
    and, with a nastier payload, whatever else the context holds."""
    rendered = render_template(
        "Hi {{guest_name}}.",
        subject=None,
        context=_context(guest_name="{{host_name}}"),
        channel=Channel.SMS,
    )

    assert "Grace Hopper" not in rendered.body
    assert "{{host_name}}" in rendered.body


def test_the_no_rescan_property_holds_on_every_channel() -> None:
    """The same payload, per channel. WhatsApp's markup neutralisation eats the underscore (so the
    literal reads ``{{hostname}}``); what must NEVER change is that the host's name cannot leak."""
    for channel in Channel:
        rendered = render_template(
            "Hi {{guest_name}}.",
            subject=None,
            context=_context(guest_name="{{host_name}}"),
            channel=channel,
        )
        assert "Grace Hopper" not in rendered.body, channel


def test_jinja_syntax_is_inert_text_not_a_control_structure() -> None:
    body = "{% for x in range(10) %}spam{% endfor %}{{guest_name}}"

    rendered = render_template(body, subject=None, context=_context(), channel=Channel.SMS)

    assert rendered.body == "{% for x in range(10) %}spam{% endfor %}Ada Lovelace"


def test_an_expression_is_not_evaluated_it_is_an_unknown_variable() -> None:
    for hostile in ("{{1+1}}", "{{__import__('os').system('id')}}", "{{config.items()}}"):
        with pytest.raises(TemplateError):
            render_template(hostile, subject=None, context=_context(), channel=Channel.SMS)


# --------------------------------------------------------------------------------------
# Escaping per channel: what the variables may CONTAIN.
# --------------------------------------------------------------------------------------


def test_email_html_escapes_a_guest_supplied_value() -> None:
    """An email template body is HTML-capable, so a guest-supplied value is HTML-escaped — otherwise
    the guest injects markup into the mail the HOST reads."""
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name="<script>alert(1)</script>"),
        channel=Channel.EMAIL,
    )

    assert "<script>" not in rendered.body
    assert "&lt;script&gt;" in rendered.body


def test_whatsapp_neutralises_its_own_markup_in_a_guest_supplied_value() -> None:
    """WhatsApp renders ``*bold*``/``_italic_``/``~strike~``/```mono```. A guest whose "name" is
    markup can forge emphasis — and a convincing fake system notice."""
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name="*URGENT* _verify_ ~now~ ```sudo```"),
        channel=Channel.WHATSAPP,
    )

    for marker in ("*", "_", "~", "`"):
        assert marker not in rendered.body


def test_a_url_in_a_free_text_guest_field_is_defanged() -> None:
    """THE phishing vector. A guest books under the name "Click https://evil.example to confirm";
    the host receives it, in a message their own booking product sent them."""
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name="Click https://evil.example/login to confirm"),
        channel=Channel.WHATSAPP,
    )

    assert "https://evil.example/login" not in rendered.body
    assert "evil" in rendered.body  # defanged, not deleted — the host still sees what was sent


def test_the_products_own_urls_stay_live_links() -> None:
    """The defanging must not eat the links the message exists to deliver."""
    rendered = render_template(
        "Cancel: {{cancel_url}} · Join: {{meeting_url}}",
        subject=None,
        context=_context(),
        channel=Channel.WHATSAPP,
    )

    assert "https://book.example.com/c/abc" in rendered.body
    assert "https://meet.example.com/xyz" in rendered.body


def test_a_guest_cannot_smuggle_a_scheme_relative_or_bare_domain_link() -> None:
    for hostile in ("//evil.example/x", "www.evil.example", "evil.example/login"):
        rendered = render_template(
            "Hi {{guest_name}}",
            subject=None,
            context=_context(guest_name=hostile),
            channel=Channel.SMS,
        )
        assert hostile not in rendered.body, hostile


def test_newlines_in_a_guest_value_cannot_forge_a_message_block() -> None:
    """An injected newline lets a guest append a fake signature or a fake "system" line below the
    real body. A VALUE is a single line; only the TEMPLATE may lay out the message."""
    rendered = render_template(
        "Hi {{guest_name}}. See you then.",
        subject=None,
        context=_context(guest_name="Ada\n\n-- AetherCal Security: reset your password at"),
        channel=Channel.SMS,
    )

    assert "\n" not in rendered.body


#: The invisible code points, written as ESCAPES and never as literals. A test that pastes a raw
#: RIGHT-TO-LEFT OVERRIDE into its own source is the very hazard it is testing for: the source then
#: displays differently from what it says, and every reviewer downstream reads the lie.
_RLO = "\u202e"  # RIGHT-TO-LEFT OVERRIDE: reverses how the rest of the line reads
_ZWSP = "\u200b"  # ZERO WIDTH SPACE: splits a word for a human, not for a client
_LRI = "\u2066"  # LEFT-TO-RIGHT ISOLATE


def test_bidi_and_zero_width_characters_are_stripped() -> None:
    """A right-to-left override reverses how the rest of the line DISPLAYS — the classic filename /
    address spoof. Zero-width joiners hide text from a human but not from a client."""
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name=f"Ada{_RLO}gro.live{_ZWSP}{_LRI}x"),
        channel=Channel.SMS,
    )

    for hidden in (_RLO, _ZWSP, _LRI):
        assert hidden not in rendered.body


def test_control_characters_are_stripped() -> None:
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name="Ada\x00\x07\x1b[31m"),
        channel=Channel.SMS,
    )

    for control in ("\x00", "\x07", "\x1b"):
        assert control not in rendered.body


def test_each_variable_is_length_capped() -> None:
    """A cap per VARIABLE, not merely per message: an unbounded name is a cheap way to push the real
    content out of an SMS segment budget (and to run up the bill)."""
    rendered = render_template(
        "Hi {{guest_name}}",
        subject=None,
        context=_context(guest_name="A" * 5_000),
        channel=Channel.SMS,
    )

    assert len(rendered.body) < MAX_VARIABLE_LENGTH + 50


def test_the_subject_is_rendered_with_the_same_rules() -> None:
    """The subject is a body too — an unescaped one is a header-injection and a spoof surface."""
    rendered = render_template(
        "hello",
        subject="Booking for {{guest_name}}",
        context=_context(guest_name="Ada\r\nBcc: victim@example.com"),
        channel=Channel.EMAIL,
    )

    assert rendered.subject is not None
    assert "\r" not in rendered.subject
    assert "\n" not in rendered.subject


def test_an_empty_value_renders_empty_rather_than_raising() -> None:
    """An in-person booking has no meeting URL. That is a legitimately empty VALUE, not a missing
    variable — the distinction the complete-context rule exists to preserve."""
    rendered = render_template(
        "Join: {{meeting_url}}",
        subject=None,
        context=_context(meeting_url=""),
        channel=Channel.SMS,
    )

    assert rendered.body == "Join: "
