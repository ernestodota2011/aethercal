# F0-11 Spike B: Google Calendar (OAuth + freebusy + insert event with Meet)

**Verdict: GO.** OAuth (installed-app loopback), reading busy blocks via freebusy, and inserting an
event with a Google Meet link all work against the real agency Google account. Build the full F1-07
integration (BusyCache, incremental sync, safe degradation) on this foundation.

## What was built

- `apps/server/src/aethercal/server/integrations/google/oauth.py` — installed-app (loopback) OAuth
  flow. The Desktop client id/secret are read from the environment
  (`AETHERCAL_GOOGLE_CLIENT_ID` / `AETHERCAL_GOOGLE_CLIENT_SECRET`), never hardcoded or committed;
  the resulting token is cached **outside the repo** and refreshed silently on later runs.
  Least-privilege scopes: `calendar.readonly` (freebusy) + `calendar.events` (insert).
- `.../google/parse.py` — the **pure, unit-tested** core: `parse_freebusy(response, calendar_id)`
  maps a freebusy response into sorted UTC `aethercal-core` `TimeInterval`s and **raises** on
  per-calendar errors rather than silently returning an empty ("all-free") result; `MeetEventRequest`
  + `build_meet_event_body(request, request_id)` build an `events().insert()` body with a
  `conferenceData.createRequest` for a `hangoutsMeet` conference.
- `.../google/calendar.py` — a thin live API layer (`build_service`, `query_busy`,
  `insert_event_with_meet`, `delete_event`) with the untyped google-api-python-client contained
  behind an `Any` seam (same discipline as `aethercal.core.ical.serde`).
- `.../google/spike.py` — the live demo runner used to verify the integration by hand.
- `apps/server/tests/test_google_parse.py` — 4 unit tests pinning the freebusy mapping (Z and offset
  forms, empty calendar, error-raising) and the Meet event body. The network layer is verified by the
  demo, not by unit tests.
- The Google SDKs are declared as the `google` **extra** of `aethercal-server`
  (`google-api-python-client`, `google-auth`, `google-auth-oauthlib`); the monorepo dev venv opts in
  via the root `pyproject.toml` requesting `aethercal-server[google]`, so `uv run poe check` has them
  while a standalone `pip install aethercal-server` stays lean. Promote to a hard dependency in F1-07.

## What worked (live demo, 2026-07-09)

- OAuth consent completed against the real agency Google account; the token was cached and is
  reusable.
- freebusy for the next 7 days was queried and parsed cleanly (0 busy blocks at run time — the
  mapping and sort ran without error). It was run against the **dedicated agency "Aetherlogik"
  calendar** (owner access), kept isolated from the account's personal `primary` calendar; the
  target calendar is deployment config (`AETHERCAL_GOOGLE_CALENDAR_ID`, stored outside the repo).
- An event was inserted with `conferenceDataVersion=1`; Google returned a `hangoutLink`
  (`https://meet.google.com/xxx-xxxx-xxx`) and a `video` conference entry point.
- The throwaway event was then deleted, leaving the calendar clean.

(Real account identifiers, event ids and live links are intentionally omitted from this public repo.)

## Design decisions

- **Pure/live split.** The risky logic (RFC 3339 parsing, interval sorting, error handling, event
  body shaping) is pure and unit-tested; the network layer is thin and demo-verified. This is what
  makes the integration testable without live credentials in CI.
- **freebusy errors raise, never "all free".** A calendar that returns errors must not be treated as
  fully available — that would risk double-booking. This aligns with RF-13's safety stance.
- **Least-privilege scopes** and a **single source of truth for intervals** (reusing core
  `TimeInterval`) rather than a parallel type.

## Risks / F1-07 follow-ups

1. **Token storage & rotation.** The spike caches a refresh token locally. F1 needs secure
   server-side token storage, refresh handling, and a revocation path.
2. **RF-13 degradation.** F1-07 must add a BusyCache (TTL + last-known-good) so a calendar outage
   degrades safely (use the last known busy set; if none, offer no slots for that host) instead of
   failing open.
3. **Incremental sync.** Use `syncToken` (and optionally push channels) rather than re-reading full
   windows, for efficiency at scale.
4. **Quotas / backoff** and the fact that `conferenceData` creation is asynchronous and not always
   guaranteed — F1 needs retry/backoff and a no-Meet fallback.
5. **Multi-host / multiple connected calendars** (RF-30) — the spike targets a single calendar
   selected by `AETHERCAL_GOOGLE_CALENDAR_ID` (a **dedicated secondary calendar**, not `primary`).
6. **Dedicated agency account (isolation).** Today the connected account is a personal Google
   account whose *calendar* is set aside for the agency (a secondary `…@group.calendar.google.com`
   calendar, isolated from `primary`) — isolated at the calendar level, not the account level. When
   F1-07's Google path is actually wired (deferred; needs a headless OAuth flow), move the OAuth
   client + calendar to a **dedicated agency Google account** (a free Gmail is enough — no Workspace
   required). The default `AETHERCAL_GOOGLE_CALENDAR_ID` is `primary`, so a deploy that turns Google
   on **must** set the dedicated calendar id explicitly. Email is sent via Resend, never Google.
