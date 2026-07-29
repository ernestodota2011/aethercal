# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every package in the repository shares one version number.

## [Unreleased]

### Changed

**The live harness's ORDER invariants are measured by executing them.** The guards over the money
path — *provenance is demanded before the refund is sent*, *nothing that can abort runs outside the
refund guarantee*, *the evidence block is composed only after the fact it certifies* — were checked
by reading the source, and had been approximated three times: substring (a mention in a comment
satisfied it), then AST position (a real call, but ==presence is not execution==: one inside a
nested function, a lambda or an untaken branch counts the same as one that runs), and the next fix
would have been AST-with-exclusions — another approximation, wrong again at the next syntactic
construction nobody thought to exclude.

- `tests/live/test_phase_b_execution_order.py` drives phase B and the checkout harness with doubles
  that RECORD, and asserts the sequence that actually happened. It also asserts what an ordering
  check never could: a session refused by provenance is **never** refunded, a validation that fails
  on a genuinely paid session **still** reaches `ensure_refunded`, and a run that could not return
  the money **shouts without certifying** — the alarm is reached, the evidence block is absent, and
  the attempt precedes the alarm. ==And the case the guarantee was BUILT for==: when
  `gateway.refund` itself raises (a timeout, a 500, the connection dropping), the exception
  propagates, `ensure_refunded` still runs, it runs *after* the attempt, nothing is certified, and a
  cleanup that also fails never replaces the original failure — asserted by IDENTITY (`is`), not by class: both stories end in a `ConnectError`, so the class is compatible with the thing being proved AND the thing being feared. ==Shouting and certifying in the same breath would put a claim
  into `live_verifications()` that the alarm itself contradicts.==
- Offline and unmarked: no credential, no network, so the money harness's own invariants run on
  every commit instead of only on the day somebody runs it for real.
