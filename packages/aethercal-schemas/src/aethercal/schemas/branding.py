"""Per-business branding (RF-27): the public name, the logo, the accent colour, the timezone.

A business on a shared instance is not an anonymous row: it has a name a guest recognises, a mark
they recognise it by, a colour, and a place it works from. These four fields are what turn "an
AetherCal page" into "*their* page" — and they are the only fields of ``tenants`` a guest ever sees.

.. rubric:: Every rule here is a rule about a value that lands in a PUBLIC page

That is what shapes them. Each of the three optional fields is rendered, verbatim, into HTML served
to strangers, so each is constrained to a shape in which the ways a string can escape its context do
not exist — rather than escaped on the way out, which has to be right at every call site forever.

* :func:`require_accent_color` — a hex triplet, and nothing else. It is interpolated into a
  ``<style>`` block as ``--accent: <value>``, where ``;`` starts a second declaration, ``}`` closes
  the rule, and ``</style>`` closes the element. ``#rgb``/``#rrggbb`` cannot contain any of them.
* :func:`require_logo_url` — ``https``, with a host, and no credentials. See its docstring for the
  threat model, which is NOT the one the webhook allowlist answers.
* ``timezone`` — delegated, whole, to :func:`aethercal.core.tz.require_iana_zone`. ==There is ONE
  definition of "this string names a real zone" in this product and it is that one.== It exists
  because the rule was once written out four times, with the same broken ``except`` in each, and
  ``GET /slots?tz=America`` answered 500. This module consumes it; it does not own it, and it does
  not copy it.

``public_name`` needs no rule of its own: it is rendered as TEXT, and the renderer escapes text.

.. rubric:: ``display_name`` is resolved once, here

The column is ``public_name`` and it is nullable; the thing a guest reads is never null.
:func:`resolve_display_name` is the single place that walks override → fallback, for the same reason
:func:`aethercal.schemas.event_types.resolve_translation` is: two callers hold two different shapes
(the API holds the ORM row, the admin holds a form) and the second one to answer the question on its
own is how the two answers come to differ.
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aethercal.core.tz import require_iana_zone

#: ``#rgb`` or ``#rrggbb``. No keywords, no ``rgb()``, no ``color-mix()`` — see the module docstring
#: for why the belt is the format and not an escape. Anchored at both ends: an unanchored pattern
#: would happily match the ``#fff`` at the head of ``#fff; } body { display:none }``.
_ACCENT_COLOR = re.compile(r"\A#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")

#: The one scheme a logo may be served over.
_LOGO_SCHEME = "https"

#: Wire-model bounds. ``logo_url`` is generous (a signed CDN URL is long) but bounded: an unbounded
#: string is a row a page has to render.
PublicName = Annotated[str, Field(min_length=1, max_length=255)]
LogoUrl = Annotated[str, Field(min_length=1, max_length=2048)]
AccentColor = Annotated[str, Field(min_length=1, max_length=7)]
Timezone = Annotated[str, Field(min_length=1, max_length=64)]


def require_accent_color(value: str) -> str:
    """Return ``value`` (trimmed) if it is a hex triplet; raise ``ValueError`` if it is not.

    The refusal is a ``ValueError`` because that is the currency every caller already speaks:
    Pydantic turns it into a 422 on the wire, and the admin service catches it to word its own
    operator-facing refusal.
    """
    candidate = value.strip()
    if not _ACCENT_COLOR.match(candidate):
        raise ValueError(
            f"invalid accent colour: {value!r} — expected a hex triplet such as '#e0894b' or '#abc'"
        )
    return candidate


def require_logo_url(value: str) -> str:
    """Return ``value`` (trimmed) if it is an absolute ``https`` URL with a host and no credentials.

    .. rubric:: ==This is NOT an SSRF guard, and it must not be mistaken for one==

    The repository already owns an SSRF defence — :mod:`aethercal.server.webhooks.ssrf` plus the
    operator's private-target allowlist and the connect-time IP pin. It exists because an outbound
    WEBHOOK is a request **the server itself makes** to a caller-supplied URL: the server's own
    network position is the attacker's prize, so the guard is about which ADDRESSES may be reached.

    ==A logo is a different shape of thing, and copying that defence here would be cargo cult.== The
    server never fetches this URL. It renders ``<img src="...">`` into a page, and the fetch is made
    later, by the GUEST's browser, from the guest's own network. There is no server-side request to
    forge; an allowlist of private CIDRs would forbid ``https://10.0.0.5/logo.png`` — which, from
    the guest's browser, reaches the *guest's* LAN, not ours, and is simply a broken image.

    So the threat model is the one that actually applies to a value that becomes an HTML attribute
    on a page served to strangers:

    * **the scheme, because the scheme is the vulnerability.** ``javascript:`` and ``data:`` are not
      transports, they are execution: an SVG served from a ``data:`` URL runs script in the page's
      origin. Only ``https`` is accepted — never ``http`` (cleartext, and mixed content a browser
      blocks on an https page anyway) and never a protocol-relative ``//host/x`` (it inherits the
      page's scheme, which is not a decision this value gets to make).
    * **a host must be present.** ``https://`` alone, or a relative path, is not a place.
    * **no credentials.** ``https://user:secret@host/x`` would be PUBLISHED, in the page source, to
      everybody who opens it.

    What this rule deliberately does NOT do is decide which HOSTS are acceptable. The value is set
    by the business's own operator, through an authenticated admin, and it names a host on their own
    page; an allowlist of image origins would be a policy this product has no basis to write. What
    it costs is stated plainly rather than hidden: the guest's browser makes a request to that host,
    so that host learns the guest's IP and user-agent. The operator chose it — as they chose every
    other byte of their page.

    .. warning::
       If a future caller makes the **server** fetch this URL (an emailed logo, a rendered OG image,
       a thumbnail cache), this validator does not make that safe. That caller acquires the webhook
       threat model wholesale and must go through :mod:`aethercal.server.webhooks.ssrf` — the pin
       included.
    """
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != _LOGO_SCHEME
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            f"invalid logo url: {value!r} — expected an absolute https:// URL with a host and no "
            "credentials"
        )
    return candidate


def _blank_to_none(value: str | None) -> str | None:
    """``""`` / whitespace → ``None``. What an admin form submits for a field it cleared."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def resolve_display_name(public_name: str | None, name: str) -> str:
    """==THE rule==: a non-blank ``public_name`` wins; otherwise the business's registered ``name``.

    A primitive over VALUES, not over a model, because the same question ("what does the guest read
    at the top of this page?") is asked from two places holding two different shapes: the API holds
    the SQLAlchemy row, and the admin holds a form. When the only resolver took a model, the second
    caller wrote its own answer — that is the mechanism behind the event-type translation defect
    (see :func:`aethercal.schemas.event_types.resolve_translation`), and it is not re-earned here.

    A present-but-blank ``public_name`` is treated as *no* public name: it is exactly what a form
    submits for "I left this alone", and a page headed by an empty string is a broken page.
    """
    return _blank_to_none(public_name) or name


