"""CalDAV busy-check integration (C-03): a SECOND freebusy provider, READ-ONLY.

CalDAV lets a host contribute their busy time from ANY standards-compliant calendar server
(Nextcloud, Radicale, Fastmail, iCloud, a corporate Zimbra) instead of only Google -- which is how
AetherCal breaks the Google monoculture (RNF-9: no core function may depend on a single proprietary
provider). It is READ-ONLY by design: it feeds the busy set that prevents double-booking
(RF-04/RF-12/RF-13) and NEVER writes an event -- there is deliberately no create/delete/reschedule
path for CalDAV (that stays Google's).

Kept intentionally light so importing this subpackage does not pull an HTTP client just to reach the
pure transforms in ``parse``; the live transport lives behind the ``Any`` seam in ``client`` exactly
as the Google integration keeps googleapiclient behind its own. See docs/caldav-busy-check.md.
"""
