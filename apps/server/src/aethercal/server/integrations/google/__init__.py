"""Google Calendar integration.

F0-11 spike: OAuth (installed-app loopback) + freebusy -> busy intervals + insert an event with a
Meet link. Kept intentionally light so importing this subpackage does not pull the google client
libraries just to reach the pure transforms in ``parse``; see docs/spikes/f0-11-google-calendar.md.
"""
