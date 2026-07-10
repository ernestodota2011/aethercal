# Embedding the booking widget

AetherCal ships a compact, framed booking flow (`/embed/{slug}`) and a small loader (`embed.js`)
so any tenant can drop a working booking widget onto their own site with one `<script>` tag — no
build step, no dependency, no API key exposed to the browser.

## Quick start

Paste this where you want the widget to appear:

```html
<script
  src="https://book.aetherlogik.com/embed.js"
  data-aethercal-slug="discovery-call"
></script>
```

That's it. The loader inserts a responsive `<iframe>` right where the `<script>` tag sits and
keeps it sized to the guest's content as they move through the picker → form → confirmation flow.

A self-hosted deployment uses its own base URL for both the script and `data-base` (see below):

```html
<script
  src="https://book.example.com/embed.js"
  data-aethercal-slug="discovery-call"
  data-base="https://book.example.com"
></script>
```

## `data-*` attributes

| Attribute | Required | Purpose |
|---|---|---|
| `data-aethercal-slug` | **yes** | The event type's slug — the same slug used at `/e/{slug}` on the full site. |
| `data-lang` | no | Force a locale (`es` or `en`). Omit it to let the guest's browser/Accept-Language decide, same as the non-embedded flow. |
| `data-base` | no | The booking page's origin. Defaults to `https://book.aetherlogik.com` (AetherLogik's hosted instance). **Self-hosted deployments must set this** to their own `AETHERCAL_BOOKING_BASE_URL`. |

There is nothing else to configure — no widget ID, no async loader queue, no init call. The
`<script>` tag itself carries the whole configuration, and the loader runs immediately when the
browser parses it.

## How the resize works

An iframe is isolated by the browser's same-origin policy, so the parent page has no way to read
the guest's content height directly. Instead:

1. The embedded page (`/embed/{slug}`) carries a small inline script that computes
   `document.documentElement.scrollHeight` and calls `window.parent.postMessage({ type:
   "aethercal:resize", height }, "*")` — once immediately, again on `load`, on `resize`, and after
   every HTMX-swapped fragment (e.g. changing the timezone).
2. `embed.js`, on the host page, listens for that message and sets the `<iframe>`'s `height` style
   to match — so the widget never shows an internal scrollbar or clips content, regardless of how
   tall a given step (picker, form, confirmation) is.
3. **The listener validates `event.origin`** against the origin derived from `data-base` before
   trusting anything in the message. A message from any other origin — another iframe, an ad, an
   unrelated script on the host page — is silently ignored. This is the one security-relevant line
   in the loader; see the note below.

No polling, no `ResizeObserver` needed on the host side, no fixed/guessed height.

## Security note: `frame-ancestors`

By default (`AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS` unset), `/embed/*` sends a Content Security
Policy of `frame-ancestors *` — **any** site can iframe the booking flow. That is a deliberate v1
default: it makes the widget work out of the box for a brand-new tenant who hasn't told the
operator which domain(s) will embed it yet, with no support ticket required.

**Before going to production with real, known embedding domain(s), lock this down.** Set
`AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS` to a comma-separated list of the exact origins that are
allowed to frame `/embed/*` (e.g. `https://tenant-a.com,https://tenant-b.com`). Every other route
in the app (`/e/{slug}`, `/cancel`, `/reschedule`, …) already denies framing outright
(`frame-ancestors 'self'`) regardless of this setting — only the `/embed/*` surface is affected,
and only in the direction of tightening it from the wide-open default.

See [`deploy/.env.example`](../deploy/.env.example) and [`deploy/README.md`](../deploy/README.md)
for where to set this (and the related `AETHERCAL_BOOKING_TRUSTED_PROXIES`) in a self-hosted
deployment.

## Caching `embed.js`

`GET /embed.js` is served with a **year-long, immutable** `Cache-Control`. A host page's browser
will not re-fetch it once cached — which is the point for a script that almost never changes, but
means that if AetherLogik (or a self-hoster) ever ships a breaking change to the loader, already-
cached copies won't pick it up on their own. The escape hatch is a version query string:

```html
<script src="https://book.aetherlogik.com/embed.js?v=2" data-aethercal-slug="discovery-call"></script>
```

Bumping `?v=` is a new URL as far as the browser cache is concerned, so it forces a fresh fetch.
This is opt-in — the default snippet above has no `?v=` and that's fine for normal use; only reach
for it if you're told a new loader version fixes something you're hitting.

## Trying it locally

`static/embed-demo.html` (served at `/static/embed-demo.html` by the running booking app) is a
minimal host page wired to the real widget — open it against a local
`python -m aethercal.booking` (or any deployed instance) to see the loader working end to end.