- The AST guards stay for claims about SHAPE ("the guarantee is the last statement", "the creation
  is wrapped by its recovery", "every provider-touching test declares the control fixture") — those
  are statements about how the code is written, and reading it is the right way to answer them.


**A business's messages now go out on that business's own account** — B-03 stored per-business
credentials; nothing sent with them.

> [!WARNING]
> **Breaking for multi-business instances that rely on `AETHERCAL_WHATSAPP_*` / `AETHERCAL_SMS_*`.**
> A business with no phone credential of its own used to send from the **instance operator's**
> number, silently. It no longer does: those steps are now `skipped`, with a reason.
>
> To restore the old behaviour on a **single-business self-host** (where the operator *is* the
> business), set `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=true`. On an instance serving more than one
> business, give each business its own credential instead —
> `aethercal-admin credentials set --provider whatsapp`.

- The senders are resolved **per business, per outbox item**, from that item's own `tenant_id`,
  inside the drain's existing per-item `tenant_scope`. They used to be built once at boot from the
  instance's environment and shared by every business the drain worked through.
- **Email is unchanged.** An SMTP relay is a transport, not an identity — the `From` header travels
  per message — so the instance relay is still lent to a business that has no SMTP of its own. A
  single-business self-hoster who set `AETHERCAL_SMTP_HOST` once keeps working exactly as before.
- The daily caps stay the **operator's** policy and now bound a business's own phone sender too: the
  recipient comes from the operator's public booking form regardless of whose API key pays the bill.
  A business that brings a phone credential to an instance with no caps declared keeps that channel
  off, and the worker logs which variables would turn it on.
- `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY` (default `false`) is warned about at boot when enabled.
- **A business's `base_url` is now guarded on egress.** Moving it out of the environment and into a
  per-business credential turned it from operator configuration into input a third party controls
  and the server obeys — so a tenant-supplied endpoint must be `https` and must resolve to a
  **public** address. Loopback, link-local (including `169.254.169.254`, the cloud metadata
  service), RFC1918, CGNAT and reserved ranges are refused, checked by **resolved IP** rather than
  hostname, with one bad record poisoning the whole target. The operator's own
  `AETHERCAL_*_BASE_URL` is unaffected — provenance decides — and the operator's private-target
  allowlist is deliberately not honoured for a tenant: it exists so the operator can reach their own
  LAN, not so a business can.
- **The egress guard is re-applied at connect, so DNS rebinding cannot move the socket.** A
  build-time check is a TOCTOU window — httpx re-resolves when it opens the connection, so a tenant
  controlling its own resolver could answer public for the guard and `127.0.0.1` for the socket. A
  tenant's sender is now built on a client whose transport re-validates and pins the address at
  connect (reusing the webhook path's `pinned_ip_for`), with `Host` and TLS certificate
  verification still bound to the real hostname.
- **Tenant egress keeps no idle connection.** Pinning rewrites the request's host to the validated
  IP, and a connection pool is keyed by origin — so two businesses whose hostnames resolve to the
  same address would collapse onto one pool key and could share a TLS connection established with
  the other's certificate. Keep-alive is off for tenant egress (HTTP/2 stays off too, and is now
  asserted): every send stands up its own connection, handshaked for its own hostname. The cost is
  a TLS handshake per send.
- **One broken channel no longer silences the others — whatever breaks it.** A failure while
  resolving one channel escaped the resolver entirely, so a business with a bad WhatsApp `base_url`
  (or simply a DNS outage on it) stopped receiving its *email*. Isolation is now scoped to the
  operation — resolving one channel — rather than to a list of error types, so it holds for failures
  nobody has enumerated. A channel that is configured and fails to resolve FAILS and retries,
  carrying its own reason; one that was never configured is still skipped. A cancellation or
  shutdown is not a channel failure and still rises.
- **A workflow step on email sees its channel's fault.** A business whose SMTP relay was refused
  had its email reminders retired as "the channel is off" — terminal — while the recorded reason
  went unread: emails failing silently, in a product whose job is sending reminders. Every
  sender-less path now goes through one door that tells OFF (terminal) from BROKEN (retryable).
- **A phone credential with no declared caps is a fault, not a switched-off channel.** It made the
  channel merely absent, which the drain reads as terminal — so a guest's reminder was discarded for
  ever because an operator had not set `AETHERCAL_<CHANNEL>_DAILY_CAP_*`, and setting it afterwards
  brought nothing back. The channel still cannot come up uncapped; it now fails and retries, so the
  message outlives the fix.
- **A tenant's SMTP relay host is guarded too, and pinned at connect.** `host: 127.0.0.1, port: 25`
  relayed a business's mail through the operator's own local MTA — an open relay on the operator's
  IP reputation. It is the same trust-boundary bug without the HTTP, and scoping it out was
  classifying by protocol when the rule is declared by provenance. A business's relay is now dialed
  through a connector that re-validates and pins the address at connect and hands the SMTP client an
  already-connected socket, so it performs no lookup of its own; `hostname` still drives TLS
  SNI/certificate verification. The operator's own `AETHERCAL_SMTP_HOST` is unaffected.
- Removed `app.build_email_sender` / `app.build_channel_senders`, replaced by
  `app.build_instance_sender_defaults` (configuration, not clients). The web process no longer
  builds senders at all — it never read the ones it was building. Its half-configured-phone-channel
  boot check is unchanged.

**Multi-business isolation is now enforced by PostgreSQL, not by application code** — every
tenant-scoped table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` plus one policy, and the process
serving requests no longer owns the tables (migration `0008_rls_roles_and_policies`).

> [!WARNING]
> **Breaking for every self-host.** The API used to run as a single database role that owned every
> table and migrated the schema on its own boot (`AETHERCAL_AUTO_MIGRATE`, default on). It now runs
> as **three** separate PostgreSQL roles across **three** separate processes, and two new environment
> variables (`AETHERCAL_OWNER_DATABASE_URL`, `AETHERCAL_WORKER_DATABASE_URL`) are required before the
> stack will start at all.
>
> Before pulling this: create the three roles with `deploy/sql/provision_roles.sql` (a superuser,
> one-time, human step — it cannot be a migration), add the two new database URLs to `.env`, and
> remove `AETHERCAL_AUTO_MIGRATE` / `AETHERCAL_RUN_SCHEDULER` from `.env` if either is set — both now
> **fail the boot** instead of being honoured. See [UPGRADING.md](UPGRADING.md).

- Migrations no longer run inside the web process. A one-shot `migrate` service runs
  `aethercal-admin db upgrade` as the new `aethercal_owner` role, to completion, before `app` or
  `worker` starts; the web process then refuses to serve a schema behind head.
- The background drain (reminders, outbound webhooks, the Google busy-cache refresh) is no longer a
  flag on the web process. It is its own `aethercal-worker` process, which must run in **exactly
  one** replica for the whole deployment (`deploy/README.md`).
- `aethercal_app` (the API + admin) never holds `BYPASSRLS`; an unbound session reads zero rows
  rather than every business's, and a write carrying another business's id is denied.
- `tenants` deliberately carries no policy (the public router resolves a business by slug before any
  session is bound to one); every other tenant-scoped table does, derived from the models rather than
  from a hand-written list, so a future table with no policy fails CI instead of shipping unprotected.

### Added

**The connected calendar** — a booking now creates the event in the host's Google Calendar,
cancelling deletes it, and rescheduling moves it. ==RF-11/12/13 were ticked and were not true: no
booking had ever reached a host's calendar.== The integration and the outbox effect were both built
and tested; the one missing link was resolving the host's connection, and its absence raised nothing
anywhere — every booking skipped the sync in silence.

- The **existence of a connected calendar** is the gate, and the two ways to read "no calendar" are
  now kept apart. A host who never linked Google enqueues no intent and the booking is complete
  (benign, the self-hoster). A host who *has* a connection enqueues the intent — and if the calendar
  cannot be resolved later, at drain time, the effect retries, dead-letters and lands in the visible
  outbox backlog. It is never quietly marked delivered. Both cases used to collapse into one silent
  `return`.
- **A chain that already has an event is always synced**, whatever the host's calendars look like
  today. A host who revokes their Google account between the confirmation and the cancellation has
  no active connection — gating on that alone would drop the intent, the guest would be cancelled,
  and the meeting would stay in the host's calendar forever with nobody told.
- The intent captures only the **host**, never a connection id: the target calendar is resolved at
  drain time from the live configuration, so there is one source of truth for "where does this event
  go" instead of a snapshot that can rot between enqueue and drain.
- ==Exercised against a **fake** Google transport only; no booking has yet reached a live Google
  account.== That the code asks for the right thing is proven; what the real API answers is not.

**No-show becomes observable** — `booking.no_show` joins `booking.created`, `booking.cancelled` and
`booking.rescheduled` on the outgoing webhook vocabulary. A subscriber's CRM learned about a
cancellation and about a reschedule, but a guest who simply never turned up was invisible to it.
Widening the vocabulary needs no data migration, and no existing subscriber starts receiving the new
event by accident — nobody can have subscribed to an event that did not exist.

**Payments** — a business can now charge for an appointment with its own Stripe or Mercado Pago
account (BYOK). ==Neither provider has been run against a live account in this cut.==

- `event_types` gains `price_cents` (`NULL` = free), `currency`, `refund_window_minutes` and
  `refund_kind`; `bookings` gains `hold_expires_at` and `confirmed_by_payment_id` (migration
  `0015_payments_and_holds`). A paid event type creates the booking as an unpaid **hold** that
  self-cancels if nobody pays within its window, never blocking the slot forever.
- The arbiter (`services/payments.py`) turns a provider's payment webhook into exactly one of six
  outcomes — confirm, refund (a double payment, a stale hold, a mismatched amount), park (the
  webhook arrived before the checkout's own commit), or mark a dispute — resolved by the provider's
  own reference, never by the event's metadata.
- Refunds are always the whole charge; partial refunds are not modelled (`F5`).
- A business with **two** money credentials configured (both Stripe and Mercado Pago) is refused with
  an explicit error rather than a silent pick — a per-tenant preference needs its own migration. See
  [docs/byok-credentials.md](docs/byok-credentials.md#which-payment-provider-a-business-charges-with).
- Neither adapter has been exercised against a live or sandbox account: Stripe's is unit-tested
  against a stubbed HTTP transport only; no Mercado Pago account exists for this project at all.
  **A live credential is refused for exactly that reason** — see the next entry, and read
  [docs/byok-credentials.md](docs/byok-credentials.md#which-payment-operations-have-been-run-against-the-real-api--and-what-that-gates)
  before taking a real charge.

**A live payment credential is now refused per GATEWAY OPERATION, on evidence** — the guard used to
require an `sk_test_`/`TEST-` prefix, which asked a question no amount of testing could ever answer
differently, so it could only be overruled and never discharged.

- The product now keeps a register of what has actually been **run against the real provider API**,
  one record per operation (`checkout`, `refund`), each carrying the date and what was observed. A
  live credential is refused while any operation of that gateway has no record.
- **Nothing is verified today**, so the refusal is byte-for-byte what it was: Stripe's gateway has
  only ever spoken to a stubbed transport, and no Mercado Pago account exists. What changed is that
  the refusal can now be **retired by evidence** instead of by editing the guard.
- The evidence comes from a new opt-in harness (`apps/server/tests/live/`, marked `live_provider`)
  that exercises the real `StripeGateway` against `api.stripe.com`. It takes its key from the
  environment, never a flag, and never runs in CI.
  - **`checkout` is verified at zero cost**: create a Checkout Session, read it back through a
    separate request, expire it. No card, no charge.
  - **`refund` costs a real charge, so it is a two-phase run** — in live mode there are no test
    cards, and a live Checkout Session is completed by a person. Phase A opens a **$1** session and
    leaves it payable (behind its own opt-in); phase B runs the real refund and confirms it with an
    independent request.
  - The money barriers are structural: the session opener takes **no amount** (a units bug is what
    turns $1 into $100), phase A's idempotency key is fixed so a retry cannot charge twice, phase B
    uses production's own `refund_dedupe_key`, the refund is re-checked in a `finally` through an
    **independent** client, and if it cannot be completed the run prints the **charge id** with
    manual-refund instructions rather than failing quietly.
  - The cleanup verifies the **whole** capture came back: it sums only `succeeded` refunds (a
    `pending` one is in flight, not done), issues any remainder on its own derived key, and polls
    to a terminal state. A partial or pending refund raises the alarm instead of passing.
  - The **evidence block** is composed only after the fact it certifies has been established — the
    refund settled to a terminal `succeeded`, the checkout session actually expired. That block is
    what gets pasted into `live_verifications()`, so certifying ahead of the measurement would put
    a false record into the very register the money guard reads.
  - Phase A validates amount, currency, mode and status after creating the session and **expires it
    on any failure**: a payable invitation is left standing only once it has passed. The pay-URL it
    publishes is Stripe's own, checked against the gateway's.
  - **Phase B refunds only what phase A opened.** The session is marked at creation (its return URL
    carries the harness name and the run id; Stripe echoes it back as `success_url`), and phase B
    demands that mark before resolving the PaymentIntent. The account this runs against holds real
    customer payments and a $1 amount identifies nothing, so a mistyped, stale or hostile session
    id is refused rather than refunded.
- **Each verification records the `ProviderMode` it was gathered in, and only `LIVE` authorises a
  live credential.** Test mode is a different backend — different keys, no card networks, no money —
  so a free test-mode round-trip proves the transport and buys nothing the money guard will spend.
  Without this, the cheap and obvious way to "verify" would have paid for real money.
- **The credential TYPE check no longer expires when verification completes.** It used to ride on
  the test-mode prefix, so a fully verified provider had nothing checking `secret_key` at all — a
  truncated paste or a key from another account stored unexamined, at exactly the moment real money
  starts moving. `credential_key_families()` is permanent and separate; only the restriction to the
  TEST variant relaxes. A value that is not a recognisable key is now `UnrecognisedCredentialError`,
  reported as itself rather than as a mode problem.
- **Each verification names the implementation it exercised** (`implementation_fingerprint`, a hash
  of the gateway method's source). Editing `StripeGateway.refund` invalidates that operation's
  verification and demands a fresh run — otherwise the register would go on saying "verified" about
  code nobody has ever run.
  - **That comparison runs in the DOOR, not only in the suite.** It used to live in
    `tests/test_credential_mode_guard.py` alone, because `services` cannot import `integrations` —
    so a rewritten gateway method expired its verification in CI while `verified_operations()`, the
    function the credential door consults, went on authorising a live key against an implementation
    nobody had ever exercised. The evidence expired in the tests and stayed valid in production. The
    dependency is now inverted rather than dropped: `verified_operations()`,
    `unverified_operations()`, `required_test_mode_prefixes()` and `store_credential()` take
    `current_implementations`, **with no default**, and `cli.run_credentials_set` — the operator's
    own command — supplies `integrations.money.current_gateway_implementations(provider)`. Every way
    of getting that argument wrong (empty, partial) makes the door *stricter*, never laxer. The
    refusal now distinguishes *nobody ran this*, *somebody ran it in TEST mode* and *somebody ran it
    against code that has since changed*, so an operator is not sent to re-run what they just ran —
    which, for `refund`, would mean another real charge.
  - **The fingerprint is of the gateway's whole MODULE, not of the method.** It hashed
    `inspect.getsource(method)`, and `StripeGateway.refund` is four lines that delegate: the request
    is built by `self._client` out of `_STRIPE_API_BASE` (the URL the run supposedly spoke to) and
    `_HTTP_TIMEOUT`. Repointing the base URL left the method's text — and therefore the fingerprint
    — byte-identical, so the verification stayed valid for code that now talks somewhere else.
    Mercado Pago's gateway delegates the same way. The precise alternative (walk the AST for the
    method's transitive closure) was rejected because an incomplete analysis fails as a false "still
    verified", which is the expensive direction; a hash of the file cannot be incomplete about the
    file. The cost is that an edit to either half of a gateway module invalidates both of its
    operations. What the hash still does not cover — other modules, `httpx`, an injected transport,
    the runtime environment — is written down in the function itself.
- **The evidence is re-checked when a credential is USED, not only when it is stored.** The door
  answers once, on the day the key is typed; a gateway edited afterwards kept moving real money on a
  verification that no longer described it, and nothing re-asked. Every charge and every refund now
  re-asks against the fingerprints of the code in that process — and the answer is **asymmetric by
  the money's direction**, decided by an exhaustive `blocks_on_stale_evidence` so a third operation
  cannot inherit a policy by omission:
  - **taking** payment through unexercised code is **refused** (the public 402, alerted). That
    failure is the silent one — every status code says success — and the refusal costs new bookings,
    is visible at once, and clears at zero cost by re-running the free checkout harness;
  - **returning** payment is **never refused**, only alarmed. Blocking a refund does not prevent the
    harm it guards against; it *is* that harm — "the guest's money does not come back" — produced
    with certainty on a card already charged. An unexercised refund fails loudly (gateway raises,
    outbox retries, intent dead-letters with an alert) rather than silently.
  - Only **live** credentials are gated, so test-mode self-hosters and the whole suite are
    untouched. Every way of getting the injected fingerprints wrong is restrictive on the charging
    side and noisy on the refund side, never silent. An AST guard fails if any gateway call site
    stops consulting the gate.
- **A terminally failed refund is no longer permanently unretryable.** `refund_dedupe_key` served
  as both the outbox row's identity and the key sent to the provider — and a provider replays the
  answer it gave for a repeated idempotency key. So once a refund ended `failed`/`canceled` (the
  money did not move and never will), every retry got that same dead refund back: the guest was
  never paid back, and the retry reported success. `PaymentGateway.refund` now returns a
  `RefundOutcome` (the provider's own verdict, instead of "the HTTP call worked"), and
  `refund_idempotency_key` carries a **generation** named by the failure it follows.
  - **The generation is a function of OBSERVED STATE, never of the attempt.** A key derived from a
    counter, the clock or randomness would satisfy "retryable" while issuing a *second* refund on
    every ordinary crash-retry — trading a bug that never returns the money for one that returns it
    twice. While the state is unchanged the key is unchanged, so a retry replays.
  - **One new generation per drain, not a chain**, and a second terminal failure raises instead of
    marking the payment refunded: the outbox retries (creating nothing new — the keys are stable)
    and its ceiling dead-letters it with an alert for a human. A card that rejects refunds needs a
    person, not a hundred attempts.
  - An unrecognised provider status reads as **not** terminally failed, in both adapters: the
    consequence of calling it a failure is another refund, so the safe direction is silence.
- **A refund still in flight is no longer recorded as money returned.** The fix above left
  `RefundOutcome` **binary** — terminally failed, or not — and the "not" absorbed two facts that are
  not the same: *the money went back* and *it has not gone back yet*. So a `pending`,
  `requires_action` or unrecognised status marked the payment `REFUNDED` while the money was still
  sitting there. ==It is the same defect one layer up, wearing the other half of the partition==: a
  failure recorded as a success, then a pending recorded as a success. The handling was not wrong;
  the partition was.
  - `RefundStatus` models **three** states (`succeeded`, `pending`, `failed`) and the runner
    dispatches on them with `assert_never`, so a fourth cannot arrive quietly — the same lock the
    money's *direction* already had.
  - `PaymentStatus.REFUNDED` is set **only** on a terminal success. A pending raises
    `RefundNotSettledError` so the outbox retries with backoff — and that retry is a real route to
    terminal, not a busy-wait: `charge.refunded` lands independently and marks the payment refunded,
    so the next attempt short-circuits on the status re-check and completes; a refund that never
    settles exhausts the attempts and dead-letters with an alert. Both directions end somewhere.
  - An **unknown** provider status reads as `pending` in both adapters, because that is the only
    reading that claims nothing: calling it a failure issues a second refund, calling it a success
    records money that has not moved.
  - The live harness had the same binary read in its **evidence** path (`not terminally_failed`) and
    would have certified a pending refund into `live_verifications()`; it now requires
    `RefundStatus.SUCCEEDED`.
- **A pending refund converges by ASKING, not only by waiting for a webhook.** `charge.refunded`
  was the single route to convergence on the success side, so one lost delivery, one signature that
  failed to verify, or a settlement slower than six attempts of backoff would dead-letter a refund
  that had **succeeded** — no money lost, but a human summoned to something that was fine, and
  invited to refund it again. `PaymentGateway.refund_status` reads one refund back (a GET: it moves
  no money) and the runner consults it whenever an answer is pending, so the outbox's retry is a
  real poll. The webhook stays the fast path; this is the net under it.
  - The gateway protocol now holds **two kinds** of call, and the anti-omission lock knows it: money
    operations (`GatewayOperation`, each of which must be verified against the real provider before
    a live credential may be stored) and declared **reads** (`read_only_gateway_methods`). The suite
    asserts the protocol's coroutines are exactly those two sets, disjoint — so a new gateway method
    is unclassified until somebody says which it is, and a read carries no verification burden
    because its worst failure is a refund that settles late.
- **The live harness's order guards are parsed, not grepped.** They pinned the money path's
  invariants — nothing that can abort outside the `try` that refunds, no creation outside its
  recovery, provenance before the refund — with `in source` / `source.index(...)`, a substring
  search over the whole file. ==A mention in a comment or a string literal satisfied them exactly as
  well as a call==, so a guard could stay green over an invariant that had been deleted. They now
  locate real `ast.Call` nodes inside the specific function and compare their positions; the alarm
  guard requires its text inside an executable `pytest.fail`, not merely somewhere in the file.
- **The live harness's run id is validated by shape.** It becomes part of a filename, so a separator
  or `..` would write a run's state (its nonce) outside the directory the harness owns. It is
  checked against an allowlist (`[A-Za-z0-9_-]+`) *and* the resolved path is required to stay inside
  the state directory. We generate the run id today — the check is on the **form** because the
  origin is what changes.
- **A terminal refund failure can no longer be nameless.** The new generation is derived from the
  failed refund's id, so a terminal failure without one leaves nothing to derive it from — and the
  code would have claimed a second attempt it never made. `RefundOutcome.failed()` takes a
  **non-optional** id (with the dataclass refusing the state even when built field by field), and an
  adapter that receives a nameless terminal failure raises `MalformedRefundResponseError` naming the
  payment reference, rather than issuing a refund blind.
- **The live harness's provenance mark is authenticated, not published.**
  `require_phase_a_provenance` demanded a fixed, public `success_url` prefix — every character of it
  in this repository — so any session in the account could carry it and pass the one barrier between
  the harness's $1 and the real customer invoices beside it. The mark is now an **HMAC** over the
  purpose, the run id and a per-run nonce, keyed by `AETHERCAL_LIVE_STRIPE_PROVENANCE_SECRET`
  (environment only), applied at creation through the return URL and compared with
  `hmac.compare_digest`.
  - **Two questions, both required**: the HMAC answers *did I create this?*; the session id phase A
    persisted (outside the repo, `~/.aethercal` by default) answers *is it THE one?* A mark travels
    in a URL a guest can read, so a copy of it on another session fails the id check.
  - **Without the signing key the money harness refuses to run** — a hard failure, never a skip and
    never an unauthenticated fallback.
- **A failed creation of a payable session is resolved rather than assumed away.** The live harness
  guarded everything *after* the session existed and nothing around the call that creates it: if
  Stripe processed the request and the response never landed, a live $1 invitation stood in a real
  account with no id to name it, invisible to every cleanup path. The shared creation seam now
  replays the identical request on the **same idempotency key** — Stripe returns the session it
  already made, which is expired — and if the replay fails too, the run shouts the idempotency key
  with manual-search instructions. Both harnesses create through that seam, and an AST guard pins
  the creation inside its recovery.
- **A prefix on its own is no longer accepted as a key.** The permanent type check was
  `value.startswith(prefixes)` while its own refusal promised to catch "a truncated paste" — so
  `sk_live_`, typed alone, was stored as a payment credential. `credential_key_families()` now
  returns a `KeyFamily` (prefixes + a floor on what follows + one unbroken token), which refuses the
  bare prefix, a paste truncated to a stub, and a value carrying a space, a line break or its
  surrounding quotes. The floor is deliberately far below the shortest key any of these providers
  issues: refusing a genuine key stops a business charging, while admitting a well-formed impostor
  costs a `401` and moves no money.
  - **And the refusal no longer claims what it cannot check.** It used to say it caught "a key from
    another account"; nothing local can. The message now states that limit — only an authenticated
    call to the provider decides whether a well-formed key is genuine, current or yours, and this
    door deliberately makes none.
- **The live harness's connectivity control is a fixture, so it cannot be selected around.** It was
  a test standing beside the runs it vouched for — and a sibling is not a precondition: running the
  evidence-producing test by name (`pytest <file>::<test>`) left the control uncollected, so a record
  could reach `live_verifications()` with nothing having shown the process reaches Stripe at all.
  `stripe_reachable` now demands Stripe's own `401` for a key Stripe never issued, before the body of
  any provider-touching test, and the refund harness — which had no control whatsoever — gets one in
  both phases. An offline AST guard fails if any live test omits it.
  - **Both gates now identify a "live" module by PARSING it, not by grepping for the marker.** The
    substring test counted any file *containing* `pytestmark = pytest.mark.live_provider` — including
    a guard that named the marker in order to search for it, which then classified itself as a
    provider harness and broke the neighbouring gate. Shared in `tests/live_harness_modules.py`, so
    the two gates cannot answer it differently.
- **The live refund harness guarantees the refund from the moment it can aim one.** Phase B's
  `try`/`finally` used to start *after* the currency, `paid`, amount and PaymentIntent-shape
  assertions; any of those can fail on a session a human has genuinely paid, and then the run ended
  with a real dollar on a real card and no `finally` left to send it back. The PaymentIntent is now
  resolved defensively immediately after the provenance check (which still runs first — a stranger's
  payment must never become a target), and every remaining validation moved inside the guarantee. If
  the PaymentIntent cannot be resolved on a `paid` session, the run raises a loud alarm naming the
  **session id** for a manual refund. Pinned structurally by an AST guard in
  `tests/live/test_live_harness_guardrails.py`: no assertion may sit outside that `try`.
- The live suite is the one exception to the repo-wide network guard, and the exception is an
  **allowlist**: `api.stripe.com:443` and nothing else, with SMTP and the Google API still shut. A
  marked test reaching anywhere else is refused exactly as an ordinary test would be.
- **Granularity is per operation because the two cost different things to prove.** Checkout is free
  to verify; refund is not (it needs a real charge to refund). Verifying checkout alone therefore
  does **not** open the door — a stored credential is the row `refund` will read weeks later, for a
  guest who has already paid.

**Per-business branding** — a business's booking page is now *theirs*, not the product's.

- `tenants` gains `public_name`, `logo_url`, `accent_color` and `timezone` (migration
  `0014_tenant_branding`). Existing rows keep behaving exactly as before: no logo, no colour, and
  `UTC` — the zone the booking page was already hard-coding for everybody.
- The booking page renders the business's name and logo in its header, its accent colour as the
  page's accent, and its timezone as the display zone a visitor sees before choosing their own. A
  visitor's explicit `?tz=` still wins.
- `GET /api/v1/branding` returns the branding of the business the API key belongs to. It takes no
  parameters — the business is never an input — and the SDK exposes it as `client.get_branding()`.
- The admin gains a **Branding** page. A colour must be a hex triplet, a logo URL must be `https`,
  and a timezone must be a real IANA zone; anything else is refused with a readable message and
  nothing is written.
- The booking page's `img-src` content-security policy now permits `https:` so an operator's logo
  can actually load. Without it the feature would have failed silently in the visitor's browser.

## [0.1.0] — 2026-07-13

The first published release. The booking stack is in production for its first operator; the API
contract may still change before 1.0.

### Added

**The scheduling engine** (`aethercal-core`)

- RFC 5545 recurrence expansion (`RRULE` / `EXDATE` / `RDATE`), property-tested against DST edges and
  an independent oracle. An event's `dtstart` is naive wall-time with its timezone stored beside it,
  so a weekly series keeps its local hour across a DST change.
- Availability from a weekly schedule plus date overrides; slot computation applying duration,
  increment, buffers, minimum notice and maximum advance; conflict detection; iCalendar
  serialization.
- Pure: no I/O, no clock, no internal dependencies — enforced by import contracts in CI.

**The API and the self-host** (shipped in the container)

- API v1: event types, schedules (with date overrides), slots, bookings (book, cancel, reschedule)
  and webhook subscriptions. API-key authenticated.
- A double-booked slot is rejected with `409`, decided by the database rather than by an application
  race.
- Rescheduling creates a successor booking that inherits the calendar identity and cancels its
  predecessor.
- Signed outgoing webhooks (HMAC-SHA256 over the canonical body), an SSRF egress guard before every
  delivery, exponential backoff, and a dead-letter state.
- Transactional email with an `.ics` invitation. Missing SMTP or Google configuration degrades
  gracefully instead of failing the boot.
- Guest self-service: the cancel and reschedule links in the confirmation email carry a signed guest
  token, so a guest can act on their own booking without an API key — and cannot touch anyone
  else's.
- Notification workflows: a durable step lifecycle on the transactional outbox, with the 24-hour
  reminder seeded per tenant as a rule and delivered over email. A step is materialised, voided or
  skipped by an exhaustive transition table, so a rescheduled booking cannot still fire its
  predecessor's follow-up.
- No-show: a booking can be marked `no_show`. It keeps occupying its slot — the time has passed, and
  releasing it would permit a retroactive booking over it.
- Google Calendar **busy-check**: a real busy block on a host's calendar removes the slot, and an
  unreachable calendar withholds that host's slots rather than risk a double-booking.
- A public bilingual (ES/EN) booking page, an embeddable widget, and a minimal Reflex admin.
- Self-host as one container plus PostgreSQL, configured by environment variables, migrations run on
  boot.

**The SDK** (`aethercal-client`)

- Synchronous client: `list_event_types`, `get_slots`, `create_booking`, `cancel_booking`,
  `reschedule_booking`, `health`, `ping`. Typed responses; transport failures surface as
  `AetherCalTransportError` instead of leaking `httpx` exceptions.
- Asynchronous client: `health` and `ping` only, for now.

**The calendar component** (`aethercal-ui`, `@aethercal/calendar-react`, `@aethercal/calendar-core`)

- Five views: month, week, day, list, and a resource timeline (resources as rows, time across).
- Drag and resize with optimistic reconciliation, rolling back on rejection or timeout.
- Four theme presets plus `--ac-*` token overrides; `en` / `es` message packs with per-string
  overrides.
- Keyboard parity for every pointer gesture, with exactly one tab stop per grid.
- The React layer never bundles React (peer dependency); the headless core never imports it at all.

**Documentation**

- A self-host quickstart that ends in a real booking, an SDK guide, a component guide, a webhooks
  guide that publishes the **at-least-once** delivery contract, and a Spanish locale in `docs/es/`.
- Runnable examples in `examples/`.

### Notes for integrators

- **Webhook delivery is at-least-once, not exactly-once.** A crash between "your endpoint accepted"
  and "we committed that it accepted" replays the effect, so a handler *will* see duplicates: key on
  the booking `id` plus the `event`, and make a repeat a no-op. Delivery order is not guaranteed
  either. See [docs/webhooks.md](docs/webhooks.md).
- **A booking does not create an event in the host's Google Calendar.** The busy-check reads the
  calendar; the write-back leg is not connected yet.
- **Notification workflows run over email only.** The engine, its migration and the seeded 24-hour
  reminder are live, but WhatsApp and SMS are declared in the `Channel` enum with no adapter behind
  them, and there is no workflow CRUD API yet — the rules are seeded, not editable.
- **No-show emits no webhook.** A booking can be marked `no_show`, but the outgoing events remain
  `booking.created`, `booking.cancelled` and `booking.rescheduled`.

[Unreleased]: https://github.com/ernestodota2011/aethercal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ernestodota2011/aethercal/releases/tag/v0.1.0
