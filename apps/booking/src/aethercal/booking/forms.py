"""Parse configured questions and validate the guest booking form (RF-07).

Two jobs, both pure (no network, no FastHTML): read an event type's free-form ``questions`` JSON
into a typed :class:`QuestionSpec` list the view can render, and turn a submitted form mapping into
either a validated :class:`BookingCreate` (the SDK's request DTO) or a list of localized field
errors the view re-renders inline. Because ``questions`` is stored as arbitrary JSON,
:func:`parse_questions` is defensive: unknown shapes degrade to a plain text input and junk entries
are dropped.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from aethercal.booking.i18n import Locale, t
from aethercal.schemas.bookings import normalize_phone
from aethercal.schemas.public import PublicBookingCreate

#: The guest's phone input, and the consent checkbox beside it (RF-24).
PHONE_FIELD_NAME = "phone"
PHONE_CONSENT_FIELD_NAME = "phone_consent"

#: The field the Turnstile widget writes its response into. ==The name is Cloudflare's, not ours==:
#: the script injects a hidden input called exactly this. It must match, or the token is never
#: submitted and every public booking is refused with a captcha error that names no cause.
TURNSTILE_FIELD_NAME = "cf-turnstile-response"

#: The ``value`` the consent checkbox carries, and therefore what a TICKED box submits. An unticked
#: box submits nothing at all — the key is simply absent from the payload.
CONSENT_SUBMITTED_VALUE = "on"

#: What counts as a tick. ``on`` is what the checkbox above actually sends; the rest are accepted so
#: that a template which one day sets an explicit ``value="true"`` cannot silently stop registering
#: consent. Consent is read as "is the value one of THESE", never as "is the key present" (a bot or
#: a crafted POST can send `phone_consent=` empty) and never as "is it truthy".
_TICKED_VALUES = frozenset({CONSENT_SUBMITTED_VALUE, "true", "1", "yes"})


def is_consent_ticked(value: str | None) -> bool:
    """Whether the guest actually ticked the consent box. Absent, empty, or junk = NOT consent.

    Shared deliberately with ``views``: the view renders ``checked`` from this, and the parser
    reads consent from this. If the two ever drifted apart, a box could render ticked and still not
    register as consent — the guest would see agreement on screen that never reached the column.
    """
    return value is not None and value.strip().lower() in _TICKED_VALUES


_QUESTION_FIELD_PREFIX = "q_"
_KNOWN_KINDS = frozenset({"text", "textarea", "select", "email", "tel", "url", "number"})
# Characters a phone answer may contain besides digits (formatting only).
_TEL_ALLOWED = frozenset("0123456789 +-().")
# One RFC 1123 hostname label: 1-63 alphanumerics/hyphens, never leading/trailing a hyphen.
_HOSTNAME_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """A configured intake question, normalized for rendering and answer collection."""

    key: str
    label: str
    kind: str
    required: bool
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FieldError:
    """A localized validation message bound to a form field name."""

    field: str
    message: str


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """The request context a submitted form is validated against (when, whose zone, which language).

    ==No ``event_type_id`` any more, and its absence is the point.== The booking now goes to the
    PUBLIC route, which names the appointment in its PATH — ``/public/{tenant_slug}/{event_slug}/
    bookings``. A body field naming the event type beside a path that already names it would be two
    sources of truth for one fact, and the one that eventually won would decide whose diary a
    guest's
    booking landed in.
    """

    start_iso: str
    guest_timezone: str
    locale: Locale


@dataclass(frozen=True, slots=True)
class BookingFormResult:
    """The outcome of validating a booking form: a ``booking`` XOR a list of ``errors``."""

    booking: PublicBookingCreate | None
    errors: list[FieldError]
    values: dict[str, str] = field(default_factory=dict)


def question_field_name(key: str) -> str:
    """The form input name for a question ``key`` (namespaced so it can't clash with name/email)."""
    return f"{_QUESTION_FIELD_PREFIX}{key}"


def _slugify(value: str) -> str:
    cleaned = [char if char.isalnum() else "_" for char in value.strip().lower()]
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _coerce_question(raw: Any, index: int) -> QuestionSpec | None:
    """Normalize one raw ``questions`` entry into a :class:`QuestionSpec` (``None`` if unusable)."""
    if isinstance(raw, str):
        label = raw.strip()
        if not label:
            return None
        key = _slugify(label) or f"q{index}"
        return QuestionSpec(key=key, label=label, kind="text", required=False, options=())
    if not isinstance(raw, Mapping):
        return None

    identifier = raw.get("key") or raw.get("id") or raw.get("name")
    label_value = raw.get("label") or raw.get("text") or raw.get("question")
    label = str(label_value).strip() if label_value is not None else ""
    key = str(identifier).strip() if identifier else _slugify(label)
    if not key:
        key = f"q{index}"
    if not label:
        label = key

    kind_raw = str(raw.get("type", "text")).strip().lower()
    kind = kind_raw if kind_raw in _KNOWN_KINDS else "text"
    required = bool(raw.get("required", False))
    options_raw = raw.get("options")
    options = (
        tuple(str(option) for option in options_raw)
        if isinstance(options_raw, Sequence) and not isinstance(options_raw, str | bytes)
        else ()
    )
    # A select with no options is misconfigured: degrade it to a plain text input (same defensive
    # philosophy as unknown kinds) so it collects a free answer instead of being a phantom
    # "select" that would accept any crafted value.
    if kind == "select" and not options:
        kind = "text"
    return QuestionSpec(key=key, label=label, kind=kind, required=required, options=options)


def parse_questions(raw_questions: Sequence[Any]) -> list[QuestionSpec]:
    """Normalize an event type's free-form ``questions`` into typed specs (junk entries dropped)."""
    specs: list[QuestionSpec] = []
    for index, raw in enumerate(raw_questions):
        spec = _coerce_question(raw, index)
        if spec is not None:
            specs.append(spec)
    return specs


def _looks_like_email(value: str) -> bool:
    """A light structural email check (single ``@``, non-empty local/domain, no spaces)."""
    candidate = value.strip()
    local, _, domain = candidate.partition("@")
    return bool(local) and bool(domain) and " " not in candidate and candidate.count("@") == 1


def _looks_like_number(value: str) -> bool:
    """A finite decimal number (rejects text, ``nan``/``inf``, and empty input)."""
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _valid_hostname(host: str) -> bool:
    """A multi-label RFC 1123 hostname (an optional trailing FQDN dot allowed).

    Requiring >=2 non-empty, well-formed labels rejects the malformed hosts a bare ``"." in host``
    check waves through: a leading dot, consecutive dots, a trailing-only dot, or empty labels.
    """
    trimmed = host[:-1] if host.endswith(".") else host  # tolerate a trailing FQDN root dot
    labels = trimmed.split(".")
    return len(labels) >= 2 and all(_HOSTNAME_LABEL.match(label) for label in labels)


def _looks_like_url(value: str) -> bool:
    """An http(s) URL with a valid multi-label host and port — rejects free text and bad hosts.

    A bare ``bool(netloc)`` check waves through whitespace, ``https://word``, an invalid ``:port``,
    and malformed hosts (leading/consecutive/trailing dots); this validates all of those.
    """
    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return False
    try:
        parsed = urlparse(candidate)
        has_valid_port = parsed.port is None or parsed.port >= 0  # accessing .port validates it
    except ValueError:
        return False
    host = parsed.hostname
    return (
        has_valid_port
        and parsed.scheme in ("http", "https")
        and host is not None
        and _valid_hostname(host)
    )


def _looks_like_tel(value: str) -> bool:
    """A phone-ish answer: at least three digits and only digit/formatting characters."""
    candidate = value.strip()
    digits = sum(char.isdigit() for char in candidate)
    return digits >= 3 and all(char in _TEL_ALLOWED for char in candidate)


# Per-kind answer validators. Only the ``select`` kind is special-cased (it checks membership in
# the configured options), so it is handled directly in ``_question_error`` rather than here.
_ANSWER_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "email": _looks_like_email,
    "number": _looks_like_number,
    "url": _looks_like_url,
    "tel": _looks_like_tel,
}
_ANSWER_ERROR_KEY: dict[str, str] = {
    "email": "error_question_email",
    "number": "error_question_number",
    "url": "error_question_url",
    "tel": "error_question_tel",
}


