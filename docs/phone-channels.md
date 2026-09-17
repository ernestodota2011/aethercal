# Phone channels: WhatsApp and SMS

A workflow rule can send its step over **WhatsApp** (Evolution API) or **SMS** (Twilio) instead of
email. Both are **off by default** — and "off" here simply means *no environment variables set*.
There is no separate `_ENABLED` flag.

**Read [the consent limitation](#the-consent-limitation) before you switch either of them on.** It
is not a footnote, and it is the reason this page exists.

---

## The consent limitation

> [!WARNING]
> **The phone number is typed into a public form by whoever is booking, and the FIRST message is
> therefore addressed to a number nobody has proven they own.**
>
> The booking page shows an explicit, unticked consent checkbox, and nothing is sent unless it is
> ticked. But a ticked box only proves that **somebody** ticked it. It does **not** prove that **the
> owner of that number** agreed to anything.
>
> Anyone can book an appointment, type in a stranger's phone number, tick the box on their behalf,
> and your business will send that stranger **one** verification code (C-02b) — and nothing else,
> because every later reminder waits for a code that only the owner of the number can read. The
> stranger still receives that one message **under your brand, from your number**.
>
> **A box ticked by a stranger is not consent from the owner of the number.**
>
> The per-phone and per-IP daily caps below bound the **volume** of that abuse. They do not prevent
> the **first message**, and the first message is the one that arrives.
>
> **Before you switch on a phone channel, decide whether your jurisdiction and your brand tolerate
> this.** In several jurisdictions, provable consent for automated messaging is a legal requirement
> that a self-served checkbox does not satisfy; and a spam complaint against a messaging account is
> frequently not recoverable. That decision is yours, and you should be able to make it knowing
> exactly what this product does and does not establish.

### What the product actually does

| | |
|---|---|
| Asks for a phone at all | **Only** where an *active* WhatsApp/SMS rule governs that event type. No phone rule → no phone field, and no personal data collected. |
| The consent checkbox | Explicit, **never** pre-ticked, **never** required. Booking without a phone always works. |
| Records it | `bookings.guest_phone_consent_at` — the timestamp at which **the box was ticked**. |
| Proves possession | One verification code (C-02b) sent to the number; reminders and every other phone step wait for `bookings.guest_phone_verified_at`. That is what makes the checkbox's claim testable. |
| Refuses to send | No number, no ticked box, no verified stamp, or a number on the opt-out list → the step is `skipped` with its own reason (`no-phone`, `no-phone-consent`, `phone-unverified`, `phone-suppressed`) and nothing goes out. |
| Revocation | Setting `guest_phone_consent_at` back to `NULL` closes the gate again. There is no special code path: the absence of the stamp *is* the revocation. |
| Opt-out | Replying `STOP`, `BAJA`, `ALTO`, `DESUSCRIBIR`, `UNSUBSCRIBE`, `PARAR`, `DETENER`, `QUITAR` or `BLOQUEAR` to a WhatsApp reminder suppresses the number instance-wide, and it stays suppressed even through guest erasure. |
| Answers back | "1" (confirm attendance) and "2" (cancel) are **acknowledged** over WhatsApp; the acknowledgement is queued like every other outbound, so it passes consent, the opt-out list and the daily caps. An **opt-out is never acknowledged** — the silence is the confirmation. |
| Bounds the damage | Per-phone and per-IP daily caps, which a channel **refuses to start without**. |

### What it does NOT do — a declared gap

**A ticked consent box is still not consent from the number's owner.** The verification code closes
the loop *before the reminders*: an attacker who types a stranger's number cannot get a single
reminder delivered, because the code lands on the stranger's phone and the reminder gate waits for
it to be entered (`phone-unverified`). What the code cannot do is prevent that **one** message from
arriving, or make the checkbox itself evidence of anything other than "somebody claimed this number".

So read `guest_phone_consent_at` for exactly what it is: **a stamp that the box on the form was
ticked**. Read `guest_phone_verified_at` for exactly what it is: **a stamp that whoever could read
the code on that number asked for this booking's reminders** — a much stronger claim, and the one
the send path actually requires. Neither is verified permission *from the number's owner for your
business*: the product does not pretend otherwise anywhere — not in the schema, not in the column
comments, not in the outbox gate, not here.

---

## Whose number does a business send from?

> [!IMPORTANT]
> **Its own.** The variables below configure the **operator's** account, and on a multi-business
> instance that is *not* what a business's reminders go out on.
>
> A WhatsApp or Twilio account is an **identity**, not a pipe: there is no per-message `From`, so
> the number is what the guest sees and replies to. A business sends on the credential it brought
> (`aethercal-admin credentials set --provider whatsapp`), or that channel is **off for it** and its
> steps are `skipped` with a reason.
>
> On a **single-business self-host** the operator is the business, so
> `AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=true` makes the account below the one it sends from. It is
> off by default and warned about at boot. See
> [BYOK](byok-credentials.md#the-rule-and-where-it-is-asymmetric).

The **caps** below are different: they stay the operator's policy, and they bound a business's own
sender too. The recipient comes from the operator's public form, and that harm does not change owner
along with the API key. A business that brings a credential to an instance with no caps declared
keeps the channel off — the worker logs which variables would turn it on.

## Enabling WhatsApp (Evolution API)

Evolution is self-hostable, so this path is open to a self-hoster with no commercial account.

```bash
# The base URL is OPERATOR configuration. It is read only from the environment and is never
# derived from inbound data.
AETHERCAL_WHATSAPP_BASE_URL=https://evolution.example.com
AETHERCAL_WHATSAPP_INSTANCE=my-instance
AETHERCAL_WHATSAPP_API_KEY=…

# Both caps are REQUIRED. The channel refuses to activate with credentials but no caps.
# These bound EVERY business on the instance, including one sending on its own account.
AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE=3
AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP=50

# Single-business self-hosts only: lend the account above to a business that brought none.
# Off by default. On a multi-business instance this messages guests from a number they do not own.
AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY=false
```

## Enabling SMS (Twilio)

```bash
AETHERCAL_SMS_ACCOUNT_SID=…
AETHERCAL_SMS_AUTH_TOKEN=…
AETHERCAL_SMS_FROM_NUMBER=+13055551234
AETHERCAL_SMS_BASE_URL=https://api.twilio.com   # optional; operator configuration only

AETHERCAL_SMS_DAILY_CAP_PER_PHONE=3
AETHERCAL_SMS_DAILY_CAP_PER_IP=50
```

> [!NOTE]
> The SMS adapter is written against Twilio's documented API and is covered by contract tests, but
> it has **never been exercised against a live Twilio account**. It ships unverified-live. This line
> exists so that you know it before you depend on it.

## The caps are fail-closed, on purpose

A phone channel **will not activate** with credentials but no caps: half-configured is treated as an
error, loudly, rather than as "mostly configured".

- **`DAILY_CAP_PER_PHONE`** — how many messages **one number** may receive per day. This is the
  protection for the stranger whose number somebody typed into your booking form. Keep it low.
- **`DAILY_CAP_PER_IP`** — how many messages one source IP may cause per day. This is the protection
  against somebody scripting your form.

Silence means "off". Anything else is loud.

## Which provider answers are "permanent", and why it matters

Every phone provider's answer is classified by **one rule** (`integrations/messaging/status.py`):

- **Permanent** — a retry cannot fix it, so the step is retired with its reason instead of burning
  six backoff attempts: `400` (malformed payload / invalid number), `401`/`403` (credentials, or a
  recipient the provider refuses), `404` (unknown instance or account), `405`, `406`, `410`, `415`,
  `422`. A number that is not on WhatsApp answers one of these, which is why the OTP flow can fall
  back to SMS **once** on it.
- **Transient** — everything else, *including every status nobody has classified yet*: 429, 5xx,
  408, 425. The step RETRIES with backoff. The asymmetry is deliberate: a needless retry costs a
  duplicate message (visible, recoverable); a needless retirement costs **the message** (invisible,
  unrecoverable).
- **Unknown outcome** — the request left this machine and the answer was lost (a read timeout after
  the write, an aborted call). The intent is **parked, never re-sent blind**, because it may already
  be on the guest's phone. A human resolves it: `aethercal-admin outbox resolve-unknown`.

## Opting out

A guest can stop the messages at any time by replying to a WhatsApp reminder:

```
STOP · BAJA · ALTO · DESUSCRIBIR · UNSUBSCRIBE · PARAR · DETENER · QUITAR · BLOQUEAR
```

> [!important] An opt-out is **never** acknowledged — and that is the feature
> A "you have been unsubscribed" message would be the first message sent to a number that just
> asked for silence. The suppression taking effect IS the confirmation, and the send path would
> refuse the acknowledgement anyway. The other two answers a guest can give — "1" (confirm) and "2"
> (cancel) — are acknowledged over WhatsApp, through the same belt: consent, the opt-out list, the
> daily caps and the ledger.

The reply writes the phone into the **instance-level opt-out list** (`phone_suppressions`), keyed by
`HMAC(AETHERCAL_SUPPRESSION_KEY, phone)`, and the send path checks that list **before** the channel,
the template and the caps: a suppressed number is `skipped` with `phone-suppressed`, and nothing goes
out. The suppression is written regardless of consent, and it is deliberately **not** removed by
guest erasure — an erasure must not reactivate messaging to somebody who asked to be left alone.

```bash
# REQUIRED whenever AETHERCAL_PUBLIC_API_ENABLED is on (the app refuses to boot without it), because
# that is the door the booking page and the reminders speak through.
# Generate one and keep it STABLE: rotating it re-keys the list, and every number already suppressed
# becomes invisible again. It is deliberately not derived from AETHERCAL_APP_SECRET.
AETHERCAL_SUPPRESSION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

## Templates

A WhatsApp/SMS step renders its body from a `workflow_templates` row for that channel, kind and
locale. A rule whose phone step has no template is **refused at authoring time** — otherwise it
would reach the drain, skip with `no-template-renderer`, and message nobody, while the rule went on
reading `active: true`.

Template bodies are strict variable substitution over a fixed allowlist: no Jinja, no expression
evaluation. Guest-supplied values are escaped per channel and length-capped, because the guest's own
text is rendered into a message that carries your brand.

## Erasing a guest's phone

`guest_phone`, `guest_phone_consent_at` and `guest_phone_verified_at` are all covered by guest
erasure (RNF-8): the stamps go with the number they were given for, because a permission attached to
a person who no longer exists is a permission nobody can withdraw.

```bash
aethercal-admin guest purge --tenant <slug> --email <addr>
```

`--tenant` is mandatory and the command fails without it: one person can be a guest of several
businesses on the same instance, and an unscoped purge would erase them from businesses that never
received the request.

The **opt-out list is the one thing erasure does not touch**, on purpose: it holds no name, no
booking and no tenant, only the HMAC of a number that asked not to be messaged — and forgetting it
would start messaging them again.
