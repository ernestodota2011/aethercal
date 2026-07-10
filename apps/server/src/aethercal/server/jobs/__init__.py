"""Background jobs for the AetherCal server: the 24 h booking reminder (RF-10) and its scheduler.

The pure, testable job bodies live here; the live APScheduler runner (persistent Postgres jobstore)
is kept behind a typed/``Any`` seam and starts nothing at import time.
"""

from __future__ import annotations
