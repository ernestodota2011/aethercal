"""OAuth for the Google Calendar spike (F0-11): installed-app (loopback) flow with token caching.

The Desktop OAuth client is read from the environment -- never hardcoded, never committed:
``AETHERCAL_GOOGLE_CLIENT_ID`` / ``AETHERCAL_GOOGLE_CLIENT_SECRET``. First run opens a browser for
consent; later runs refresh the cached token silently. The cached token lives outside the repo.

google-auth libraries are untyped, so their objects stay behind an ``Any`` seam here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Least-privilege scopes for the spike: read busy blocks + create events (with a Meet link).
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _client_config() -> dict[str, Any]:
    try:
        client_id = os.environ["AETHERCAL_GOOGLE_CLIENT_ID"]
        client_secret = os.environ["AETHERCAL_GOOGLE_CLIENT_SECRET"]
    except KeyError as missing:  # pragma: no cover - env wiring, exercised in the live demo
        raise RuntimeError(
            "set AETHERCAL_GOOGLE_CLIENT_ID and AETHERCAL_GOOGLE_CLIENT_SECRET (agency OAuth "
            "Desktop client) before running the Google spike"
        ) from missing
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def get_credentials(
    token_path: Path,
) -> Any:  # pragma: no cover - interactive/live, not unit-tested
    """Return valid Google credentials, running the consent flow or refreshing as needed."""
    creds: Any = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
        creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
