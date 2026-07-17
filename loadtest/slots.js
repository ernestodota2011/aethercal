// k6 load test for the hottest READ path: the public slots endpoint.
//
//   GET /public/{tenant}/{event}/slots?from=YYYY-MM-DD&to=YYYY-MM-DD&tz=America/New_York
//
// This is the query every booking-page load makes, unauthenticated, and the one that fans out into
// the availability + busy-cache computation — so it is where latency shows up first under load. It
// is a pure read: it books nothing, so it is safe to point at a staging instance. (The booking POST
// is deliberately NOT here — it mutates, trips Turnstile and the per-IP rate limit, and would need
// its own scenario with real anti-spam handling. See README.md.)
//
// Run (see README.md for the full setup):
//   k6 run -e BASE_URL=https://staging.example.com -e TENANT=acme -e EVENT=discovery-call loadtest/slots.js

import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const TENANT = __ENV.TENANT || "acme";
const EVENT = __ENV.EVENT || "discovery-call";
const TZ = __ENV.TZ || "America/New_York";

export const options = {
  // A ramp, not a flat load: the interesting number is where p95 starts to bend, not the average at
  // one arbitrary VU count.
  stages: [
    { duration: "30s", target: 20 }, // warm up
    { duration: "1m", target: 20 }, // hold
    { duration: "30s", target: 50 }, // push
    { duration: "1m", target: 50 }, // hold at the higher level
    { duration: "30s", target: 0 }, // ramp down
  ],
  // Thresholds are the pass/fail contract. A load test with no threshold is a graph nobody reads.
  // These are starting targets for a single small instance; tune them to the deployment, do not
  // loosen them to make a bad run pass.
  thresholds: {
    http_req_failed: ["rate<0.01"], // < 1% of requests error
    http_req_duration: ["p(95)<500", "p(99)<1500"], // p95 under 500ms, p99 under 1.5s
    // The functional `check()`s below must ALSO gate the run — otherwise a fast 200 that returns a
    // malformed body (no slots array) passes silently, and "fast but wrong" reads as success.
    checks: ["rate>0.99"], // > 99% of the status/body checks pass
  },
};

function windowDates() {
  // A one-week window starting a day out — the shape a booking page actually requests. Computed per
  // iteration so the test is not hammering one cached date forever.
  const from = new Date();
  from.setUTCDate(from.getUTCDate() + 1);
  const to = new Date(from);
  to.setUTCDate(to.getUTCDate() + 7);
  const iso = (d) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(to) };
}

export default function () {
  const { from, to } = windowDates();
  const url = `${BASE_URL}/public/${TENANT}/${EVENT}/slots?from=${from}&to=${to}&tz=${encodeURIComponent(TZ)}`;
  const res = http.get(url, { tags: { name: "public_slots" } });
  check(res, {
    "status is 200": (r) => r.status === 200,
    "body has slots array": (r) => {
      try {
        return Array.isArray(r.json("slots"));
      } catch {
        return false;
      }
    },
  });
}
