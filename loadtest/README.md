# Load testing

AetherCal's scheduling engine is covered by property-based tests (correctness), but correctness under
one request is not the same question as **latency under many**. This directory holds the load tests
that answer the second one. They are not run in CI — they need a live instance and produce a report
you read, not a pass/fail a machine gates — but they exist so "is it fast enough?" has a repeatable
answer instead of a guess.

## What to run against

**A staging instance, never production, and never a laptop.** The number that matters is the
deployed one: real PostgreSQL, the reverse proxy, the network in front of it. A load test against
`localhost` measures your laptop and the dev server, which is a different machine with a different
story. Point it at a staging deploy that mirrors production.

## The read path — `slots.js`

Before running: the public router is off by default and needs three things set on the target
instance (see [`.env.example`](../deploy/.env.example)):

- `AETHERCAL_PUBLIC_API_ENABLED=true`
- A Turnstile secret + site key (the app refuses to boot with the flag above on and no secret set —
  Cloudflare publishes dummy "always passes" test keys for exactly this: `AETHERCAL_TURNSTILE_SECRET=1x0000000000000000000000000000000AA`,
  `AETHERCAL_TURNSTILE_SITE_KEY=1x00000000000000000000AA`)
- If you are running k6 from a single machine (the normal case), a raised
  `AETHERCAL_PUBLIC_RATE_LIMIT_PER_MINUTE` (default `30`, **per address** — k6's VUs all share one
  source IP, so the default ceiling gates the ramp within the first few seconds and the run measures
  the rate limiter, not the app). Raise it for the staging instance only; never in production.

```bash
# Install k6 once (https://k6.io/docs/get-started/installation/), then:
k6 run \
  -e BASE_URL=https://staging.aethercal.example \
  -e TENANT=acme \
  -e EVENT=discovery-call \
  -e TZ=America/New_York \
  loadtest/slots.js
```

This ramps to 50 virtual users hitting `GET /api/v1/public/{tenant}/{event}/slots` — the query every
booking-page load makes, the one that fans out into availability + busy-cache computation. Its
thresholds (in the script) are the contract:

- `http_req_failed rate < 1%`
- `http_req_duration p95 < 500ms`, `p99 < 1.5s`

k6 exits non-zero if a threshold is breached, so a run can gate a release **if you choose to wire it
in**. Tune the thresholds to your deployment — but tune them to reality, do not loosen them to make a
bad run go green.

### Reading the result

- **p95 climbing with VUs** is the signal to look for. A flat p95 means headroom; a bend means you
  found the knee. Note the VU count where it bends — that is your per-instance capacity.
- **Errors > 0** under a read-only test point at the database (connection-pool exhaustion) or the
  proxy, not the code path. Check `pg_stat_activity` and the pool size.
- Compare runs across releases: a p95 that regressed between two tags is a performance regression a
  correctness suite will never catch.

**A real run** (staging instance freshly provisioned from this repo's own production commit,
4 vCPU / 3GB, single-node Postgres in the same compose project — see `deploy/README.md`):

```
checks.........................: 100.00% 53350 out of 53350
http_req_failed................: 0.00%   0 out of 26675
http_req_duration..............: avg=235.95ms min=6.22ms med=196.99ms max=827.18ms p(90)=430.54ms p(95)=455.3ms
http_reqs......................: 26675   127.01958/s
```

All thresholds green at 50 VUs — but p95 (455ms) sits at 91% of the 500ms budget, close enough that
this is the number to re-check if this instance's real traffic grows: the knee is not far past 50
VUs on this hardware. No fix was needed for this run; noted here so the next run has a baseline to
compare against (see "Reading the result" above).

## The write path — booking POST (not scripted here, on purpose)

Load-testing `POST …/book` is a different job and is deliberately left out of `slots.js`, because a
naive script would measure the wrong thing:

- it **mutates** — every iteration creates a booking, so the test seeds its own contention and fills
  the database; it needs a disposable tenant and a cleanup step;
- it is **rate-limited and Turnstile-gated** on the public path — a load test would mostly measure
  the anti-spam layer refusing it, which is correct behaviour, not a latency number. Test it against
  the internal authenticated API, or with the limiter's trusted-proxy path configured for the test
  source;
- the **anti-double-booking** guarantee is a *correctness* property under concurrency, and it already
  has a dedicated test (`test_booking_concurrency.py`, run against real PostgreSQL) that fires
  concurrent POSTs and asserts exactly one winner. That is the concurrency question answered where it
  belongs.

If you add a write scenario, give it its own file, its own throwaway tenant, and a teardown.

## The booking page itself (Core Web Vitals)

Server latency is only half of "fast". The booking page's front-end budget — LCP, CLS, INP — is a
separate measurement (Lighthouse / the `seo-geo` review), and its hard guardrail lives outside this
directory: **the LCP element is never a video or a canvas** (poster + lazy + `prefers-reduced-motion`
+ mobile gating). A load test proving the API is quick says nothing about a page that paints slowly;
measure both.
