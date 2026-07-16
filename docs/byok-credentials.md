# BYOK — each business brings its own credentials

One AetherCal instance can serve several businesses. Each of them can bring **its own** provider
credentials: its own payment account, its own SMTP relay, its own WhatsApp number, its own SMS
account.

This page covers what that changes, how to configure it, **exactly what the encryption protects and
what it does not**, and how to rotate the key.

---

## The rule, and the one place it is asymmetric

A business's own credential always wins. The instance's environment variables (`AETHERCAL_SMTP_*`,
`AETHERCAL_WHATSAPP_*`, `AETHERCAL_SMS_*`) stop being *the* credential and become the instance
**default**.

Then there is money, and money is not the same:

|  | The business has its own | The business has none |
|---|---|---|
| **Sending** (SMTP, WhatsApp, SMS) | its own is used | the **instance default** is used — and with no default, the channel is simply off |
| **Payments** (Stripe, Mercado Pago) | its own is used | ❌ **it does not charge.** There is no fallback. |

**A business with no payment credential of its own does not take payments.** It does not fall back to
the instance's payment account — not once, not in a degraded mode, not "for now".

The asymmetry is deliberate, and it is not a matter of degree:

* sending a booking confirmation through the instance's SMTP relay is **infrastructure the operator
  lends**. A single-business self-hoster sets `AETHERCAL_SMTP_HOST` once and everything works, which
  is exactly what a self-hostable product should do;
* taking a guest's money into the instance operator's payment account is **charging with somebody
  else's account**. A different act, with a different name — and it does not become acceptable
  because the code path was convenient.

So the fallback does not exist on the money path: not as a flag, not as an optional argument. The
function that resolves a payment credential has no parameter you *could* pass a default to, and the
function that can fall back refuses to accept a payment provider at all. Neither is a rule somebody
has to remember. Both are the shape of the code.

---

## Configuring a credential

Credentials are set through the admin CLI, and **the secret is read from standard input** — never
from a command-line option. An `--api-key sk_live_…` flag would put a live payment key into the
process table (where any user on the box can read it with `ps`), into your shell's history file, and
into your terminal scrollback. There is no safe way to write that flag, so it does not exist.

```bash
# stripe.json → {"secret_key": "sk_live_…", "webhook_secret": "whsec_…"}
cat stripe.json | aethercal-admin credentials set --tenant-slug acme --provider stripe

aethercal-admin credentials list --tenant-slug acme        # names providers, never secrets
aethercal-admin credentials delete --tenant-slug acme --provider stripe
```

Deleting a payment credential is the **off switch**: that business stops charging.

Every field a provider needs must be present. A Stripe credential with no `webhook_secret` is
refused, for instance — it could start a charge and never verify the confirmation, which means the
guest's money leaves their card and the booking is never confirmed.

| Provider | Required fields |
|---|---|
| `stripe` | `secret_key`, `webhook_secret` |
| `mercado_pago` | `access_token`, `webhook_secret` |
| `smtp` | `host`, `from_addr` |
| `whatsapp` | `base_url`, `instance`, `api_key` |
| `sms` | `account_sid`, `auth_token`, `from_number` |

Extra fields (an SMTP port, a publishable key) are kept as given.

### Which payment providers actually work today

> [!WARNING]
> **Neither payment adapter has ever run against a live account, and Mercado Pago is not yet
> reachable at all.** Read this before you configure either one.

| Provider | Webhooks | Checkout + refund | Verified against a live account? |
|---|---|---|---|
| `stripe` | ✅ works | ✅ works | ❌ **no** — written to Stripe's documented test-mode API, covered only by contract tests |
| `mercado_pago` | ✅ adapter exists and verifies | ⚠️ **not reachable** — see below | ❌ **no** — no Mercado Pago account exists for this project; **zero real charges have ever been made** |

The Mercado Pago **adapter** is complete: its `x-signature` manifest is built exactly as Mercado
Pago's own SDKs build it, and it derives a payment's meaning by fetching `GET /v1/payments/{id}`
rather than trusting the notification body (which Mercado Pago does not sign).

What is **not** built is provider *selection*. The checkout path hardcodes Stripe
(`api/public.py` resolves `CredentialProvider.STRIPE` and records the payment row as `stripe`), and
the instance holds a single `StripeGateway`. So **configuring a `mercado_pago` credential today does
not make a business charge with Mercado Pago** — the booking flow will still ask Stripe, and a
business with only a Mercado Pago credential gets a 402. Which provider a business charges with is a
product decision that has not been made yet.

