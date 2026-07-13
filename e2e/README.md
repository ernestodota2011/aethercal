# End-to-end tests (F1-13)

The only tests that cross **all three surfaces** — the public booking page, the API, and the
outbound webhook — and make them agree about the same booking (RF-23). Everything else in this repo
tests one seam at a time; the defects that ship live *between* seams.

They run against the **shipping artifact**: `deploy/docker-compose.yml`, built and booted exactly the
way `deploy/README.md` tells a self-hoster to do it. The suite never boots the app itself.

```bash
pnpm --dir e2e install
pnpm --dir e2e exec playwright install --with-deps chromium

pnpm --dir e2e stack:up      # compose up + migrate + create tenant + issue API key → .stack.json
pnpm --dir e2e test          # the golden flow + accessibility
pnpm --dir e2e stack:down    # down -v
```

## What the golden flow proves

1. A guest **books** an offered time in a real browser, on the public page.
2. The **API** reports that booking, and the slot **disappears** from the offer — in the API *and* on
   the page.
3. The **outbound webhook** arrives at a sink, and its `X-AetherCal-Signature` **verifies** against
   the exact bytes delivered (with negative controls: a flipped bit and a wrong secret must both
   fail, or the check would be theatre).
4. The **confirmation email** lands in a real mailbox carrying the guest's signed links. That is the
   only place a guest ever receives them — the confirmation *page* does not render them.
5. The guest **reschedules**: the successor takes the new time, the predecessor is cancelled, and
   **the old slot comes back on offer while the new one leaves it**.
6. The guest **cancels** the successor: the slot is released and the `booking.cancelled` webhook
   fires, signed.

## The rule this suite lives by

> A test that cannot fail is worse than no test: it is a false signature of quality.

So:

- **No stack ⇒ hard error, never a skip.** `src/stack.ts` refuses to build a config out of nothing,
  and `global-setup.ts` re-probes the API, the booking page, the mailbox and the sink before a single
  spec runs. It also refuses to start when the bootstrapped event type offers fewer than three
  slots — an empty calendar would make every booking assertion vacuously true.
- **`retries: 0`.** A retry launders an intermittent defect into a green report.
- **No baseline of accepted a11y violations.** The axe assertion is *zero* WCAG A/AA violations. A
  baseline is how a suite learns to shrug. (The job exists because the axe run that once caught the
  contrast bug was manual and unrepeatable.)
- **Nothing is skipped quietly.** What cannot run yet is marked with the surface it waits for
  (`no-show.spec.ts`); what is broken today is pinned red-on-purpose (`guest-links.spec.ts`) so it
  can be neither fixed silently nor forgotten.

## Layout

| Path | What |
|---|---|
| `compose.e2e.yml` | Overlay on the shipping stack: a Mailpit mailbox + a webhook sink |
| `sink/receiver.py` | The sink: stores each delivery's raw bytes + headers (stdlib only) |
| `scripts/stack-up.sh` | Boot, migrate, create the tenant, issue the key, write `.stack.json` |
| `global-setup.ts` | Reachability gate + this run's fixtures (schedule, event type, webhook) |
| `src/` | The oracle: API client, mailbox, sink + HMAC verification, page helpers |
| `specs/golden-flow.spec.ts` | The journey above |
| `specs/guest-links.spec.ts` | The mailed cancel/reschedule links — **red on purpose** (see below) |
| `specs/no-show.spec.ts` | RF-25 — **pending**, waiting on a surface that marks a no-show |
| `specs/a11y.spec.ts` | axe-core over every step of the flow, in both locales |

## Product defects this suite exposed

### 1. The mailed guest links were unusable — FIXED in `main` 7a8d336 (P1, RF-09)

The server minted `{base}/cancel?token=…` while the booking page reads `booking=<uuid>` (and
`event_type` to reschedule), so **no guest could cancel or reschedule from their confirmation
email**. Both halves were internally consistent and unit-tested; the defect lived in the seam, which
is precisely what this suite exists to walk. `_guest_link` now mints the full context, and
`guest-links.spec.ts` — which opens the mailed link **verbatim** — stays as the regression guard.

### 2. A webhook can never reach a private address

The delivery worker refuses any target that is not globally routable (`webhooks/ssrf.py`, plus a
connect-time IP pin). Correct against SSRF — and it also means **a self-hoster whose n8n / Make / CRM
runs on the same Docker network or LAN receives nothing**, the delivery parked `dead` with no signal.
The sink in `compose.e2e.yml` therefore sits on a bridge with a *public* subnet (`44.44.44.0/24`);
that is test tackle, not a fix. The real fix belongs in the server: an operator-configured, env-only,
fail-closed allowlist of private targets — never derived from inbound data.

### 3. `deploy/.env.example` documents a CLI that does not exist

It says `aethercal-admin create-tenant "<name>"` and `issue-api-key --tenant "<name>"`. The real
commands are `create-tenant --slug --name --email [--timezone]` and
`issue-api-key --tenant-slug --name`. A self-hoster following it verbatim gets a usage error on step
one of the quickstart. `scripts/stack-up.sh` uses the real ones.