def _question_error(spec: QuestionSpec, answer: str, locale: Locale) -> str | None:
    """Localized error for a non-empty ``answer`` that violates its question kind, else ``None``.

    Server-side is the source of truth (RF-07): the browser's ``<select>``/``type=email`` hints
    help, but a JS-less or crafted POST is still validated here before any booking is created.
    """
    if spec.kind == "select":
        if spec.options and answer not in spec.options:
            return t(locale, "error_question_select")
        return None
    validator = _ANSWER_VALIDATORS.get(spec.kind)
    if validator is not None and not validator(answer):
        return t(locale, _ANSWER_ERROR_KEY[spec.kind])
    return None


def _collect_values(form: Mapping[str, str], questions: Sequence[QuestionSpec]) -> dict[str, str]:
    values = {
        "name": form.get("name", ""),
        "email": form.get("email", ""),
        "notes": form.get("notes", ""),
        # Echoed back so a re-rendered form keeps what the guest typed and ticked. The consent tick
        # is re-rendered from the guest's OWN answer — never re-checked by us — so an error on some
        # other field can never quietly turn an unticked box into a consent.
        PHONE_FIELD_NAME: form.get(PHONE_FIELD_NAME, ""),
        PHONE_CONSENT_FIELD_NAME: form.get(PHONE_CONSENT_FIELD_NAME, ""),
    }
    for spec in questions:
        field_name = question_field_name(spec.key)
        values[field_name] = form.get(field_name, "")
    return values


