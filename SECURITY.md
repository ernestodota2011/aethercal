# Security Policy

## Supported versions

AetherCal is in pre-alpha. Until the first tagged release, only the `main` branch is supported.

## Reporting a vulnerability

Please report security issues **privately** through GitHub's **"Report a vulnerability"** button
(Security → Advisories) on this repository — **not** in a public issue, pull request, or discussion.
That channel is private to the maintainers and lets us collaborate on a fix and, when it is ready,
publish a coordinated advisory. If you cannot use it, open a normal issue that says only *"security —
please open a private channel"*, with **no details**, and we will reach out.

A useful report includes: the affected component and version or commit, a description of the impact,
and the smallest set of steps (or a proof of concept) that reproduces it.

**What to expect:**

- **Acknowledgement** within a few business days.
- **An initial assessment** — accepted / needs-more-info / out of scope, with reasoning — shortly
  after, and updates as the fix progresses.
- **Coordinated disclosure.** Please give us reasonable time to ship a fix before disclosing
  publicly. We will agree a disclosure date with you and publish an advisory (crediting you by name
  or handle, unless you prefer to stay anonymous) when the fix is released.

AetherCal is pre-alpha and has no bug-bounty program; there is no monetary reward, only credit.

## Scope

**In scope** — anything that lets someone cross a boundary the design promises to hold:

- The server (API, admin) and the public booking page, as built from this repository.
- The self-host artifact as documented — the deploy image, `docker-compose.yml`, and
  `provision_roles.sql`.
- Cross-business isolation failures (one business reading or writing another's data despite
  PostgreSQL row-level security), authentication/authorization bypass, guest-token forgery, secret
  or credential disclosure, injection, and SSRF beyond the configured egress allowlist.

**Out of scope:**

- Vulnerabilities in third-party dependencies — report those upstream (a dependency advisory that
  affects us is welcome, but the fix usually lives in the dependency).
- Anything that requires the **instance operator** to be malicious. The operator is trusted by
  design: they hold `AETHERCAL_APP_SECRET` and can decrypt any business's stored credentials (see
  [Stored credentials](#stored-credentials-what-the-encryption-protects-and-what-it-does-not)). "The
  operator can read the data on their own instance" is the documented threat model, not a bug.
- Findings that only reproduce under an insecure configuration the docs explicitly warn against —
  e.g. exposing `/admin` publicly with no reverse-proxy rate-limiting
  ([deploy/README.md](deploy/README.md)), or an over-broad `AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS`.
- Missing security hardening with no concrete exploit, volumetric denial of service, social
  engineering, and self-XSS.

## What not to report

These are **known, documented limitations**, not vulnerabilities — they are already stated in the
docs, so a report telling us they exist does not add anything:

- **The phone number on the public booking form is not verified to belong to the guest.** Possession
  verification (an OTP or confirmation link) is a declared gap; the phone channels ship off by
  default and warn at boot. See [docs/phone-channels.md](docs/phone-channels.md).
- **Neither payment provider has been exercised against a live account**, and partial refunds are
  not modelled. See [docs/byok-credentials.md](docs/byok-credentials.md).
- **One key encrypts every business's credentials on an instance** — encryption at rest, not
  isolation from the operator (below).

Automated scanner output with no demonstrated impact is not a report we can action — please include a
concrete, reproducible finding.

## Handling of secrets

AetherCal never stores secrets in source. Instance configuration — database URLs, API keys, OAuth
client secrets, and signing keys — is provided at runtime through environment variables. Guest
links are signed tokens with expiry; API keys are stored hashed; outgoing webhooks are signed.

## Stored credentials: what the encryption protects (and what it does not)

Each business on an instance can bring its own provider credentials — payment account, SMTP relay,
WhatsApp, SMS. They are encrypted at rest with Fernet, under a key derived from the instance's single
`AETHERCAL_APP_SECRET`.

**One key encrypts the credentials of every business on the instance. That is encryption at rest, not
cryptographic isolation: whoever operates the instance can decrypt any business's credential.** It
protects against a stolen database dump, a leaked backup or a SQL-injection read — none of which
carry the app secret. It does **not** protect against the operator of the instance.

Between businesses, isolation is enforced by the database (PostgreSQL `FORCE ROW LEVEL SECURITY`), so
one business cannot read another's credentials even through a query that forgot to filter.

If you need the operator to be unable to decrypt your credentials, run your own instance. A
per-business key is not implemented, and we would rather say so than let the word "encrypted" imply
it. The full statement, and the key-rotation procedure, are in
[`docs/byok-credentials.md`](docs/byok-credentials.md).
