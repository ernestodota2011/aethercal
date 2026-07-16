# Security Policy

## Supported versions

AetherCal is in pre-alpha. Until the first tagged release, only the `main` branch is supported.

## Reporting a vulnerability

Please report security issues privately through GitHub's **"Report a vulnerability"** (Security
Advisories) on this repository rather than opening a public issue. We aim to acknowledge reports
within a few business days.

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
