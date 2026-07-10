"""FastHTML view builders for the booking page — pure, server-rendered, HTMX-enhanced.

Every function here returns a FastHTML component (``FT``) and touches no network and no request
state, so each view is rendered to a string and asserted in tests. The pages are plain semantic HTML
with a light, inlined stylesheet (RNF-6: the LCP is never a heavy video/canvas) and a premium-dark
theme driven by CSS custom properties. Accessibility is built in (RNF-7): a skip link, a ``lang``
attribute, semantic landmarks, labelled inputs, and ``aria-describedby`` wiring for inline errors.

FastHTML ships no type stubs, so the tag constructors are untyped upstream; builders are annotated
``-> Any`` (the strict-mode unknown-type family is silenced in ``pyright`` config) while every
parameter this module owns is fully typed. Text passed to tag constructors is auto-escaped by
FastHTML. Every ``<script>`` this module emits (htmx, the timezone-detection script) is
externally sourced (``src=``) with no inline body — required for the app's ``script-src 'self'``
CSP (see ``app.py``'s security-headers middleware). ``NotStr`` stays exported for callers that
need to inject pre-rendered markup (e.g. tests composing a page shell around a raw fragment).
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fasthtml.common import (
    H1,
    H2,
    A,
    Body,
    Button,
    Dd,
    Div,
    Dl,
    Dt,
    Footer,
    Form,
    Head,
    Header,
    Html,
    Input,
    Label,
    Li,
    Link,
    Main,
    Meta,
    Nav,
    NotStr,
    Option,
    P,
    Script,
    Section,
    Select,
    Span,
    Style,
    Textarea,
    Title,
    Ul,
    to_xml,
)

from aethercal.booking.forms import FieldError, QuestionSpec, question_field_name
from aethercal.booking.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, Locale, t
from aethercal.booking.settings import DEFAULT_BASE_URL
from aethercal.booking.timefmt import DayGroup, slot_aria_label
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeRead, resolve_description, resolve_title
from aethercal.schemas.slots import Availability

# Self-hosted (vendored at `static/htmx-2.0.4.min.js`, served by the app itself via `/static`) —
# never a third-party CDN, so the page has no external script dependency and can run a strict
# `script-src 'self'` CSP. Everything works WITHOUT it — the flow is plain forms; htmx only
# live-swaps the slot list when the guest changes timezone.
_HTMX_SRC = "/static/htmx-2.0.4.min.js"

# Premium-dark, brand-warm (ember accent) — deliberately NOT the lavender/violet/cyan-glow AI-slop
# palette. Boxless: hairline separators + air, not stacked cards. Light mode is provided for
# preference/accessibility, but dark is the primary aesthetic.
_CSS = """
:root {
  --bg: #0e0e10; --surface: #16161a; --border: #2a2a30;
  --text: #ededee; --muted: #a2a2aa; --accent: #e0894b; --accent-ink: #1b1206;
  --focus: #f4b477; --danger: #e08497; --radius: 10px; --maxw: 42rem;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #faf9f7; --surface: #ffffff; --border: #e5e2dc;
    --text: #1b1b1e; --muted: #5f5f68; --accent: #b4632a; --accent-ink: #ffffff;
    --focus: #b4632a; --danger: #b23a52; color-scheme: light;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.55; font-size: 1rem;
}
a { color: var(--accent); }
main { max-width: var(--maxw); margin: 0 auto; padding: 2rem 1.25rem 4rem; }
.site-header, .site-footer {
  max-width: var(--maxw); margin: 0 auto; padding: 1rem 1.25rem;
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
}
.site-footer { color: var(--muted); font-size: .85rem; border-top: 1px solid var(--border);
  margin-top: 2rem; }
