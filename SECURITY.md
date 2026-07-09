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
