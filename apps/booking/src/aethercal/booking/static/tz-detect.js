// AetherCal booking page — timezone auto-detection.
//
// Externalized from an inline <script> (was previously injected as a raw string) so the app can
// run a strict `script-src 'self'` CSP: every script the page loads has a `src`, none carry an
// inline body. The `tz_explicit` flag is passed in via `data-tz-explicit` on this <script> tag
// itself (see `_detect_script` in views.py) and read back through `document.currentScript`.
//
// Behavior (unchanged from the inline version): detect the browser's IANA timezone; if it isn't
// already an option in the #tz <select>, add it; if the guest didn't explicitly choose a zone
// (tz_explicit === "false") and the detected zone differs from the current selection, apply it
// and either trigger HTMX's live slot refresh (if htmx is loaded) or submit the form (plain,
// works without JS/HTMX either way).
(function () {
  "use strict";
  var currentScript = document.currentScript;
  var explicit = Boolean(currentScript && currentScript.dataset.tzExplicit === "true");
  var select = document.getElementById("tz");
  if (!select) return;
  try {
    var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!tz) return;
    var has = Array.prototype.some.call(select.options, function (option) {
      return option.value === tz;
    });
    if (!has) {
      var option = document.createElement("option");
      option.value = tz;
      option.text = tz;
      select.appendChild(option);
    }
    if (!explicit && select.value !== tz) {
      select.value = tz;
      if (window.htmx) {
        select.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (select.form) {
        if (select.form.requestSubmit) {
          select.form.requestSubmit();
        } else {
          select.form.submit();
        }
      }
    }
  } catch (e) {
    // Intl unsupported or detection failed — leave the guest's explicit/default choice as-is.
  }
})();
