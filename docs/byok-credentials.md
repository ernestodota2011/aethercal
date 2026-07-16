# BYOK — each business brings its own credentials

One AetherCal instance can serve several businesses. Each of them can bring **its own** provider
credentials: its own payment account, its own SMTP relay, its own WhatsApp number, its own SMS
account.

This page covers what that changes, how to configure it, **exactly what the encryption protects and
what it does not**, and how to rotate the key.

---

## The rule, and where it is asymmetric

A business's own credential always wins. The instance's environment variables (`AETHERCAL_SMTP_*`,
`AETHERCAL_WHATSAPP_*`, `AETHERCAL_SMS_*`) stop being *the* credential and become the instance
**default** — wherever a default is a legitimate thing to have at all.

There are **three** answers to "the business has none", not two:

|  | The business has its own | The business has none |
|---|---|---|
| **Email** (SMTP) | its own is used | the **instance relay** is used — with no relay configured, no email goes out |
| **Phone** (WhatsApp, SMS) | its own is used | ⚠️ **the channel is off for that business.** Its reminders there are *skipped*, with a reason. The operator's number is **not** lent unless they declare it (below) |
| **Payments** (Stripe, Mercado Pago) | its own is used | ❌ **it does not charge.** No fallback, and no flag either. |

### Why email and WhatsApp do not get the same answer

Both are "sending". They are not the same act, and the difference is visible in the message itself:

* an **SMTP relay is a pipe.** The identity travels per message, in the `From` header — the sender
  stamps its own address only when the message does not already carry one. A business's mail goes
  through the operator's relay **as the business**. That is infrastructure the operator lends, and a
  single-business self-hoster who sets `AETHERCAL_SMTP_HOST` once is entitled to have it keep
  working, which is exactly what a self-hostable product should do;
* a **WhatsApp or Twilio account is an identity.** There is no per-message `From` to stamp: the
  number *is* what the guest sees, and what they reply to. Lending it does not put the business's
  message through the operator's pipe — **it sends the message as the operator**, to a guest who
  then replies to a company that has never heard of them.

So a business's phone reminders leave on its own number, or they do not leave.

> [!IMPORTANT]
> **This is a behaviour change.** Before, a business with no WhatsApp credential sent from the
> instance's number, silently. Now those steps are `skipped` — visible in the outbox row, in the
> worker's log, and in the `outcome="skipped"` counter at `/metrics`. If you were relying on the old
> behaviour, either give each business its own credential or read the next section.

### Lending the operator's number (single-business self-hosts)

On a one-business instance the operator *is* the business, and `AETHERCAL_WHATSAPP_*` is their own
number. Refusing to use it would be pedantry with a real cost, so:

```bash
AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=true
```

**Off by default**, and the process logs a warning at boot when it is on. Leave it off on any
instance serving more than one business: with it on, every business without its own credential
messages guests from a number none of them own.

### A business's endpoint must be on the public internet

A WhatsApp or SMS credential carries a `base_url`, and an SMTP credential carries a relay `host`.
This server connects to them. That makes it **your endpoint, our network** — so it is validated like
anything else a third party hands us and we obey:

| Rule | Why |
|---|---|
| **`https` only** | that request carries your API key in a header, and it leaves the operator's network |
| **a public address** | loopback, link-local (**including `169.254.169.254`**, the cloud metadata service), RFC1918, CGNAT and reserved ranges are all refused |
| **checked by resolved IP** | a public hostname pointing at `127.0.0.1` is refused. The destination is the address, not the string |
| **every record must pass** | one internal address in a mixed DNS answer refuses the whole target |
| **re-checked at connect** | the address is validated again when the socket opens, and pinned. A DNS answer that changes between the check and the connect (rebinding) cannot move the socket |

The same applies to a **tenant SMTP relay host** — `host: 127.0.0.1, port: 25` would relay your mail
through the operator's own MTA, which is an open relay on their IP reputation. There is no `https`
rule there (SMTP has no such scheme), and no certificate to fall back on: the address check is the
whole defence.

A credential that fails this is **not usable**: the channel stays off, and the worker logs which
field is wrong (never its value). Fix it with `credentials set` and the next attempt goes through.

> [!NOTE]
> The instance's own `AETHERCAL_WHATSAPP_BASE_URL` is **not** subject to this — that is the operator
> configuring their own instance, so a self-hoster's Evolution on `http://192.168.1.50` keeps
> working. The rule follows the value's **provenance**, not its name.
>
> The operator's private-target allowlist is deliberately **not** honoured here either. It exists so
> the operator can send *their own* webhooks into *their own* LAN; letting a business's credential
> reach it would turn that declaration into a key handed to every tenant.

### The caps stay the operator's policy

A business's own phone sender is still bounded by the instance's
`AETHERCAL_<CHANNEL>_DAILY_CAP_PER_PHONE` / `_PER_IP`. The cap protects the stranger whose number
somebody typed into the **public booking form**, and that form is the operator's surface no matter
whose API key pays the bill. The counting was always per-business; only the ceiling is instance-wide.

A business that brings a phone credential to an instance with no caps declared keeps that channel
**off**, and the worker logs which variables would turn it on. An uncapped phone channel behind a
public form is the one state this product refuses to reach.

### And money has no flag at all

**A business with no payment credential of its own does not take payments.** It does not fall back to
the instance's payment account — not once, not in a degraded mode, not "for now". Taking a guest's
money into the operator's account is **charging with somebody else's account**: a different act, with
a different name, and it does not become acceptable because the code path was convenient.

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
