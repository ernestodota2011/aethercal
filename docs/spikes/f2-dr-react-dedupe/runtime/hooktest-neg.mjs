// NEGATIVE CONTROL for the F2-DR spike.
//
// The neg bundle (from build-bundled.mjs) vendors its OWN React 18 -- built
// WITHOUT external:['react']. Rendering it with the host's React-DOM 19 server
// must fail with the classic duplicate-React symptom: the component's react-18
// useMemo reads a hooks dispatcher that react-dom-19 never set. This proves
// external:['react'] is the LOAD-BEARING root cause of the dedupe, not incidental.
//
// Placement/run: put bundled-calendar.js somewhere reachable, adjust the import
// path below, and run from inside `.web/` (so react / react-dom resolve to the
// host's React 19).
//
// Expected output:
//   HOST_REACT_VERSION 19.2.6
//   EXPECTED_FAILURE Cannot read properties of null (reading 'useMemo')
import { renderToStaticMarkup } from "react-dom/server"; // host React 19
import * as hostReact from "react";
import { AetherCalendar } from "./bundled-calendar.js"; // bundle w/ its own React 18

console.log("HOST_REACT_VERSION", hostReact.version);
try {
  const el = hostReact.createElement(AetherCalendar, { view: "month", events: [] });
  const html = renderToStaticMarkup(el);
  console.log("UNEXPECTED_RENDER_OK", html.length);
} catch (e) {
  console.log("EXPECTED_FAILURE", e && e.message ? e.message.split("\n")[0] : String(e));
}
