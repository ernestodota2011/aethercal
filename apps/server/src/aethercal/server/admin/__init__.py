"""The single-user AetherCal admin (F1-11, RF-18).

A minimal, sessioned admin UI built with Reflex that runs **in-process**: its state handlers call
the service layer (``aethercal.server.services``) directly rather than over HTTP/the SDK. Login is a
single operator whose credentials come from the environment (``AETHERCAL_ADMIN_USERNAME`` plus a
PBKDF2 password hash in ``AETHERCAL_ADMIN_PASSWORD_HASH``) — there is no admin DB table and no
migration. The public surface is the mount helper (:func:`mount_admin`) and the testable seams
(:mod:`.passwords`, :mod:`.config`, :mod:`.auth`, :mod:`.service`).
"""

from __future__ import annotations