.brand { font-weight: 600; letter-spacing: -0.01em; color: var(--text); text-decoration: none; }
.langs a { color: var(--muted); text-decoration: none; font-size: .85rem; padding: 0 .35rem; }
.langs a[aria-current="true"] { color: var(--text); font-weight: 600; }
h1 { font-size: 1.6rem; line-height: 1.2; letter-spacing: -0.02em; margin: 0 0 .5rem; }
h2 { font-size: 1.05rem; margin: 1.75rem 0 .75rem; letter-spacing: -0.01em; }
.lead { color: var(--muted); margin: 0 0 1.5rem; }
.meta { color: var(--muted); font-size: .9rem; }
.skip-link {
  position: absolute; left: -999px; top: 0; background: var(--accent); color: var(--accent-ink);
  padding: .6rem 1rem; border-radius: 0 0 var(--radius) 0; z-index: 10;
}
.skip-link:focus { left: 0; }
.stack > * + * { margin-top: 1.5rem; }
.event-list { list-style: none; padding: 0; margin: 0; }
.event-list li { padding: 1rem 0; border-top: 1px solid var(--border); }
.event-list li:first-child { border-top: 0; }
.tz-form { display: flex; flex-wrap: wrap; align-items: end; gap: .75rem; margin: 0 0 1rem; }
label { display: block; font-weight: 500; margin-bottom: .35rem; }
.req { color: var(--muted); font-weight: 400; font-size: .8rem; }
input, select, textarea {
  width: 100%; padding: .6rem .7rem; background: var(--surface); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius); font: inherit;
}
.tz-form .field { flex: 1 1 14rem; }
textarea { min-height: 5rem; resize: vertical; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.btn {
  display: inline-block; padding: .65rem 1.1rem; border-radius: var(--radius); border: 0;
  background: var(--accent); color: var(--accent-ink); font: inherit; font-weight: 600;
  text-decoration: none; cursor: pointer; text-align: center;
}
.btn.secondary { background: transparent; color: var(--text); border: 1px solid var(--border); }
.day { margin: 1.25rem 0 .5rem; font-size: .8rem; text-transform: uppercase;
  letter-spacing: .06em; color: var(--muted); }
.slots { display: grid; grid-template-columns: repeat(auto-fill, minmax(6.5rem, 1fr)); gap: .6rem; }
.slots form { margin: 0; }
.slot {
  display: block; text-align: center; padding: .65rem .5rem; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--surface); color: var(--text);
  text-decoration: none; font: inherit; cursor: pointer; width: 100%;
}
.slot:hover { border-color: var(--accent); }
.field { margin-bottom: 1.1rem; }
.field-error { color: var(--danger); font-size: .85rem; margin-top: .35rem; }
.notice { border: 1px solid var(--border); border-left: 3px solid var(--accent);
  padding: .9rem 1rem; border-radius: var(--radius); color: var(--muted); }
.notice.error { border-left-color: var(--danger); color: var(--text); }
.pager { display: flex; justify-content: space-between; gap: .75rem; margin-top: 1.5rem; }
dl.summary { margin: 0; }
dl.summary dt { color: var(--muted); font-size: .8rem; text-transform: uppercase;
  letter-spacing: .05em; margin-top: 1rem; }
