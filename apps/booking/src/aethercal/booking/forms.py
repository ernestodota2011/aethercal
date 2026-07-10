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
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from aethercal.booking.i18n import Locale, t
from aethercal.schemas.bookings import BookingCreate

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
    """The request context a submitted form is validated against (which event, when, whose zone)."""

    event_type_id: uuid.UUID
    start_iso: str
    guest_timezone: str
    locale: Locale


@dataclass(frozen=True, slots=True)
class BookingFormResult:
    """The outcome of validating a booking form: a ``booking`` XOR a list of ``errors``."""

    booking: BookingCreate | None
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
    }
    for spec in questions:
        field_name = question_field_name(spec.key)
        values[field_name] = form.get(field_name, "")
    return values


def build_booking(
    request: BookingRequest,
    *,
    questions: Sequence[QuestionSpec],
    form: Mapping[str, str],
) -> BookingFormResult:
    """Validate a submitted booking form into a :class:`BookingCreate` or localized field errors."""
    locale = request.locale
    values = _collect_values(form, questions)
    errors: list[FieldError] = []

    name = form.get("name", "").strip()
    if not name:
        errors.append(FieldError("name", t(locale, "error_name_required")))

    email = form.get("email", "").strip()
    if not _looks_like_email(email):
        errors.append(FieldError("email", t(locale, "error_email_invalid")))

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
        booking = BookingCreate(
            event_type_id=request.event_type_id,
            start=start,
            guest_name=name,
            guest_email=email,
            guest_timezone=request.guest_timezone,
            guest_notes=notes,
            answers=answers,
            locale=locale,
        )
    except ValidationError:
        # Defensive: our own checks precede this, so a residual failure (e.g. an unexpected
        # timezone) is surfaced as a generic, non-leaking form error rather than a stack trace.
        errors.append(FieldError("form", t(locale, "error_form_has_issues")))
        return BookingFormResult(booking=None, errors=errors, values=values)

    return BookingFormResult(booking=booking, errors=[], values=values)


__all__ = [
    "BookingFormResult",
    "BookingRequest",
    "FieldError",
    "QuestionSpec",
    "build_booking",
    "parse_questions",
    "question_field_name",
]
