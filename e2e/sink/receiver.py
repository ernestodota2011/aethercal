"""A webhook sink for the end-to-end suite: capture the delivery, byte for byte, and hand it back.

The suite must verify the HMAC the server computed over the *exact* bytes it POSTed
(``webhooks/signing.py`` signs a canonical JSON serialisation), so this sink stores the raw body
(base64) and the raw headers, and never re-serialises anything. It answers ``200`` so the delivery
worker marks the intent ``delivered`` — the happy path we are asserting.

Deliberately stdlib-only (no pip install inside the container) and deliberately dumb: it is a piece
of test tackle, not a product. It is reachable only on the private compose network of the E2E stack.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

#: Refuse a body larger than this — a sink is not a place to buffer someone's mistake.
MAX_BODY_BYTES = 1_000_000

_LOCK = threading.Lock()
_CAPTURED: list[dict[str, Any]] = []


class Handler(BaseHTTPRequestHandler):
    """``POST`` → captured. ``GET /_captured`` → the list. ``DELETE /_captured`` → clear it."""

    protocol_version: ClassVar[str] = "HTTP/1.1"

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/_health":
            self._respond(200, {"status": "ok"})
            return
        if self.path == "/_captured":
            with _LOCK:
                self._respond(200, {"captured": list(_CAPTURED)})
            return
        self._respond(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        if self.path != "/_captured":
            self._respond(404, {"error": "not found"})
            return
        with _LOCK:
            _CAPTURED.clear()
        self._respond(200, {"captured": 0})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "body too large"})
            return
        body = self.rfile.read(length) if length > 0 else b""
        entry: dict[str, Any] = {
            "received_at": datetime.now(UTC).isoformat(),
            "path": self.path,
            # Header names are lower-cased so the suite can look one up without guessing its case.
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
        with _LOCK:
            _CAPTURED.append(entry)
        self._respond(200, {"ok": True})

    def log_message(self, format: str, *args: Any) -> None:
        # One line per delivery; the suite's own polling would otherwise drown the log.
        if self.command == "POST":
            super().log_message(format, *args)


def main() -> None:
    port = int(os.environ.get("SINK_PORT", "9099"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"webhook sink listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