dl.summary dd { margin: .2rem 0 0; }
body.embed main { padding: 1.25rem 1rem 1.75rem; max-width: 100%; }
"""

# --------------------------------------------------------------------------------------
# Embed auto-resize (B1) — a fixed-content inline script for `/embed/*` pages only. Since the
# content is a compile-time constant (never templated with request data), it's allow-listed via a
# CSP `sha256-` HASH source (app.py's `security_headers`) rather than the blanket `'unsafe-inline'`
# — the strictest form of "allow exactly this one script" CSP supports.
# --------------------------------------------------------------------------------------

#: Tells the parent frame the guest's current content height so it can size the iframe — a
#: same-origin-policy-isolated iframe has no other way to learn this. Runs once immediately (an
#: early estimate from what's parsed so far), then again on `load` (images/fonts settled) and
#: `resize` (guest viewport change). `htmx:afterSettle` bubbles up to `document`, so this ONE
#: listener — attached only on the initial full-page load — also covers every later HTMX-swapped
#: fragment (the timezone slot-list refresh), which never re-emits this script itself.
EMBED_RESIZE_SCRIPT = (
    "(function(){"
    "function post(){"
    "window.parent.postMessage("
    "{type:'aethercal:resize',height:document.documentElement.scrollHeight},'*');"
    "}"
    "post();"
    "window.addEventListener('load',post);"
    "window.addEventListener('resize',post);"
    "document.addEventListener('htmx:afterSettle',post);"
    "})();"
)

#: The CSP `script-src` hash source for `EMBED_RESIZE_SCRIPT`, computed once at import time over
#: the exact UTF-8 bytes a browser hashes for CSP `sha256-` matching.
EMBED_RESIZE_SCRIPT_CSP_SOURCE = (
    "'sha256-"
    + base64.b64encode(hashlib.sha256(EMBED_RESIZE_SCRIPT.encode("utf-8")).digest()).decode("ascii")
    + "'"
)


def _embed_resize_script() -> Any:
    # `Script(...)` (fasthtml.xtend) does NOT html-escape its text child — required here since the
    # script contains `&&`/`<`/`>`-free but quote-heavy JS; escaping would also change the bytes
    # the CSP hash was computed over.
    return Script(EMBED_RESIZE_SCRIPT)


def render(component: Any) -> str:
    """Render a component to an HTML string (thin wrapper over FastHTML's ``to_xml``)."""
    return to_xml(component)


def _with_lang(path: str, locale: Locale) -> str:
    return f"{path}?{urlencode({'lang': locale})}"


# --------------------------------------------------------------------------------------
# Shell + shared chrome.
# --------------------------------------------------------------------------------------


def _lang_switcher(locale: Locale, lang_urls: Mapping[Locale, str]) -> Any:
    links: list[Any] = []
    for candidate in SUPPORTED_LOCALES:
        url = lang_urls.get(candidate)
        if not url:
            continue
        label = t(candidate, "spanish" if candidate == "es" else "english")
        links.append(A(label, href=url, aria_current="true" if candidate == locale else "false"))
    return Nav(*links, cls="langs", aria_label=t(locale, "language"))


def _header(locale: Locale, lang_urls: Mapping[Locale, str]) -> Any:
    return Header(
        A(t(locale, "app_name"), href=_with_lang("/", locale), cls="brand"),
        _lang_switcher(locale, lang_urls),
        cls="site-header",
    )


def _footer(locale: Locale) -> Any:
    return Footer(P(t(locale, "footer_powered")), cls="site-footer")


def _hreflang_links(lang_urls: Mapping[Locale, str]) -> list[Any]:
    """``<link rel="alternate" hreflang="...">`` for every locale ``page()`` was given a URL for,
    plus ``x-default`` pointing at the default-locale URL — so a crawler (and any client that
    parses it) knows the current page's URL in each language (RNF-1: ES primary + EN)."""
    links = [
        Link(rel="alternate", hreflang=candidate, href=lang_urls[candidate])
        for candidate in SUPPORTED_LOCALES
        if candidate in lang_urls
    ]
    default_url = lang_urls.get(DEFAULT_LOCALE)
    if default_url:
        links.append(Link(rel="alternate", hreflang="x-default", href=default_url))
    return links


#: Locale → Open Graph ``og:locale`` tag (RFC-ish ``language_TERRITORY`` form platforms expect).
_OG_LOCALE: dict[Locale, str] = {"es": "es_ES", "en": "en_US"}

#: The social-preview image every page references (absolute, since an unfurler has no request
#: context of its own). The file itself is generated/uploaded separately — this module only wires
#: the path.
_OG_IMAGE_PATH = "/static/og.png"


def _social_meta(locale: Locale, *, full_title: str, base_url: str, current_url: str) -> list[Any]:
    """Open Graph + Twitter Card ``<meta>`` tags (A7) — every url is absolute (``base_url``-
    prefixed) so a social unfurler (WhatsApp/email/Slack) fetched out-of-band still resolves them.
    """
    description = t(locale, "meta_description")
    image_url = f"{base_url}{_OG_IMAGE_PATH}"
    return [
        Meta(property="og:title", content=full_title),
        Meta(property="og:description", content=description),
        Meta(property="og:type", content="website"),
        Meta(property="og:site_name", content=t(locale, "app_name")),
        Meta(property="og:url", content=current_url),
        Meta(property="og:image", content=image_url),
        Meta(property="og:locale", content=_OG_LOCALE.get(locale, _OG_LOCALE[DEFAULT_LOCALE])),
        Meta(name="twitter:card", content="summary_large_image"),
        Meta(name="twitter:title", content=full_title),
        Meta(name="twitter:description", content=description),
        Meta(name="twitter:image", content=image_url),
    ]


def page(
    locale: Locale,
    title: str,
    *content: Any,
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
    embed: bool = False,
) -> Any:
    """The full HTML document shell: head, accessible chrome, and ``content`` inside ``<main>``.

    ``base_url`` mints the ABSOLUTE urls Open Graph/Twitter Card tags require (A7); callers that
    don't thread a real ``BookingSettings.base_url`` through still get the production default
    rather than a meaningless bare relative path.

    ``embed`` (B1) renders the COMPACT shell for ``/embed/*``: no site header/footer/language
    switcher (an iframe embedder provides its own chrome, or none) and a reduced-padding
    ``<main>``, plus the inline auto-resize script (``EMBED_RESIZE_SCRIPT``) so the embedder can
    size the iframe to the guest's content. The skip-link is also omitted — with no header there
    is nothing before ``<main>`` to skip past.
    """
    full_title = f"{title} · {t(locale, 'app_name')}"
    current_url = f"{base_url}{lang_urls.get(locale, '')}"
    body_children: list[Any] = []
    if not embed:
        body_children.append(A(t(locale, "skip_to_content"), href="#main", cls="skip-link"))
        body_children.append(_header(locale, lang_urls))
    body_children.append(Main(*content, id="main"))
    if embed:
        body_children.append(_embed_resize_script())
    else:
        body_children.append(_footer(locale))
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Meta(name="color-scheme", content="dark light"),
            Meta(name="description", content=t(locale, "meta_description")),
            *_social_meta(
                locale, full_title=full_title, base_url=base_url, current_url=current_url
            ),
            Title(full_title),
            *_hreflang_links(lang_urls),
            Link(rel="icon", type="image/svg+xml", href="/static/favicon.svg"),
            Style(_CSS),
            Script(src=_HTMX_SRC, defer=True),
        ),
        Body(*body_children, cls="embed" if embed else None),
        lang=locale,
    )