Mercado Pago also **refuses a currency whose minor unit it cannot prove** (CLP, COP and anything
else outside `ARS/BRL/MXN/PEN/USD/UYU`). The product carries money as `amount_cents`; Mercado Pago
wants a decimal `unit_price`, and that conversion is a 100× error in a currency with no minor unit.
The canonical table (`GET /currencies`) needs an account, so the adapter refuses rather than guesses.

Before either adapter is switched on for real money, it needs a run against a real account (test
mode, zero real charges): the shapes above are documented, not observed. See
`integrations/mercadopago.py` — "What is NOT proven".

---

## What the encryption protects — and what it does not

Read this section before you decide where to host.

Credentials are encrypted at rest with **Fernet** (AES-128-CBC + HMAC). The key is derived from the
instance's single `AETHERCAL_APP_SECRET`, which lives in the process environment and never in the
database.

> [!IMPORTANT]
> **One key encrypts the credentials of every business on the instance.**
>
> That is **encryption at rest. It is not cryptographic isolation.**
>
> **Whoever operates the instance can decrypt any business's credential.** They hold the app secret,
> and the key is a pure function of it.

What the encryption does buy is real, and narrower than the word may suggest:

* a stolen database dump, a leaked backup, a misconfigured read replica or a SQL-injection read is
  **useless without the app secret**;
* one business's credential is **invisible to another**. PostgreSQL row-level security is enforced on
  the table (`FORCE ROW LEVEL SECURITY`), so a query made for business B — even one that forgot its
  `WHERE tenant_id = …` — returns zero rows. That is a guarantee from the database, not a promise
  that our code is careful.

Two further facts you are entitled to, because they follow from the design:

* **the web process and the background worker both decrypt BYOK credentials in memory, in flight.**
  The web process creates the checkout session and verifies the signature of an inbound payment
  webhook; the worker executes the effects (a refund, a message). Both do so with the business bound
  and row-level security applied — a process only ever decrypts the credential of the business it is
  acting for at that moment — but both hold the instance key, because both must;
* the key is derived from the app secret, so **rotating the key means rotating the app secret** and
  re-encrypting everything (below).

**If you need the operator to be *unable* to decrypt your credentials, run your own instance.** That
is the honest answer and it is a real one: AetherCal is self-hostable, and a single-business instance
is a supported deployment. A per-business key — so that one business's credentials cannot be
decrypted with another's, and the operator cannot decrypt at all — is **not implemented**. It is
recorded as future work rather than implied by silence.

We would rather say this plainly than let the word "encrypted" do work it has not earned.
**A product that promises more isolation than it delivers is worse than one that is honest about
what it has.**

---

## Rotating the key

Rotating the encryption key means rotating `AETHERCAL_APP_SECRET` and re-encrypting every stored
secret: the BYOK credentials, the webhook subscriber secrets, and the connected-calendar tokens.

```bash
export AETHERCAL_APP_SECRET=<the NEW secret>
export AETHERCAL_PREVIOUS_APP_SECRET=<the secret being retired>

aethercal-admin credentials rotate-key
# rotated 7 row(s): external_connections.encrypted_credentials=2 …

unset AETHERCAL_PREVIOUS_APP_SECRET   # it opens nothing any more
```

What the command guarantees:

* **it finds every encrypted column.** The list is derived from the schema rather than written down,
  so a column added by a future release is rotated on the day it lands. An encrypted column that
  failed to declare itself breaks the build instead of being silently skipped — a skipped column is
  data that becomes unreadable the moment you retire the old secret;
* **all of the rows, or none of them.** It runs in one transaction. A row that decrypts under neither
  key stops the rotation and rolls it back, naming the table, the column and the row's id — never its
  contents. Half a rotation is a state nobody can describe, so it is not a state you can reach;
* **it is resumable.** A row already on the new key needs nothing, so an interrupted rotation is
  finished by running the command again;
* **it prints counts, never secrets.** No plaintext is returned, logged, or written anywhere;
* **it refuses to run as the wrong database role.** Under row-level security, a rotation on the
  application role would read *zero rows*, rewrite nothing, and report success — after which retiring
  the old secret would destroy every credential on the instance. It runs as the owner, and it checks
  that it is.

Set the new secret and the previous one to the *same* value and the process refuses to start. That is
not a rotation: it is a re-encryption under the key you believe you have just retired.

---

## Where the code lives

| Concern | Module |
|---|---|
| Precedence, and the money asymmetry | `services/tenant_credentials.py` |
| Key rotation | `services/key_rotation.py` |
| Which columns hold ciphertext | `db/encrypted.py` |
| Key derivation | `crypto.py` |
| The table and its row-level security | migration `0010_tenant_credentials` |
