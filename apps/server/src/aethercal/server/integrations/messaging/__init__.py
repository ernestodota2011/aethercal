"""Shared machinery for the PHONE channels (WhatsApp, SMS).

The phone channels differ from email in one way that changes everything about their design: their
recipient is **untrusted input**. An email address is typed by the person who owns the inbox they
are asking us to write to; a phone number is typed into a public form by whoever is at the keyboard,
and it can be a stranger's. So these channels carry a guard (:mod:`.guard`) that email does not
need, and they are FAIL-CLOSED — they refuse to activate without their caps.
"""

from __future__ import annotations
