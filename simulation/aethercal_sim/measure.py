"""The instruments: latency distributions, an error taxonomy, the outbox backlog, the mailbox.

.. rubric:: Percentiles, and why the method is written down

``p95`` is not one number, it is a family of them: nearest-rank, linear interpolation and the
several exclusive/inclusive variants disagree by a whole sample on small sets, and "p95 = 480 ms"
means nothing if the reader cannot reproduce it. This module uses **nearest-rank on the
sorted sample**
(the smallest observation at or above 95% of the data), says so here, and returns ``None`` for a
percentile of an empty sample rather than quoting the maximum three times over as p50/p95/p99.

.. rubric:: Why the outbox is SAMPLED rather than read once at the end

A backlog read after the run is over is a backlog of zero, on any queue that drains at all. The
number that matters is the **peak** — how far behind the drain fell while the load was on — and it
exists only during the run. So :class:`OutboxSampler` polls the operator surface on a background
thread for the whole run and keeps the series.

==A failed scrape is recorded as a failure, never as a zero.== ``/metrics/summary`` answers 503 when
no operator token is configured and 401 when the token is wrong; both would deserialise to "no
numbers", and a sampler that filled in zeros would report a perfectly flat, perfectly healthy queue
for a run in which it never once managed to look at it. That is precisely the failure the product's
own ``api/operator.py`` was moved out of the web process to avoid — "RLS would have turned the
dead-man switch into the corpse" — and it would be a poor joke to reintroduce it in the instrument
built to measure it.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .client import Response

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------------------


def percentile(sorted_samples: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile over an ALREADY SORTED sample; ``None`` when it is undefined.

    Nearest rank: the smallest observation at or above ``fraction`` of the sample. No interpolation,
    so every number the report quotes is a latency that was really measured, never the average of
    two that were.
    """
    if not sorted_samples:
        return None
    rank = max(1, math.ceil(len(sorted_samples) * fraction))
    return sorted_samples[min(rank - 1, len(sorted_samples) - 1)]


@dataclass(slots=True)
class Latency:
    """A named sample of durations in milliseconds, safe to append to from many threads."""

    name: str
    samples: list[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, elapsed_ms: float) -> None:
        with self._lock:
            self.samples.append(elapsed_ms)

    @property
    def count(self) -> int:
        return len(self.samples)

    def summary(self) -> dict[str, float | int | None]:
        ordered = sorted(self.samples)
        return {
            "count": len(ordered),
            "min": ordered[0] if ordered else None,
            "p50": percentile(ordered, 0.50),
            "p95": percentile(ordered, 0.95),
            "p99": percentile(ordered, 0.99),
            "max": ordered[-1] if ordered else None,
        }


@dataclass(slots=True)
class ErrorTally:
    """Outcomes counted by ``(status, machine code)`` — the report's error taxonomy.

    ==Successes are counted too.== A tally of failures alone cannot answer "out of how many", and a
    run that failed 40 times out of 40 would look identical to one that failed 40 out of 40,000.
    """

    counts: Counter[tuple[int, str]] = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, response: Response) -> None:
        key = (response.status, response.error_code or ("ok" if response.ok else "unclassified"))
        with self._lock:
            self.counts[key] += 1

    def total(self) -> int:
        return sum(self.counts.values())

    def failures(self) -> int:
        return sum(count for (status, _), count in self.counts.items() if not 200 <= status < 300)

    def rows(self) -> list[tuple[int, str, int]]:
        return sorted(
            ((status, code, count) for (status, code), count in self.counts.items()),
            key=lambda row: (-row[2], row[0], row[1]),
        )


def record(latency: Latency, tally: ErrorTally, response: Response) -> Response:
    """Record one response into both instruments and hand it back, so call sites stay one line."""
    latency.record(response.elapsed_ms)
    tally.record(response)
    return response


# --------------------------------------------------------------------------------------
# The outbox backlog
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutboxSample:
    at: float
    due: int
    oldest_due_age_seconds: float
    by_status: dict[str, int]


class OutboxScrapeError(RuntimeError):
    """The operator surface could not be read. Fatal by design — see the module docstring."""


