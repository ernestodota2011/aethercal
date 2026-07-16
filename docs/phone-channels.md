# Phone channels: WhatsApp and SMS

A workflow rule can send its step over **WhatsApp** (Evolution API) or **SMS** (Twilio) instead of
email. Both are **off by default** — and "off" here simply means *no environment variables set*.
There is no separate `_ENABLED` flag.

**Read [the consent limitation](#the-consent-limitation) before you switch either of them on.** It
is not a footnote, and it is the reason this page exists.

---

## The consent limitation

> [!WARNING]
> **The phone number is typed into a public form by whoever is booking, and this product never
> verifies that the number belongs to them.**
>
> The booking page shows an explicit, unticked consent checkbox, and nothing is sent unless it is
> ticked. But a ticked box only proves that **somebody** ticked it. It does **not** prove that **the
> owner of that number** agreed to anything.
>
> Anyone can book an appointment, type in a stranger's phone number, tick the box on their behalf,
> and your business will send that stranger a WhatsApp or SMS message — **under your brand, from
> your number**.
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
| Refuses to send | No number, or no ticked box → the step is `skipped` with its own reason (`no-phone` / `no-phone-consent`) and nothing goes out. |
| Revocation | Setting `guest_phone_consent_at` back to `NULL` closes the gate again. There is no special code path: the absence of the stamp *is* the revocation. |
| Bounds the damage | Per-phone and per-IP daily caps, which a channel **refuses to start without**. |

### What it does NOT do — a declared gap

**It never verifies that the person booking possesses the number they typed.** There is no OTP and
no confirmation link. This is not implemented, not partially implemented, and no code path
approximates it.

So read `guest_phone_consent_at` for exactly what it is: **a stamp that the box on the form was
ticked**. It is evidence of a claim made by whoever filled in that form. It is not verified
permission from the number's owner — and the product does not pretend otherwise anywhere: not in the
schema, not in the column comment, not in the outbox gate, not here.

Closing this gap (verifying possession before the first message) is a whole feature. It is **not in
this batch**, and you should not plan around it existing.

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

## Templates

A WhatsApp/SMS step renders its body from a `workflow_templates` row for that channel, kind and
locale. A rule whose phone step has no template is **refused at authoring time** — otherwise it
would reach the drain, skip with `no-template-renderer`, and message nobody, while the rule went on
reading `active: true`.

Template bodies are strict variable substitution over a fixed allowlist: no Jinja, no expression
evaluation. Guest-supplied values are escaped per channel and length-capped, because the guest's own
text is rendered into a message that carries your brand.

## Erasing a guest's phone

`guest_phone` and `guest_phone_consent_at` are both covered by guest erasure (RNF-8):

```bash
aethercal-admin guest purge --tenant <slug> --email <addr>
```

`--tenant` is mandatory and the command fails without it: one person can be a guest of several
businesses on the same instance, and an unscoped purge would erase them from businesses that never
received the request.
