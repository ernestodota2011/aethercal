# CalDAV busy-check — a second calendar provider, read-only

AetherCal reads a host's **busy time** so it never offers a slot the host is already booked in
(RF-04/RF-12/RF-13). Until now that busy time could only come from **Google**. CalDAV adds a
**second** provider so no core function depends on a single proprietary service (**RNF-9**): a
self-hoster can feed their busy set from **any standards-compliant CalDAV server** — Nextcloud,
Radicale, Fastmail, iCloud, a corporate Zimbra.

It is **read-only by design.** CalDAV contributes freebusy and nothing else — AetherCal **never**
writes an event to a CalDAV calendar. There is deliberately no create / delete / reschedule path for
it; booking events are still written only to Google (or to no external calendar at all). A host
whose *only* connection is CalDAV books normally: their internal bookings block them, their CalDAV
freebusy blocks them, and nothing is written back.

---

## How it fits the busy read

`read_busy` unions the busy sets of **every** active connection a host has, across **both**
providers (`BUSY_PROVIDERS = google, caldav`). The RF-13 safe-degradation rules are identical for
CalDAV and Google:

| Situation | Result |
|---|---|
| Cache covers the window and is time-fresh | `FRESH` from cache — no server is contacted |
| Cache is stale/uncovered, refresh **succeeds** | `FRESH`, unioned with the other connections |
| Refresh **fails** but the prior cache fully covers the window | `STALE` — the last-known copy is served, slots may still be offered |
| Refresh **fails** with partial/absent coverage | `UNAVAILABLE` — the whole host's slots are refused |

**Unknown is never free.** One connection we cannot read — of *either* provider — makes the whole
host `UNAVAILABLE` rather than presenting an incomplete busy set as complete. A malformed freebusy
response, or one missing its `VFREEBUSY` component, raises rather than reading as "all free" — that
is exactly how a double-booking would slip through.

The background scheduler already refreshes **every** active connection regardless of provider, so a
CalDAV connection's cache is kept warm the same way Google's is.

---

## Connecting a CalDAV calendar

Use the admin CLI. The **app-password is read from the environment, never a CLI flag** — a
`--password` option would leak the secret into `ps`, the shell history and the terminal scrollback.

```bash
AETHERCAL_CALDAV_APP_PASSWORD='<app-password>' \
  aethercal-admin connect-caldav \
    --tenant-slug   acme \
    --user-email    host@acme.test \
    --server-url    https://cloud.example \
    --username      host \
    --calendar-url  https://cloud.example/remote.php/dav/calendars/host/personal/
```

- **`--server-url`** — the base URL of the CalDAV server.
- **`--username`** — the CalDAV account identifier.
- **`AETHERCAL_CALDAV_APP_PASSWORD`** — use a provider **app-password** (a scoped, revocable token),
  never the account's real password.
- **`--calendar-url`** — the URL of the calendar **collection** whose freebusy is read. It is stored
  as a read-only busy link, never as a booking target.

The `{server_url, username, password}` triple is Fernet-encrypted at rest in the existing
`external_connections.encrypted_credentials` column (the same seam Google's OAuth token uses — **no
new column and no migration**); the calendar URL is stored on an `external_calendar_links` row
flagged `busy=true, is_booking_target=false`. Re-running the command (an app-password rotation)
updates the ciphertext in place and does not pile up rows.

---

## How the busy is queried (RFC 4791 §7.10)

AetherCal issues a **`free-busy-query` REPORT** against the calendar collection and parses the
`VFREEBUSY` document the server answers with. The server expands recurrences and merges overlaps and
hands back the busy periods already computed, so AetherCal never has to reason about `RRULE` — the
correct-by-construction tool for a busy-check (a raw `calendar-query` of `VEVENT`s would not be).

The request shaping and the `VFREEBUSY` → `TimeInterval` mapping are pure, unit-tested transforms
(`integrations/caldav/parse.py`); the live HTTP transport is behind an `Any` seam
(`integrations/caldav/client.py`), so the whole busy-read is driven offline by a fake exactly as the
Google integration is.

---

## Verifying it live (devops, Nextcloud CT201)

The offline contract tests (`tests/test_caldav_freebusy.py`, `tests/test_calendars_caldav.py`) run
in CI **without a network** and pin the parsing, the provider dispatch, the union, and the
degradation. They do **not** exercise a real server — the live HTTP transport is marked
`# pragma: no cover - live`.

The one-time live smoke check against the agency Nextcloud (**CT 201**) is run by **devops** with a
real app-password (which is not available in the build environment):

1. Create a dedicated app-password for a test host on Nextcloud (Settings → Security).
2. `connect-caldav` that host against `https://drive.aetherlogik.com` with its personal calendar URL.
3. Put a known busy block on that calendar for the next week.
4. Run one busy-refresh tick and confirm the block appears in the host's busy set, and that pulling
   the app-password (401) degrades the host to `UNAVAILABLE` rather than "free".

This is a client-of-agency Nextcloud (agency infra, `192.168.1.x`) — **no client data or client
credentials** are involved.
