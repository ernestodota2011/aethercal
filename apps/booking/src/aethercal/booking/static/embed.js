// AetherCal booking widget — embed loader.
//
// A tenant drops one line on their site:
//
//   <script src="https://book.example.com/embed.js" data-aethercal-slug="discovery-call"></script>
//
// This file reads that <script> tag's `data-*` attributes, inserts a responsive <iframe> pointed
// at the compact `/embed/{slug}` booking flow (see app.py's `_EMBED_PATH_PREFIX`), and resizes the
// iframe to fit the guest's content by listening for the `aethercal:resize` message the embedded
// page's own inline script posts on load/resize/HTMX-swap (views.py's `EMBED_RESIZE_SCRIPT`).
//
// No dependencies, no build step, ES5-safe (var/function, no arrow/template-literal/let/const) so
// it runs unmodified on any host page's browser matrix.
(function () {
  "use strict";

  // `document.currentScript` is only live while this file's own top-level code is executing — it
  // is null once we're inside an event-listener callback, which is why it's captured right here at
  // the top, not inside `onMessage` below. Falls back to scanning for the last <script> that
  // carries the required attribute, for the rare host that runs this via a loader which clears
  // `document.currentScript` (e.g. some async-inject snippets).
  var script = document.currentScript;
  if (!script) {
    var scripts = document.getElementsByTagName("script");
    for (var i = scripts.length - 1; i >= 0; i--) {
      if (scripts[i].hasAttribute("data-aethercal-slug")) {
        script = scripts[i];
        break;
      }
    }
  }
  // No host script found, or this exact <script> element already mounted an iframe — the mounted
  // flag makes re-running this file against the SAME tag a no-op (idempotent), so a host page that
  // accidentally includes the loader twice (or a bundler that re-executes it) never double-embeds.
  if (!script || script.getAttribute("data-aethercal-mounted") === "true") {
    return;
  }
  script.setAttribute("data-aethercal-mounted", "true");

  var slug = script.getAttribute("data-aethercal-slug");
  if (!slug) {
    return; // required attribute missing — nothing we can embed
  }
  var lang = script.getAttribute("data-lang"); // optional — omitted means "don't force a locale"
  var base = script.getAttribute("data-base") || "https://book.aetherlogik.com";
  base = base.replace(/\/+$/, ""); // strip trailing slash(es) so `base + "/embed/..."` never doubles

  var src = base + "/embed/" + encodeURIComponent(slug);
  if (lang) {
    src += "?lang=" + encodeURIComponent(lang);
  }

  var iframe = document.createElement("iframe");
  iframe.src = src;
  iframe.title = "AetherCal booking";
  iframe.loading = "lazy";
  iframe.setAttribute("scrolling", "no");
  iframe.style.width = "100%";
  iframe.style.border = "0";
  iframe.style.minHeight = "640px"; // a reasonable first paint; the resize listener refines it

  // Insert right where the <script> tag sits, so the host's own layout/CSS around it applies
  // exactly as if the iframe had been hand-authored in that spot.
  script.parentNode.insertBefore(iframe, script.nextSibling);

  // The origin an incoming `postMessage` must match to be trusted (derived from `data-base`, not
  // hardcoded) — computed via the classic anchor-element trick rather than the `URL` constructor,
  // so this keeps working on older browsers with no native `URL` support.
  var originAnchor = document.createElement("a");
  originAnchor.href = base;
  var expectedOrigin = originAnchor.protocol + "//" + originAnchor.host;

  window.addEventListener("message", function (event) {
    // SECURITY: only ever trust a resize message from the exact origin we embedded — anything
    // else is ignored outright, so an unrelated iframe/window on the page (ads, other widgets)
    // can't spoof a resize (or worse) by posting a look-alike message.
    if (event.origin !== expectedOrigin) {
      return;
    }
    // Also require the message to have come from THIS loader's own iframe: with two or more
    // AetherCal widgets of the SAME origin on one page, `event.origin` alone matches all of them,
    // so one widget's resize would size EVERY iframe. `event.source` is the posting window, so
    // this scopes each resize to the iframe that actually sent it. (`contentWindow` is null until
    // the iframe gains a browsing context; a message can only arrive from a loaded frame, so by
    // the time we're in here it is populated.)
    if (event.source !== iframe.contentWindow) {
      return;
    }
    var data = event.data;
    if (!data || data.type !== "aethercal:resize") {
      return;
    }
    var height = data.height;
    if (typeof height === "number" && height > 0) {
      iframe.style.height = height + "px";
    }
  });
})();