# --------------------------------------------------------------------------------------
# Index + event landing.
# --------------------------------------------------------------------------------------


def _duration_label(locale: Locale, event: EventTypeRead) -> str:
    return t(locale, "duration_minutes", minutes=event.duration_seconds // 60)


def index_page(
    locale: Locale,
    *,
    event_types: Sequence[EventTypeRead],
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
) -> Any:
    """Landing page: the tenant's bookable meeting types, each linking into the booking flow."""
    if not event_types:
        body: Any = P(t(locale, "index_empty"), cls="lead")
    else:
        items = [
            Li(
                A(
                    resolve_title(event, locale),
                    href=_with_lang(f"/e/{event.slug}", locale),
                    cls="brand",
                ),
                Div(_duration_label(locale, event), cls="meta"),
            )
            for event in event_types
        ]
        body = Ul(*items, cls="event-list")
    return page(
        locale,
        t(locale, "index_title"),
        Div(
            H1(t(locale, "index_title")), P(t(locale, "index_lead"), cls="lead"), body, cls="stack"
        ),
        lang_urls=lang_urls,
        base_url=base_url,
    )


def _event_intro(locale: Locale, event: EventTypeRead) -> Any:
    meta_parts = [_duration_label(locale, event)]
    if event.location:
        meta_parts.append(event.location)
    bits: list[Any] = [H1(resolve_title(event, locale)), P(" · ".join(meta_parts), cls="meta")]
    description = resolve_description(event, locale)
    if description:
        bits.append(P(description, cls="lead"))
    return Div(*bits)


def _tz_choices(current: str, options: Sequence[str]) -> list[str]:
    choices = list(options)
    if current not in choices:
        choices.insert(0, current)
    return choices


def _tz_form(
    locale: Locale,
    *,
    self_path: str,
    tz: str,
    tz_options: Sequence[str],
    hidden: Sequence[tuple[str, str]],
    slots_endpoint: str | None = None,
) -> Any:
    """A timezone ``<select>`` in a GET form. With ``slots_endpoint`` it HTMX-swaps ``#slots`` live;
    without it (reschedule) a plain submit reloads the page. Works without JS either way."""
    options = [
        Option(zone, value=zone, selected=(zone == tz)) for zone in _tz_choices(tz, tz_options)
    ]
    select_attrs: dict[str, Any] = {"id": "tz", "name": "tz"}
    if slots_endpoint is not None:
        select_attrs.update(
            hx_get=slots_endpoint,
            hx_target="#slots",
            hx_swap="outerHTML",
            hx_trigger="change",
            hx_include="closest form",
        )
    hidden_inputs = [Input(type="hidden", name=name, value=value) for name, value in hidden]
    return Form(
        Div(
            Label(t(locale, "timezone_label"), fr="tz"),
            Select(*options, **select_attrs),
            cls="field",
        ),
        *hidden_inputs,
        Button(t(locale, "timezone_update"), type="submit", cls="btn secondary"),
        method="get",
        action=self_path,
        cls="tz-form",
    )


def _detect_script(tz_explicit: bool) -> Any:
    """A deferred, externally-sourced script (``static/tz-detect.js``) that auto-detects the
    guest's browser timezone and, unless it was explicitly chosen, applies it and triggers the
    HTMX slot refresh (or a plain form submit without JS/HTMX). ``tz_explicit`` rides a
    ``data-tz-explicit`` attribute so the script tag itself carries no inline JS body — required
    for the strict ``script-src 'self'`` CSP (A5.3); the script reads it back via
    ``document.currentScript.dataset.tzExplicit``.
    """
    return Script(
        src="/static/tz-detect.js",
        data_tz_explicit="true" if tz_explicit else "false",
        defer=True,
    )


def event_page(
    locale: Locale,
    *,
    event: EventTypeRead,
    tz: str,
    tz_options: Sequence[str],
    tz_explicit: bool,
    window_from: str,
    slots: Any,
    self_path: str,
    slots_endpoint: str,
    lang_urls: Mapping[Locale, str],
    notice: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    embed: bool = False,
) -> Any:
    """Step 1: the event details, a timezone control, and the (HTMX-swappable) slot list.

    ``notice`` renders an inline error banner above the picker (I4) — used after the PRG redirect
    a 409 slot conflict on submit sends the guest back to with ``?err=slot_unavailable``.
    ``embed`` (B1) renders the compact, chrome-less ``/embed/*`` shell (see ``page()``).
    """
    intro: list[Any] = [_event_intro(locale, event)]
    if notice:
        intro.append(Div(notice, cls="notice error"))
    return page(
        locale,
        resolve_title(event, locale),
        Div(
            *intro,
            H2(t(locale, "choose_time")),
            _tz_form(
                locale,
                self_path=self_path,
                tz=tz,
                tz_options=tz_options,
                hidden=[("lang", locale), ("from", window_from)],
                slots_endpoint=slots_endpoint,
            ),
            slots,
            cls="stack",
        ),
        _detect_script(tz_explicit),
        lang_urls=lang_urls,
        base_url=base_url,
        embed=embed,
    )


# --------------------------------------------------------------------------------------
# Slots section (the HTMX partial, shared shape for initial render and live swap).
# --------------------------------------------------------------------------------------


def _slot_link(
    locale: Locale, *, book_path: str, iso: str, tz: str, label: str, aria_label: str
) -> Any:
    href = f"{book_path}?{urlencode({'start': iso, 'tz': tz, 'lang': locale})}"
    return A(label, href=href, cls="slot", aria_label=aria_label)


def _pager(locale: Locale, prev_url: str, next_url: str, *, prev_disabled: bool = False) -> Any:
    """Prev/next navigation. ``prev_disabled`` renders "previous week" as a non-link, non-focusable
    notice instead of a dead link — the guest is already at the floor (the earliest allowed
    window) and clicking it would just reload the same page (I2/audit minor)."""
    prev_control: Any = (
        Span(t(locale, "prev_week"), cls="btn secondary", aria_disabled="true")
        if prev_disabled
        else A(t(locale, "prev_week"), href=prev_url, cls="btn secondary")
    )
    return Nav(
        prev_control,
        A(t(locale, "next_week"), href=next_url, cls="btn secondary"),
        cls="pager",
        aria_label=t(locale, "choose_time"),
    )


def slots_unavailable_fragment(locale: Locale) -> Any:
    """The ``#slots`` region reduced to a friendly 'temporarily unavailable' notice.

    Used when the backend can't be reached during an HTMX timezone swap: HTMX only swaps on a
    2xx, so this is returned with a normal status and no event context — the guest sees a friendly
    notice in place of the slot list instead of a broken swap or a leaked error (RF-16).
    """
    return Section(
        Div(t(locale, "availability_unavailable"), cls="notice error"),
        id="slots",
        aria_live="polite",
    )


def slots_section(
    locale: Locale,
    *,
    event: EventTypeRead,
    groups: Sequence[DayGroup],
    availability: Availability,
    tz: str,
    book_path: str,
    prev_url: str,
    next_url: str,
    prev_disabled: bool = False,
) -> Any:
    """The bookable-times region (``id="slots"``): day-grouped time links, or a friendly notice."""
    if availability == "unavailable":
        inner: Any = Div(t(locale, "availability_unavailable"), cls="notice error")
    elif not groups:
        inner = Div(t(locale, "no_slots"), cls="notice")
    else:
        blocks: list[Any] = []
        for group in groups:
            links = [
                _slot_link(
                    locale,
                    book_path=book_path,
                    iso=choice.iso,
                    tz=tz,
                    label=choice.label,
                    aria_label=slot_aria_label(choice.label, group.heading),
                )
                for choice in group.slots
            ]
            blocks.append(Div(group.heading, cls="day"))
            blocks.append(Div(*links, cls="slots"))
        inner = Div(*blocks)
    pager = _pager(locale, prev_url, next_url, prev_disabled=prev_disabled)
    return Section(inner, pager, id="slots", aria_live="polite")


# --------------------------------------------------------------------------------------
# Booking form (step 2) + confirmation (step 3).
# --------------------------------------------------------------------------------------


#: The honeypot field name — plausible enough that a naive spam bot fills it, but no real guest
#: ever sees or focuses it (CSS-hidden off-screen, `tabindex="-1"`, `aria-hidden`). A non-empty
#: value on submit means a bot filled the form (see ``book_submit``'s honeypot check).
HONEYPOT_FIELD_NAME = "company_website"


def _honeypot_field() -> Any:
    """A decoy input real guests never perceive or reach, but a naive bot fills.

    `display:none`/`visibility:hidden` are common honeypot tells bots skip; positioning it
    off-screen while it stays "visible" to a naive DOM-fill script is the more effective trap.
    `tabindex="-1"` keeps it out of the keyboard tab order and `aria-hidden="true"` keeps it out
    of the accessibility tree, so a real guest (sighted or assistive-tech) never encounters it.
    """
    return Input(
        type="text",
        name=HONEYPOT_FIELD_NAME,
        id=HONEYPOT_FIELD_NAME,
        tabindex="-1",
        autocomplete="off",
        aria_hidden="true",
        style="position:absolute;left:-9999px;top:-9999px;",
    )


def _errors_by_field(errors: Sequence[FieldError]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for error in errors:
        mapping.setdefault(error.field, error.message)
    return mapping


def _field_control(
    *,
    field_name: str,
    kind: str,
    value: str,
    required: bool,
    options: Sequence[str],
    described_by: str | None,
    input_type: str,
    autocomplete: str | None,
) -> Any:
    common: dict[str, Any] = {"id": field_name, "name": field_name}
    if required:
        common["required"] = True
        common["aria_required"] = "true"
    if described_by:
        common["aria_describedby"] = described_by
    if autocomplete:
        common["autocomplete"] = autocomplete
    if kind == "textarea":
        return Textarea(value, **common)
    if kind == "select":
        opts = [Option(option, value=option, selected=(option == value)) for option in options]
        if not required:
            opts.insert(0, Option("", value=""))
        return Select(*opts, **common)
    return Input(type=input_type, value=value, **common)


def _labelled_field(
    locale: Locale,
    *,
    field_name: str,
    label: str,
    kind: str = "text",
    value: str = "",
    required: bool = False,
    options: Sequence[str] = (),
    error: str | None = None,
    input_type: str = "text",
    autocomplete: str | None = None,
) -> Any:
    error_id = f"{field_name}-error"
    label_children: list[Any] = [label]
    if required:
        label_children.append(Span(f" ({t(locale, 'required_mark')})", cls="req"))
    parts: list[Any] = [
        Label(*label_children, fr=field_name),
        _field_control(
            field_name=field_name,
            kind=kind,
            value=value,
            required=required,
            options=options,
            described_by=error_id if error else None,
            input_type=input_type,
            autocomplete=autocomplete,
        ),
    ]
    if error:
        parts.append(P(error, id=error_id, cls="field-error"))
    return Div(*parts, cls="field")


def booking_form_page(
    locale: Locale,
    *,
    event: EventTypeRead,
    start_iso: str,
    tz: str,
    when_label: str,
    questions: Sequence[QuestionSpec],
    values: Mapping[str, str],
    errors: Sequence[FieldError],
    action: str,
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
    embed: bool = False,
) -> Any:
    """Step 2: name, email, notes, and questions — re-renders inline errors on failure.

    ``embed`` (B1) renders the compact, chrome-less ``/embed/*`` shell (see ``page()``).
    """
    field_errors = _errors_by_field(errors)
    fields: list[Any] = [
        _labelled_field(
            locale,
            field_name="name",
            label=t(locale, "name_label"),
            value=values.get("name", ""),
            required=True,
            error=field_errors.get("name"),
            autocomplete="name",
        ),
        _labelled_field(
            locale,
            field_name="email",
            label=t(locale, "email_label"),
            value=values.get("email", ""),
            required=True,
            error=field_errors.get("email"),
            input_type="email",
            autocomplete="email",
        ),
    ]
    for spec in questions:
        name = question_field_name(spec.key)
        fields.append(
            _labelled_field(
                locale,
                field_name=name,
                label=spec.label,
                kind=spec.kind,
                value=values.get(name, ""),
                required=spec.required,
                options=spec.options,
                error=field_errors.get(name),
            )
        )
    fields.append(
        _labelled_field(
            locale,
            field_name="notes",
            label=t(locale, "notes_label"),
            kind="textarea",
            value=values.get("notes", ""),
        )
    )

    top_error: list[Any] = []
    if "form" in field_errors:
        top_error.append(Div(field_errors["form"], cls="notice error"))
    elif errors:
        top_error.append(Div(t(locale, "error_form_has_issues"), cls="notice error"))

    form = Form(
        *top_error,
        Input(type="hidden", name="start", value=start_iso),
        Input(type="hidden", name="tz", value=tz),
        Input(type="hidden", name="lang", value=locale),
        _honeypot_field(),
        *fields,
        Button(t(locale, "confirm_booking"), type="submit", cls="btn"),
        method="post",
        action=action,
        enctype="application/x-www-form-urlencoded",
    )
    # Derived from `action` (always "<event_path>/book") rather than hardcoding "/e/{slug}" — an
    # embed route's `action` is "/embed/{slug}/book", so this must stay "/embed/{slug}" too, or a
    # guest inside the iframe would be bounced out to the full-chrome site (B1).
    event_path = action.removesuffix("/book")
    return page(
        locale,
        resolve_title(event, locale),
        Div(
            H1(resolve_title(event, locale)),
            P(f"{t(locale, 'selected_time')}: {when_label}", cls="meta"),
            H2(t(locale, "your_details")),
            form,
            A(t(locale, "back_to_times"), href=_with_lang(event_path, locale), cls="meta"),
            cls="stack",
        ),
        lang_urls=lang_urls,
        base_url=base_url,
        embed=embed,
    )


def _calendar_details(locale: Locale, event: EventTypeRead, booking: BookingRead) -> str:
    """A short plain-text body for the add-to-calendar links: the event description (if any),
    plus the meeting link (if any) on its own line so it stays clickable in the guest's calendar
    app."""
    parts: list[str] = []
    description = resolve_description(event, locale)
    if description:
        parts.append(description)
    if booking.meeting_url:
        parts.append(booking.meeting_url)
    return "\n".join(parts)


def _google_calendar_url(locale: Locale, event: EventTypeRead, booking: BookingRead) -> str:
    """A Google Calendar "quick add" deep link pre-filled with the confirmed booking (M-F3)."""

    def google_dt(instant: datetime) -> str:
        return instant.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    params = {
        "action": "TEMPLATE",
        "text": resolve_title(event, locale),
        "dates": f"{google_dt(booking.start)}/{google_dt(booking.end)}",
        "details": _calendar_details(locale, event, booking),
    }
    if event.location:
        params["location"] = event.location
    return f"https://calendar.google.com/calendar/render?{urlencode(params)}"


def _outlook_calendar_url(locale: Locale, event: EventTypeRead, booking: BookingRead) -> str:
    """An Outlook Web "compose event" deep link pre-filled with the confirmed booking (M-F3)."""

    def outlook_dt(instant: datetime) -> str:
        return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "subject": resolve_title(event, locale),
        "startdt": outlook_dt(booking.start),
        "enddt": outlook_dt(booking.end),
        "body": _calendar_details(locale, event, booking),
        "path": "/calendar/action/compose",
        "rru": "addevent",
    }
    if event.location:
        params["location"] = event.location
    return f"https://outlook.live.com/calendar/0/deeplink/compose?{urlencode(params)}"


def _add_to_calendar_section(locale: Locale, event: EventTypeRead, booking: BookingRead) -> Any:
    """The "add to calendar" links (M-F3): Google + Outlook deep links, no server round-trip."""
    return Div(
        H2(t(locale, "add_to_calendar_heading")),
        Div(
            A(
                t(locale, "add_to_calendar_google"),
                href=_google_calendar_url(locale, event, booking),
                cls="btn secondary",
                target="_blank",
                rel="noopener noreferrer",
            ),
            A(
                t(locale, "add_to_calendar_outlook"),
                href=_outlook_calendar_url(locale, event, booking),
                cls="btn secondary",
                target="_blank",
                rel="noopener noreferrer",
            ),
            cls="pager",
        ),
    )


def confirmation_page(
    locale: Locale,
    *,
    event: EventTypeRead,
    booking: BookingRead,
    when_label: str,
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
    embed: bool = False,
) -> Any:
    """Step 3: a clear confirmation with the essentials (when, meeting link, email note) plus
    add-to-calendar links (M-F3). ``embed`` (B1) renders the compact, chrome-less shell."""
    summary: list[Any] = [Dt(t(locale, "confirmed_when")), Dd(when_label)]
    if event.location:
        summary.append(Dd(event.location, cls="meta"))
    if booking.meeting_url:
        summary.append(Dt(t(locale, "confirmed_meeting_link")))
        summary.append(Dd(A(booking.meeting_url, href=booking.meeting_url)))
    return page(
        locale,
        resolve_title(event, locale),
        Div(
            H1(t(locale, "confirmed_heading", title=resolve_title(event, locale))),
            Dl(*summary, cls="summary"),
            P(t(locale, "confirmed_email_note", email=booking.guest_email), cls="lead"),
            _add_to_calendar_section(locale, event, booking),
            cls="stack",
        ),
        lang_urls=lang_urls,
        base_url=base_url,
        embed=embed,
    )


# --------------------------------------------------------------------------------------
# Generic message + cancel/reschedule pages.
# --------------------------------------------------------------------------------------


def message_page(
    locale: Locale,
    *,
    title: str,
    message: str,
    lang_urls: Mapping[Locale, str],
    back_url: str | None = None,
    back_label: str | None = None,
    is_error: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    embed: bool = False,
) -> Any:
    """A minimal, friendly single-message page (errors, not-found, done states) — never leaks.
    ``embed`` (B1) renders the compact, chrome-less shell so a backend hiccup or a 404 inside an
    iframe never suddenly surfaces the full site chrome."""
    body: list[Any] = [H1(title), Div(message, cls="notice error" if is_error else "notice")]
    if back_url and back_label:
        body.append(A(back_label, href=back_url, cls="btn secondary"))
    return page(
        locale, title, Div(*body, cls="stack"), lang_urls=lang_urls, base_url=base_url, embed=embed
    )


def cancel_confirm_page(
    locale: Locale,
    *,
    booking_id: UUID,
    token: str,
    action: str,
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
) -> Any:
    """The cancel confirmation: a POST form carrying the booking id + guest token."""
    form = Form(
        Input(type="hidden", name="booking", value=str(booking_id)),
        Input(type="hidden", name="token", value=token),
        Input(type="hidden", name="lang", value=locale),
        Button(t(locale, "cancel_confirm"), type="submit", cls="btn"),
        method="post",
        action=action,
        enctype="application/x-www-form-urlencoded",
    )
    return page(
        locale,
        t(locale, "cancel_title"),
        Div(
            H1(t(locale, "cancel_title")),
            P(t(locale, "cancel_prompt"), cls="lead"),
            form,
            cls="stack",
        ),
        lang_urls=lang_urls,
        base_url=base_url,
    )


def reschedule_section(
    locale: Locale,
    *,
    groups: Sequence[DayGroup],
    availability: Availability,
    action: str,
    booking_id: UUID,
    token: str,
    prev_url: str,
    next_url: str,
    prev_disabled: bool = False,
) -> Any:
    """The reschedule slot list: each time is a POST button carrying ``new_start`` + the token."""
    if availability == "unavailable":
        inner: Any = Div(t(locale, "availability_unavailable"), cls="notice error")
    elif not groups:
        inner = Div(t(locale, "no_slots"), cls="notice")
    else:
        blocks: list[Any] = []
        for group in groups:
            buttons = [
                Form(
                    Input(type="hidden", name="booking", value=str(booking_id)),
                    Input(type="hidden", name="token", value=token),
                    Input(type="hidden", name="lang", value=locale),
                    Input(type="hidden", name="new_start", value=choice.iso),
                    Button(
                        choice.label,
                        type="submit",
                        cls="slot",
                        aria_label=slot_aria_label(choice.label, group.heading),
                    ),
                    method="post",
                    action=action,
                    enctype="application/x-www-form-urlencoded",
                )
                for choice in group.slots
            ]
            blocks.append(Div(group.heading, cls="day"))
            blocks.append(Div(*buttons, cls="slots"))
        inner = Div(*blocks)
    pager = _pager(locale, prev_url, next_url, prev_disabled=prev_disabled)
    return Section(inner, pager, id="slots", aria_live="polite")


def reschedule_page(
    locale: Locale,
    *,
    tz: str,
    tz_options: Sequence[str],
    tz_explicit: bool,
    self_path: str,
    hidden: Sequence[tuple[str, str]],
    section: Any,
    lang_urls: Mapping[Locale, str],
    base_url: str = DEFAULT_BASE_URL,
) -> Any:
    """The reschedule flow: a timezone control plus the slot section (times POST ``new_start``)."""
    return page(
        locale,
        t(locale, "reschedule_title"),
        Div(
            H1(t(locale, "reschedule_title")),
            P(t(locale, "reschedule_prompt"), cls="lead"),
            _tz_form(locale, self_path=self_path, tz=tz, tz_options=tz_options, hidden=hidden),
            section,
            cls="stack",
        ),
        _detect_script(tz_explicit),
        lang_urls=lang_urls,
        base_url=base_url,
    )


__all__ = [
    "HONEYPOT_FIELD_NAME",
    "NotStr",
    "booking_form_page",
    "cancel_confirm_page",
    "confirmation_page",
    "event_page",
    "index_page",
    "message_page",
    "page",
    "render",
    "reschedule_page",
    "reschedule_section",
    "slots_section",
    "slots_unavailable_fragment",
]
