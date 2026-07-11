// POSITIVE runtime hook-execution proof for the F2-DR React-dedupe spike.
//
// Placement: copy into the freshly built `<demo>/.web/` directory and run
// `node hooktest.mjs` from there. Running inside `.web/` makes Node resolve the
// bare `react` / `react/jsx-runtime` specifiers that the calendar bundle imports
// (as EXTERNAL) to the single installed `.web/node_modules/react` (19.2.6) --
// exactly what Vite does in the browser build.
//
// If a second React instance were involved, the useMemo/useCallback hooks inside
// the component would throw "Invalid hook call" during renderToStaticMarkup.
//
// Expected output:
//   REACT_VERSION 19.2.6
//   HOOKS_RAN_OK true
//   HTML_LEN 9428
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AetherCalendar } from "./public/external/aethercal/ui/calendar/assets/aethercal-calendar.js";

const el = React.createElement(AetherCalendar, {
  view: "month",
  events: [
    { id: "e1", title: "Demo", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" },
  ],
});

const html = renderToStaticMarkup(el);
console.log("REACT_VERSION", React.version);
console.log("HOOKS_RAN_OK", html.includes("aethercal-calendar"));
console.log("HTML_LEN", html.length);
console.log("HTML_HEAD", html.slice(0, 160));