class OutboxSampler:
    """Polls the worker's ``/metrics/summary`` on a background thread for the life of the run.

    The response shape is the one ``observability.render_summary`` produces:
    ``{"outbox": {"by_status": {...}, "due": int, "oldest_due_age_seconds": float, ...}, ...}``.
    """

    def __init__(self, worker_url: str, token: str, interval_seconds: float = 0.5) -> None:
        self._url = f"{worker_url.rstrip('/')}/api/v1/metrics/summary"
        self._token = token
        self._interval = interval_seconds
        self._samples: list[OutboxSample] = []
        self._failures: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    def scrape(self) -> OutboxSample:
        """One read. Raises :class:`OutboxScrapeError` rather than inventing zeros."""
        request = urllib.request.Request(self._url, method="GET")
        request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with _OPENER.open(request, timeout=10.0) as raw:
                payload: Any = json.loads(raw.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OutboxScrapeError(
                f"/metrics/summary answered {exc.code}. 503 means no AETHERCAL_METRICS_TOKEN is "
                "set on the worker (the endpoint is CLOSED by design); 401 means the token the "
                "harness holds is not the one the worker was given. Either way the backlog was NOT "
                "measured, and reporting it as zero would be a lie."
            ) from exc
        except Exception as exc:
            raise OutboxScrapeError(
                f"/metrics/summary unreachable: {type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise OutboxScrapeError(f"/metrics/summary returned a non-object body: {payload!r}")
        outbox: Any = payload.get("outbox")
        if not isinstance(outbox, dict):
            raise OutboxScrapeError(
                f"/metrics/summary carried no 'outbox' object — the contract changed: {payload!r}"
            )
        by_status_raw: Any = outbox.get("by_status", {})
        by_status = (
            {str(k): int(v) for k, v in by_status_raw.items()}
            if isinstance(by_status_raw, dict)
            else {}
        )
        return OutboxSample(
            at=time.time(),
            due=int(outbox.get("due", 0)),
            oldest_due_age_seconds=float(outbox.get("oldest_due_age_seconds", 0.0)),
            by_status=by_status,
        )

    def pause(self) -> None:
        """Stop sampling without ending the run.

        ==For the deliberate outage in the dead-man control, and nothing else.== The operator
        surface is served by the worker, so stopping the worker makes every scrape fail - and those
        failures are the control working, not the instrument breaking. Recorded as failures they
        would raise a warning about an outage the run caused on purpose, and that is how a real
        warning later gets ignored.
        """
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._paused.is_set():
                try:
                    sample = self.scrape()
                    with self._lock:
                        self._samples.append(sample)
                except OutboxScrapeError as exc:
                    with self._lock:
                        self._failures.append(str(exc))
            self._stop.wait(self._interval)

    def start(self) -> None:
        # Prove the endpoint answers BEFORE the run, rather than discovering at the end that every
        # sample failed. A sampler that cannot read is a fatal configuration error, not a degraded
        # mode: the whole drain-health half of the report would be missing.
        self.scrape()
        self._thread = threading.Thread(target=self._loop, name="outbox-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)

    @property
    def samples(self) -> list[OutboxSample]:
        with self._lock:
            return list(self._samples)

    @property
    def failures(self) -> list[str]:
        with self._lock:
            return list(self._failures)

    def peak_due(self) -> int:
        return max((s.due for s in self.samples), default=0)

    def peak_oldest_age(self) -> float:
        return max((s.oldest_due_age_seconds for s in self.samples), default=0.0)


def wait_for_drain(
    sampler: OutboxSampler, *, timeout_seconds: float, poll_seconds: float = 1.0
) -> tuple[bool, float]:
    """Block until the outbox has no DUE work left. Returns ``(drained, seconds_waited)``.

    ``due`` rather than ``pending``: the outbox doubles as the durable scheduler, so a reminder for
    a booking three weeks out is ``pending`` and in perfect health. Waiting for ``pending == 0``
    would wait for ever and report a stuck queue on a completely healthy instance.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    while time.monotonic() < deadline:
        if sampler.scrape().due == 0:
            return True, time.monotonic() - started
        time.sleep(poll_seconds)
    return False, time.monotonic() - started


# --------------------------------------------------------------------------------------
# The mailbox — the only place a confirmation is really observable
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MailMessage:
    to: str
    subject: str
    created: datetime


class Mailbox:
    """Reads Mailpit: the harness's oracle for "the guest was actually told".

    ==The confirmation is what makes a booking real to a guest, and it is produced by the WORKER,
    not by the request that returned 201.== So "the booking POST was fast" and "the guest has their
    appointment" are two different measurements, and only this one crosses the outbox.

    ``unparseable`` counts messages whose ``Created`` timestamp this client could not read. It is
    surfaced in the report instead of being swallowed: dropping them silently would shrink the
    drain-latency sample without saying so, and a Mailpit upgrade that renamed the field would
    present as "the drain got faster" — fewer, luckier samples — rather than as a broken oracle.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self.unparseable = 0

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(f"{self._base}{path}", method="GET")
        with _OPENER.open(request, timeout=30.0) as raw:
            return json.loads(raw.read().decode("utf-8"))

    def purge(self) -> None:
        request = urllib.request.Request(f"{self._base}/api/v1/messages", method="DELETE")
        with _OPENER.open(request, timeout=30.0):
            pass

    def messages(self, limit: int = 20000) -> list[MailMessage]:
        """Every message currently held, flattened to what the harness needs."""
        payload: Any = self._get(f"/api/v1/messages?limit={limit}")
        if not isinstance(payload, dict):
            return []
        raw_messages: Any = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            return []
        out: list[MailMessage] = []
        unparseable = 0
        for item in raw_messages:
            if not isinstance(item, dict):
                unparseable += 1
                continue
            recipients: Any = item.get("To", [])
            address = ""
            if isinstance(recipients, list) and recipients:
                first: Any = recipients[0]
                if isinstance(first, dict):
                    address = str(first.get("Address", ""))
            try:
                created = datetime.fromisoformat(
                    str(item.get("Created", "")).replace("Z", "+00:00")
                )
            except ValueError:
                unparseable += 1
                continue
            if not address:
                unparseable += 1
                continue
            out.append(
                MailMessage(
                    to=address.lower(), subject=str(item.get("Subject", "")), created=created
                )
            )
        self.unparseable = unparseable
        return out

    def count(self) -> int:
        """How many messages the mailbox is holding, as Mailpit itself reports it.

        Used to assert the mailbox never ROLLED: Mailpit evicts the oldest messages once
        ``MP_MAX_MESSAGES`` is reached, which would delete the confirmations belonging to the
        earliest bookings — and the drain-latency distribution would then be computed over the tail
        of the run only, a better-looking number produced purely by losing data.
        """
        payload: Any = self._get("/api/v1/messages?limit=1")
        if not isinstance(payload, dict):
            return 0
        for key in ("messages_count", "total"):
            value: Any = payload.get(key)
            if isinstance(value, int):
                return value
        return 0