def _phone_and_consent(
    form: Mapping[str, str], locale: Locale, *, collects_phone: bool
) -> tuple[str | None, bool, FieldError | None]:
    """Resolve the guest's phone + consent, or the single field error explaining why we cannot.

    ``collects_phone`` is the gate. When this event type has no active WhatsApp/SMS rule the field
    was never rendered, so ANY phone in the payload is unsolicited — a crafted POST, or a page
    cached before the tenant switched the rule off. It is dropped, completely. We do not raise an
    error over it: the guest did nothing wrong, and a public form must not report on the tenant's
    rule configuration. We simply never take the data (RNF-8).
    """
    if not collects_phone:
        return None, False, None

    consent = is_consent_ticked(form.get(PHONE_CONSENT_FIELD_NAME))
    raw = form.get(PHONE_FIELD_NAME, "").strip()
    if not raw:
        if consent:
            # They ticked "message me" and gave nothing to message. Refusing here is the only
            # honest move: dropping the tick would discard a consent they DID give, and keeping it
            # would stamp a consent that points at no number.
            return (
                None,
                False,
                FieldError(PHONE_FIELD_NAME, t(locale, "error_phone_consent_without_number")),
            )
        return None, False, None

    phone = normalize_phone(raw)
    if phone is None:
        return None, False, FieldError(PHONE_FIELD_NAME, t(locale, "error_phone_invalid"))
    return phone, consent, None


def build_booking(
    request: BookingRequest,
    *,
    questions: Sequence[QuestionSpec],
    form: Mapping[str, str],
    collects_phone: bool = False,
) -> BookingFormResult:
    """Validate a submitted form into a :class:`PublicBookingCreate` or localized field errors.

    ``collects_phone`` mirrors the event type's own flag: the phone + consent are read from the
    payload ONLY when an active WhatsApp/SMS rule governs this event type. It defaults to ``False``
    — the safe direction — so a caller that forgets to pass it collects no personal data rather than
    harvesting numbers nothing will ever send to.

    The captcha's response is carried through UNVALIDATED, and deliberately: it is opaque to us, and
    the only thing entitled to judge it is Cloudflare, server-side. An absent one is not a field
    error either — the API answers that with its own ``403 captcha_required``, which is the same
    answer a token that FAILED gets. Telling a bot which field to start guessing at is not a
    kindness we owe it.
    """
    locale = request.locale
    values = _collect_values(form, questions)
    errors: list[FieldError] = []

    name = form.get("name", "").strip()
    if not name:
        errors.append(FieldError("name", t(locale, "error_name_required")))

    email = form.get("email", "").strip()
    if not _looks_like_email(email):
        errors.append(FieldError("email", t(locale, "error_email_invalid")))

    phone, phone_consent, phone_error = _phone_and_consent(
        form, locale, collects_phone=collects_phone
    )
    if phone_error is not None:
        errors.append(phone_error)

    start: datetime | None = None
    try:
        start = datetime.fromisoformat(request.start_iso)
    except ValueError:
        errors.append(FieldError("start", t(locale, "error_start_invalid")))

    answers: dict[str, Any] = {}
    for spec in questions:
        field_name = question_field_name(spec.key)
        answer = form.get(field_name, "").strip()
        if not answer:
            if spec.required:
                errors.append(FieldError(field_name, t(locale, "error_question_required")))
            continue
        type_error = _question_error(spec, answer, locale)
        if type_error is not None:
            errors.append(FieldError(field_name, type_error))
            continue
        answers[spec.key] = answer

    notes = form.get("notes", "").strip() or None

    if errors or start is None:
        return BookingFormResult(booking=None, errors=errors, values=values)

    try:
        booking = PublicBookingCreate(
            start=start,
            guest_name=name,
            guest_email=email,
            guest_timezone=request.guest_timezone,
            guest_notes=notes,
            answers=answers,
            locale=locale,
            # The consent travels with the booking to the column. If it stopped here, the box would
            # be decorative and the guest could never prove — nor we evidence — that they agreed.
            guest_phone=phone,
            guest_phone_consent=phone_consent,
            turnstile_token=form.get(TURNSTILE_FIELD_NAME, "").strip() or None,
        )
    except ValidationError:
        # Defensive: our own checks precede this, so a residual failure (e.g. an unexpected
        # timezone) is surfaced as a generic, non-leaking form error rather than a stack trace.
        errors.append(FieldError("form", t(locale, "error_form_has_issues")))
        return BookingFormResult(booking=None, errors=errors, values=values)

    return BookingFormResult(booking=booking, errors=[], values=values)


__all__ = [
    "CONSENT_SUBMITTED_VALUE",
    "PHONE_CONSENT_FIELD_NAME",
    "PHONE_FIELD_NAME",
    "TURNSTILE_FIELD_NAME",
    "BookingFormResult",
    "BookingRequest",
    "FieldError",
    "QuestionSpec",
    "build_booking",
    "is_consent_ticked",
    "parse_questions",
    "question_field_name",
]