class _BrandingRules(BaseModel):
    """The three rules, declared ONCE and inherited by both wire models.

    .. rubric:: ==Both edges, and the second one is not paranoia==

    It would be natural to validate only what an operator WRITES. That would be a hole, because the
    consumer of the read model is a **different process**: the booking page holds no database
    connection: it parses whatever the API's HTTP response says, and drops ``accent_color`` straight
    into a ``<style>`` block and ``logo_url`` into an ``<img src>``.

    So a value that reached the row by any route other than the admin — the deployment runbook's
    ``psql``, a restored dump, a future service, a migration written in a hurry — would arrive at
    that f-string having been checked by nobody. Enforcing the rule on the way OUT as well means the
    page's existing "the API answered something I cannot use" branch catches it, and the guest gets
    an unbranded page rather than an injected one.

    Declaring the validators on one base is the point: two copies of "what a colour is" is precisely
    how the two edges would come to disagree — which is the whole story of ``require_iana_zone``.
    """

    @field_validator("logo_url", "accent_color", "public_name", check_fields=False, mode="before")
    @classmethod
    def _empty_is_absent(cls, value: object) -> object:
        return _blank_to_none(value) if isinstance(value, str) else value

    @field_validator("accent_color", check_fields=False)
    @classmethod
    def _check_accent_color(cls, value: str | None) -> str | None:
        return None if value is None else require_accent_color(value)

    @field_validator("logo_url", check_fields=False)
    @classmethod
    def _check_logo_url(cls, value: str | None) -> str | None:
        return None if value is None else require_logo_url(value)

    @field_validator("timezone", check_fields=False)
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        # The ONE definition, consumed — never a fifth copy. See the module docstring.
        return require_iana_zone(value)


class TenantBrandingRead(_BrandingRules):
    """The branding of ONE business, as a guest's page consumes it.

    ==``display_name`` is already RESOLVED== (:func:`resolve_display_name`) — the page receives the
    name it must show, not two fields and a rule to re-apply. The registered ``name`` is not on this
    model, and neither is the ``slug`` or the id: a booking page needs none of them, and this object
    crosses the wire.
    """

    model_config = ConfigDict(from_attributes=True)

    display_name: PublicName
    logo_url: LogoUrl | None = None
    accent_color: AccentColor | None = None
    timezone: Timezone


class TenantBrandingUpdate(_BrandingRules):
    """The branding an operator writes. ==COMPLETE, not partial.==

    Every field is sent on every write, and the three optional ones are cleared by sending them
    blank — the same shape as the host form, and for the same reason: an admin form has a box for
    each field, and a partial-update model would make an emptied box mean "unchanged", which is the
    one thing the operator did not do.
    """

    public_name: PublicName | None = None
    logo_url: LogoUrl | None = None
    accent_color: AccentColor | None = None
    timezone: Timezone


__all__ = [
    "TenantBrandingRead",
    "TenantBrandingUpdate",
    "require_accent_color",
    "require_logo_url",
    "resolve_display_name",
]
