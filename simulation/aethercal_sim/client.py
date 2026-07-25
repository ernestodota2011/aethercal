"""The harness's hands and its stopwatch: one HTTP call, one measured outcome.

Deliberately stdlib-only (``urllib``), like ``e2e/sink/receiver.py``. The simulation runs on a
throwaway host that is built from a template and destroyed an hour later; a harness with
dependencies is a harness that needs a working package index at exactly the wrong moment.

.. rubric:: Why nothing here raises on a non-2xx

Every other client in this repo throws on a bad status, and is right to: their callers are tests
whose subject is the happy path, and a 500 must stop them. This one's subject is the *distribution*
of outcomes — how many 409s a race produced, how many 429s the rate limiter issued, what the p95 was
while that happened. A client that raised would turn the most interesting measurements into
tracebacks, and the phase that produces the most rejections **on purpose** (the adversarial race,
where N-1 callers MUST be refused) could not run at all.

So a refusal is data: :class:`Response` carries the status and the RF-16 machine ``error`` code, and
the caller decides. Setup code, whose failures really are fatal, uses :meth:`Client.must` instead —
which is the same call with the raise put back.

.. rubric:: The stopwatch

``elapsed_ms`` is measured with :func:`time.perf_counter` around the whole call — connect, send,
wait, read. ==That is the number a guest experiences, and it is deliberately not the server's own
view of its work.== Server-side timing would exclude precisely the queueing this simulation exists
to find: a request sitting in the accept queue behind 39 others has a fast handler and a slow
booking, and only the outside measurement can tell those apart.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_TIMEOUT_SECONDS = 30.0

#: An opener with an EMPTY proxy handler. On a host that happens to export `http_proxy`, urllib
#: would route `localhost:8000` through it and the run would measure somebody's proxy — or, more
#: likely, fail in a way that reads like the app refusing connections.
#:
#: It also does no connection pooling, which is correct here: the adversarial phase needs N
#: genuinely independent, genuinely simultaneous requests, and a shared pool would serialise some of
#: them behind one another and quietly weaken the very race being tested.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True, slots=True)
class Response:
    """One completed HTTP exchange, successful or not — the harness's unit of observation."""

    status: int
    body: Any
    """Parsed JSON when the body was JSON, else ``None``."""
    text: str
    elapsed_ms: float
    error_code: str | None
    """The RF-16 envelope's machine code (``slot_unavailable``, ``day_full``, ``not_ended``, …).

    ==This, not the status, is what the simulation counts.== Several distinct refusals share HTTP
    409, and a report that said "17 conflicts" without saying how many were a taken slot versus a
    day at its cap would have described nothing. ``None`` when the body carried no envelope."""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def extract_error_code(body: Any) -> str | None:
    """Pull the machine code out of the RF-16 envelope, tolerating every shape it is served in.

    FastAPI wraps a raised ``HTTPException(detail={...})`` as ``{"detail": {"error": ..., ...}}``,
    while request-validation errors answer with ``detail`` as a LIST. Reading only the first shape
    would silently classify every 422 as "no code" and hide a harness bug that was sending malformed
    bodies — the run would report a pile of uncategorised failures and blame the server for them.
    """
    if not isinstance(body, dict):
        return None
    detail: Any = body.get("detail", body)
    if isinstance(detail, dict):
        code: Any = detail.get("error")
        return code if isinstance(code, str) else None
    if isinstance(detail, list):
        return "validation_error"
    return None


class HttpFailure(RuntimeError):
    """A setup call that had to work and did not. Never raised on the measured paths."""


class Client:
    """A thin, blocking HTTP client bound to one base URL and (optionally) one bearer token."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> Response:
        """Perform one call and return its outcome. ==Never raises for an HTTP status.==

        A transport failure (connection refused, timeout, DNS) has no status of its own, so it is
        reported as status ``0`` with the exception's text — distinguishable from every real answer
        the server can give, and therefore countable as its own error class rather than being
        mistaken for a 500 the app never sent.
        """
        url = f"{self._base}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        bearer = token if token is not None else self._token
        if bearer is not None:
            request.add_header("Authorization", f"Bearer {bearer}")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        started = time.perf_counter()
        try:
            with _OPENER.open(request, timeout=_TIMEOUT_SECONDS) as raw:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                text = raw.read().decode("utf-8", errors="replace")
                status = int(raw.status)
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            text = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return Response(0, None, f"{type(exc).__name__}: {exc}", elapsed_ms, "transport_error")

        try:
            body: Any = json.loads(text) if text else None
        except json.JSONDecodeError:
            body = None
        return Response(status, body, text, elapsed_ms, extract_error_code(body))

    def must(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> Response:
        """The same call with the raise put back — for SETUP, where a failure IS fatal.

        Provisioning a world is not the behaviour under test. If a schedule cannot be created the
        run must stop here, naming the call, rather than continue into a phase whose every
        measurement would be about an instance that was never built.
        """
        response = self.request(method, path, payload, token=token)
        if not response.ok:
            raise HttpFailure(
                f"{method} {path} → {response.status}: {response.text[:400]}\n"
                "This is a SETUP call; the simulation cannot proceed against a half-built world."
            )
        return response

    def get(self, path: str, *, token: str | None = None) -> Response:
        return self.request("GET", path, None, token=token)

    def post(
        self, path: str, payload: dict[str, Any] | None = None, *, token: str | None = None
    ) -> Response:
        return self.request("POST", path, payload, token=token)
