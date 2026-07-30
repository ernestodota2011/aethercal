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

==A failed scrape is recorded as a failure, never as a zero.== That sentence is old;
:func:`parse_summary` is where it became true of the READER too, not just of the transport.
Until then every absent field was defaulted to its healthiest value, so a renamed field or a
partial response read as ``backlog 0`` — see that function for what it cost.

``/metrics/summary`` answers 503 when no operator token is configured and 401 when the token is
wrong; both would deserialise to "no numbers", and a sampler that filled in zeros would report a
perfectly flat, perfectly healthy queue for a run in which it never once managed to look at it.
That is precisely the failure the product's own ``api/operator.py`` was moved out of the web
process to avoid — "RLS would have turned the dead-man switch into the corpse" — and it would be
a poor joke to reintroduce it in the instrument built to measure it.
"""

from __future__ import annotations

import email
import email.policy
import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, assert_never

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

    def _snapshot(self) -> list[tuple[tuple[int, str], int]]:
        """Copia bajo el lock. ==Escribir protegido y LEER sin proteger no es proteger.==

        `record` corre desde 8 invitados organicos y desde rafagas de 40 hilos; iterar
        `counts` mientras otro hilo lo muta puede reventar con "dictionary changed size
        during iteration" o —peor, porque es silencioso— devolver un total que no
        corresponde a ningun instante real. La taxonomia de §6 y la reconciliacion de
        C13 se calculan sobre esto.
        """
        with self._lock:
            return list(self.counts.items())

    def total(self) -> int:
        return sum(count for _, count in self._snapshot())

    def failures(self) -> int:
        return sum(count for (status, _), count in self._snapshot() if not 200 <= status < 300)

    def rows(self) -> list[tuple[int, str, int]]:
        return sorted(
            ((status, code, count) for (status, code), count in self._snapshot()),
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
    """One read of the operator surface — ==both the gauges and the counters, on purpose.==

    ``due`` and ``oldest_due_age_seconds`` are INSTANTANEOUS: they describe the queue at the instant
    of the scrape and say nothing about the instant before it. ``rows`` and ``delivered`` are
    CUMULATIVE, and that difference is what the dead-man control turned out to need — a gauge can
    be back at rest before the first observer arrives, and then the observation reports "nothing
    happened" about something that did.
    """

    at: float
    due: int
    oldest_due_age_seconds: float
    by_status: dict[str, int]
    delivered: int | None = None
    """``drain.delivered`` — intents this WORKER PROCESS has delivered since it booted.

    ==Process-local and reset by a restart, which is exactly why it is useful here.== After the
    dead-man control restarts the worker it starts from zero and only grows, so "the restarted
    worker processed the stranded work" can be read at any later moment and still be true. ``None``
    means the payload carried no ``drain.delivered`` at all — a changed contract, kept distinct
    from a delivered count of zero, because "absent" and "0" must never look the same."""

    @property
    def rows(self) -> int:
        """Total outbox rows the DB-derived snapshot can see, across every status.

        ==Cumulative and durable across a worker restart==, unlike ``due``. It is what proves the
        snapshot really reads the table rather than answering a constant: work that appeared while
        the drain was down is still visible in it long after the drain caught up.
        """
        return sum(self.by_status.values())


class OutboxScrapeError(RuntimeError):
    """The operator surface could not be read. Fatal by design — see the module docstring."""


#: Every status ``observability.MetricsSnapshot.outbox_by_status`` publishes, which the product
#: documents as always present: *"Every member of OutboxStatus, whether or not it has rows — a
#: dashboard cannot alert on a series that does not exist, so absent and zero must never look the
#: same."* ==The harness holds the product to that sentence.==
#:
#: The set is compared EXACTLY, not as a subset. ``rows`` (the durable signal C5 gates on) is the
#: sum of these, so a status this harness does not know about would be silently excluded from a
#: total that is then compared against work created — and a renamed one would vanish from it. Either
#: way the arithmetic would keep working and quietly mean something else. A schema change here must
#: stop the run and be answered by a human, not absorbed.
EXPECTED_OUTBOX_STATUSES: frozenset[str] = frozenset(
    {"pending", "claimed", "failed", "delivered", "dead", "skipped", "voided", "unknown"}
)


def _require_count(container: dict[str, Any], key: str, where: str) -> int:
    """A required, non-negative integer. ==No coercion: `"5"` and `True` are contract breaches.==

    ``bool`` is checked before ``int`` because it is a subclass of one, so ``True`` would otherwise
    sail through as ``1``. And a string that happens to parse is not a number the server sent — it
    is a server that changed how it serialises, which is exactly the event this must surface.
    """
    if key not in container:
        raise OutboxScrapeError(
            f"{where} carried no '{key}'. ==An absent field is NOT a zero.== Reporting it as one "
            "would draw the same flat, healthy line as a queue that is genuinely keeping up, which "
            "is the single failure this instrument exists to make impossible."
        )
    value: Any = container[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise OutboxScrapeError(
            f"{where}['{key}'] is {value!r} ({type(value).__name__}), not an integer. The contract "
            "changed, and coercing it would invent a measurement nobody took."
        )
    if value < 0:
        raise OutboxScrapeError(f"{where}['{key}'] is negative ({value}); that is not a count.")
    return value


def _require_seconds(container: dict[str, Any], key: str, where: str) -> float:
    """A required, non-negative duration. Accepts ``int``/``float``; rejects ``bool`` and text."""
    if key not in container:
        raise OutboxScrapeError(
            f"{where} carried no '{key}'. ==An absent dead-man reading is NOT 0.0s.== The "
            "oldest-due age IS the alarm; defaulting it to zero silences it and calls that health."
        )
    value: Any = container[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OutboxScrapeError(
            f"{where}['{key}'] is {value!r} ({type(value).__name__}), not a number."
        )
    if value < 0:
        raise OutboxScrapeError(f"{where}['{key}'] is negative ({value}); that is not an age.")
    return float(value)


def parse_summary(payload: Any) -> OutboxSample:
    """Turn one ``/metrics/summary`` body into a sample, or refuse it. ==Pure, so it is testable.==

    .. rubric:: The policy this module declares, finally applied to itself

    The module docstring above has always said *"a failed scrape is recorded as a failure, never as
    a zero"* — and then the reader did the opposite one screen down. ``outbox.get("due", 0)``,
    ``outbox.get("oldest_due_age_seconds", 0.0)`` and a ``by_status`` that fell back to ``{}``
    turned **every absent field into the healthiest possible value**. So a renamed field in a future
    version, a partial response, or an error serialised as JSON would all have read as *backlog 0*:
    C12 would announce "drained", §3 would print a flat line, and C5 would have nothing to see.

    ==A backlog that reaches zero because the field disappeared is indistinguishable from a system
    that is keeping up.== That is the defect this whole directory hunts, sitting inside the
    instrument the drain half of the report is built on.

    Every field is now required and strictly typed, and ``by_status`` is validated to the value —
    a ``null``, a string where an integer belongs, or a status this harness does not recognise is
    an :class:`OutboxScrapeError`, never a zero.

    ``drain.delivered`` keeps its deliberate three-way reading: absent is ``None`` (C5 reports
    "ABSENT" and fails, naming a changed contract), present-but-mistyped is a scrape error, and a
    real integer is the counter. Absent and zero stay different facts there too.
    """
    if not isinstance(payload, dict):
        raise OutboxScrapeError(f"/metrics/summary returned a non-object body: {payload!r}")
    outbox: Any = payload.get("outbox")
    if not isinstance(outbox, dict):
        raise OutboxScrapeError(
            f"/metrics/summary carried no 'outbox' object — the contract changed: {payload!r}"
        )

    by_status_raw: Any = outbox.get("by_status")
    if not isinstance(by_status_raw, dict):
        raise OutboxScrapeError(
            f"outbox['by_status'] is {by_status_raw!r}, not an object. ==An empty map is not a "
            "quiet queue==; it is a queue nobody counted, and `rows` would sum it to 0."
        )
    seen = frozenset(str(key) for key in by_status_raw)
    if seen != EXPECTED_OUTBOX_STATUSES:
        missing = sorted(EXPECTED_OUTBOX_STATUSES - seen)
        extra = sorted(seen - EXPECTED_OUTBOX_STATUSES)
        raise OutboxScrapeError(
            f"outbox['by_status'] does not carry the documented status set — the contract changed. "
            f"Missing: {missing or 'none'}; unrecognised: {extra or 'none'}. `rows` is the sum of "
            "these and C5 compares it against work created, so a status silently absent from the "
            "total would keep the arithmetic working while changing what it means."
        )
    # ==Validated to the VALUE, not just to the key.== A status that is present but carries `null`,
    # or `"3"`, is a series the dashboard cannot alert on either; permissive coercion here would put
    # the zero back one level down from where it was just removed.
    by_status = {
        str(key): _require_count(by_status_raw, str(key), "outbox['by_status']")
        for key in by_status_raw
    }

    delivered: int | None = None
    drain: Any = payload.get("drain")
    if isinstance(drain, dict) and "delivered" in drain:
        delivered = _require_count(drain, "delivered", "drain")

    return OutboxSample(
        at=time.time(),
        due=_require_count(outbox, "due", "outbox"),
        oldest_due_age_seconds=_require_seconds(outbox, "oldest_due_age_seconds", "outbox"),
        by_status=by_status,
        delivered=delivered,
    )


@dataclass(frozen=True, slots=True)
class SamplerResults:
    """The sampler's series, ==frozen at the instant :meth:`OutboxSampler.stop` sealed it.==

    A tuple rather than a list, and a value rather than a view: what the report is built from must
    not be able to change between two reads of it. See :meth:`OutboxSampler.stop`.
    """

    samples: tuple[OutboxSample, ...]
    failures: tuple[str, ...]
    discarded_at_pause: int


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
        # ==Held for the whole scrape-and-record step, so `pause()` can wait it out.== See pause().
        self._in_flight = threading.Lock()
        self._discarded_at_pause = 0
        self._thread: threading.Thread | None = None
        #: The sealed snapshot, once :meth:`stop` has taken it. ==Its presence is what makes every
        #: recording path a no-op==, which is the only form of "the results are stable" this class
        #: can actually keep; see :meth:`stop`.
        self._results: SamplerResults | None = None

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

        return parse_summary(payload)

    def pause(self) -> None:
        """Stop sampling without ending the run, and ==do not return until sampling has STOPPED.==

        For the deliberate outage in the dead-man control, and nothing else. The operator surface is
        served by the worker, so stopping the worker makes every scrape fail — and those failures
        are the control working, not the instrument breaking. Recorded as failures they would raise
        a warning about an outage the run caused on purpose, and that is how a real warning later
        gets ignored.

        ==That was the guarantee this method DOCUMENTED and did not provide.== It set the flag and
        returned instantly, while the sampler thread could be in the middle of a scrape — a window
        as wide as the 10-second socket timeout. `control_drain_deadman` calls this and then stops
        the worker on the very next line, so that in-flight scrape fails with a connection refused
        and lands in ``failures``. C12 gates on ``scrape_failures == 0``. ==A perfectly correct run
        would then fail its own control, at random, because of an outage it caused on purpose== —
        the same shape as C5 racing its own drain, one layer down in the instrument.

        So the flag is set FIRST and then the in-flight lock is acquired: when this returns, no
        scrape is running, and any that was running finished with the flag already set and had its
        result discarded (counted in :attr:`discarded_at_pause`, never silently). A sample lost at
        that boundary is a sample taken microseconds before a deliberate outage; the count is
        reported so the discard is a fact rather than a favour.
        """
        self._paused.set()
        with self._in_flight:
            pass

    def resume(self) -> None:
        self._paused.clear()

    @property
    def discarded_at_pause(self) -> int:
        """Scrapes that completed after a pause began, and were therefore not recorded.

        Surfaced because a filter nobody can see is how a real warning eventually gets dropped.
        """
        with self._lock:
            return self._discarded_at_pause

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._paused.is_set():
                self._scrape_once()
            self._stop.wait(self._interval)

    def _scrape_once(self) -> None:
        """One sampled read, recorded or discarded. ==Holds the in-flight lock itself.==

        The lock lives HERE rather than at the call site, and that placement is the point: it spans
        the scrape AND the decision about what to do with its result, so `pause()` can wait for
        both by acquiring the same lock. Put it in `_loop` instead and the guarantee holds only for
        the one caller that remembers it — which is the defect this harness keeps finding in itself,
        a rule enforced at one of its call sites.

        Re-reading `_paused` after the call is the other half: a pause that began mid-scrape means
        this result belongs to the outage the run caused on purpose, so it is discarded and counted
        rather than filed as an unexplained failure C12 would then void the run over.
        """
        with self._in_flight:
            # ==The pause is re-checked HERE, under the lock `pause()` waits on.== `_loop` tests
            # `_paused` outside it, so a thread that had already passed that test could acquire this
            # lock and fire its request after `pause()` had returned — and the dead-man stops the
            # worker on the very next line, so that request fails and is recorded as a real failure
            # caused by the control itself. Third pass over this: S13 made `pause()` a barrier, S19
            # made `stop()` prove it stopped, and the decision to scrape was still being taken
            # outside the lock that serialises them.
            if self._paused.is_set():
                return
            self._record_scrape()

    def _record_scrape(self) -> None:
        try:
            sample = self.scrape()
        except OutboxScrapeError as exc:
            self._file(failure=str(exc))
        except Exception as exc:
            # ==An unexpected exception used to END this thread, in silence.== Only
            # OutboxScrapeError was caught, so anything else — and the old permissive reader could
            # raise TypeError on `int(None)` — killed the sampler outright. The run carried on,
            # `failures` stayed empty, C12 saw zero unexplained scrape failures, and §3 reported a
            # peak backlog computed over however many samples had been taken before the thread
            # died. Sampling that STOPPED must never present as sampling that found nothing wrong.
            self._file(failure=f"the sampler hit an unexpected {type(exc).__name__}: {exc}")
        else:
            self._file(sample=sample)

    def _file(self, *, sample: OutboxSample | None = None, failure: str = "") -> None:
        """The ONE place a reading enters this object — ==and therefore the one place it can be
        refused.==

        The three recording branches each held the lock and each decided for themselves whether the
        run was paused. Adding the seal to two of them and forgetting the third is exactly the
        "rule enforced at one of its call sites" this harness keeps finding, so there is now a
        single site: sealed → dropped, paused → counted as a discard, otherwise recorded.
        """
        with self._lock:
            if self._results is not None:
                # ==After stop() sealed the snapshot, a still-running thread may not touch it.==
                # This is what makes the guarantee in stop()'s docstring true rather than hoped
                # for. The thread outliving its join is recorded there, as a FAILURE C12 gates on.
                return
            if self._paused.is_set():
                self._discarded_at_pause += 1
            elif sample is not None:
                self._samples.append(sample)
            else:
                self._failures.append(failure)

    def start(self) -> None:
        """Prove the endpoint answers, ==and KEEP that first reading.==

        The probe was thrown away: ``self.scrape()`` ran only for its exception. On a short run the
        thread could then be stopped before its first tick, leaving ``samples`` empty — and every
        derived number is a ``max(..., default=0)``, so §3 printed a peak backlog of 0 and a peak
        age of 0.0s. ==The ABSENCE of measurement read as a perfectly healthy queue==, which is the
        one failure this module's docstring exists to forbid.

        A sampler that cannot read is a fatal configuration error, not a degraded mode: the whole
        drain-health half of the report would be missing. So the probe is recorded, and a start
        that somehow leaves no sample raises rather than proceeding.
        """
        sample = self.scrape()
        with self._lock:
            self._samples.append(sample)
            recorded = len(self._samples)
        if recorded < 1:  # pragma: no cover - defensive; the append above cannot leave it empty
            raise OutboxScrapeError("the sampler started without recording its first reading")
        self._thread = threading.Thread(target=self._loop, name="outbox-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> SamplerResults:
        """Stop sampling and ==SEAL the results, so nothing can change under the report.==

        .. rubric:: What this used to promise, and why the promise was the defect

        It said it would "prove the thread is finished before anyone reads the results", and
        implemented that as a bounded ``join`` followed by an appended failure if the thread was
        still alive. ==Recording the problem is not preventing it.== A thread that outlived the
        join went on appending to ``_samples`` and ``_failures`` while ``main()`` built C12 and
        rendered §3, so the peak backlog, the sample count and the scrape-failure count could each
        be read from a DIFFERENT state — and the failure note itself, which says "every backlog
        number below was read while it was still being written", was filed into the very list it
        was warning about.

        .. rubric:: The guarantee that can actually be kept is about the DATA, not the thread

        Killing a Python thread is not possible, and joining without a deadline would hang the run
        on a socket that never times out — the caller would then have neither a report nor a
        verdict, which is strictly worse than a warned one. So the invariant is inverted: the
        snapshot is taken under the lock and stored, and its presence makes every recording path a
        no-op (see :meth:`_file`). A zombie thread may keep scraping; it can no longer be heard.
        ==After this returns, ``samples``, ``failures`` and ``discarded_at_pause`` are frozen —
        including on a run where the join timed out.==

        The thread outliving its join is still a fact about the run, so it is recorded as a scrape
        failure INSIDE the sealed snapshot, where C12 gates on it. Idempotent: a second call (the
        ``finally`` in ``main()``) returns the same snapshot without joining again.
        """
        self._stop.set()
        with self._lock:
            if self._results is not None:
                return self._results
        alive = False
        if self._thread is not None:
            self._thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
            alive = self._thread.is_alive()
        with self._lock:
            if self._results is not None:  # pragma: no cover - two concurrent stop() callers
                return self._results
            if alive:
                self._failures.append(
                    "the sampler thread was STILL RUNNING "
                    f"{_STOP_JOIN_TIMEOUT_SECONDS:.0f}s after stop() was asked for. Its readings "
                    "from this point on are DISCARDED, so the numbers below are stable — but the "
                    "series they come from ended early"
                )
            self._results = SamplerResults(
                tuple(self._samples), tuple(self._failures), self._discarded_at_pause
            )
            return self._results

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
class CalendarInvite:
    """The iTIP facts a booking email carries in its ``.ics`` part — ==a structured identity.==

    ``uid`` is the booking's ``ical_uid``, stable across the whole lineage; ``method``/``status``
    are the iTIP verb (``REQUEST``/``CANCEL``, ``CONFIRMED``/``CANCELLED``); ``sequence`` is the
    RFC 5545 counter the product bumps on every reschedule while keeping the uid.

    Those four fields are what let a confirmation be told apart from the cancellation or reschedule
    notice that follows it — ==without matching on a subject line, which is locale-dependent tenant
    data, and without matching on a recipient, which every one of them shares.==
    """

    uid: str
    method: str
    status: str
    sequence: int

    @property
    def is_confirmation(self) -> bool:
        """The FIRST announcement of a booking: an invite, confirmed, at the original sequence.

        A reschedule is also ``REQUEST``/``CONFIRMED`` — it re-invites to the same uid — so the
        sequence is what separates them. A cancellation is ``CANCEL``/``CANCELLED``. A reminder and
        a workflow email carry no calendar part at all.
        """
        return self.method == "REQUEST" and self.status == "CONFIRMED" and self.sequence == 0


@dataclass(frozen=True, slots=True)
class NoInvite:
    """The message carries no ``text/calendar`` part at all — ==an ABSENCE, and a legitimate one.==

    A reminder and a workflow email are exactly this, and there are hundreds of them in a run.
    """


@dataclass(frozen=True, slots=True)
class UnreadableInvite:
    """A calendar part is PRESENT and this reader could not turn it into an identity.

    ==This is the state that used to be indistinguishable from :class:`NoInvite`, and the two are
    opposites.== "This message announces nothing" is a fact about a reminder; "this message
    announces something and I cannot tell what" is a hole in the oracle. Collapsing them put the
    hole on the reassuring side: a confirmation whose ``.ics`` came back mangled simply stopped
    being a confirmation, the booking fell to "no confirmation", and a guest who ALSO received a
    cancellation was then counted as ``superseded`` — accounted for, and C14 passed over it.
    """

    reason: str


#: The THREE outcomes of reading a message's calendar identity, as a closed union. The policy that
#: depends on them (:func:`invite_role`) matches on this alias and ends in ``assert_never``, so a
#: fourth state cannot be added without the type checker naming every place that must decide about
#: it — rather than having it fall silently into whichever branch happens to be the default.
InviteReading = CalendarInvite | NoInvite | UnreadableInvite


class InviteRole(Enum):
    """What a message's calendar reading MEANS to the booking→confirmation reconciliation."""

    CONFIRMATION = "confirmation"
    """A first announcement: ``REQUEST``/``CONFIRMED``/``SEQUENCE:0``."""
    SUPERSEDING = "superseding"
    """A cancellation or a reschedule — it retires a confirmation instead of being one."""
    UNCLASSIFIED = "unclassified"
    """A readable invite that is neither (e.g. ``REQUEST``/``TENTATIVE`` at sequence 0)."""
    ABSENT = "absent"
    """No calendar part: a reminder or a workflow email."""
    UNREADABLE = "unreadable"
    """A calendar part that could not be read — ==counted and gated, never assumed harmless.=="""


def invite_role(reading: InviteReading) -> InviteRole:
    """Classify ONE calendar reading. ==The single place the three-state axis is decided.==

    Every consumer asks this function instead of testing ``is not None``, which is how the
    ``present but unreadable`` case used to disappear: a truthiness check has two answers and the
    question has three.
    """
    match reading:
        case CalendarInvite():
            if reading.is_confirmation:
                return InviteRole.CONFIRMATION
            if reading.method == "CANCEL" or reading.sequence > 0:
                return InviteRole.SUPERSEDING
            return InviteRole.UNCLASSIFIED
        case NoInvite():
            return InviteRole.ABSENT
        case UnreadableInvite():
            return InviteRole.UNREADABLE
        case _:
            assert_never(reading)


@dataclass(frozen=True, slots=True)
class MailMessage:
    to: str
    subject: str
    created: datetime
    message_id: str = ""
    invite: InviteReading = NoInvite()
    """The message's calendar identity as one of THREE states — see :data:`InviteReading`."""


#: How many messages ONE Mailpit request asks for. ==A page size, never a ceiling.== The difference
#: is the whole of :class:`MailboxRead` — see its docstring.
MAILBOX_PAGE_SIZE = 500

#: How long ``stop()`` waits for the sampler thread. One in-flight scrape can hold the socket for
#: the client's full timeout, so the wait has to exceed it or a healthy stop looks like a stuck one.
_STOP_JOIN_TIMEOUT_SECONDS = 20.0

#: A read needing more pages than this is not a big mailbox, it is a loop that will not terminate.
_MAX_MAILBOX_PAGES = 400


@dataclass(frozen=True, slots=True)
class MailboxRead:
    """Every message the mailbox held — and ==whether the read can be trusted to BE every message.==

    ``complete`` exists because of a defect this reader used to have. It asked for ``limit=20000``
    in a single request: a number chosen once, equal to the ``MP_MAX_MESSAGES`` the compose overlay
    happens to set, and compared against nothing. A mailbox holding more came back **silently
    truncated** — the list simply stopped — and ``booking_to_confirmation_email`` was then computed
    over whichever subset survived. ==A latency sample that loses members reports a FASTER
    product==, so the failure mode of that ceiling was to improve the published numbers, and the
    verdict never learned that anything had been lost.

    The class already reasoned correctly about exactly this shape for ``unparseable`` — "a Mailpit
    upgrade that renamed the field would present as *the drain got faster*" — and then left the
    ceiling unwatched three lines below it. So the ceiling is gone: :meth:`Mailbox.read_all` pages
    until it has collected the total Mailpit itself reports, and reports here when it could not.
    An incomplete read is a fact the verdict acts on (C14), never a shorter list.
    """

    messages: list[MailMessage]
    complete: bool
    reported_total: int
    """How many messages Mailpit says it holds — the bound this read is reconciled against, taken
    from the server rather than chosen by the client."""
    pages: int
    page_size: int
    unparseable: int = 0
    """Messages whose recipient or ``Created`` timestamp this client could not read.

    Surfaced rather than swallowed: dropping them silently would shrink the drain-latency sample
    without saying so."""
    problem: str = ""


#: RFC 5545 folds a long line by inserting a line break plus ONE space or tab. Both continue.
_FOLD_PREFIXES = (" ", "\t")


def _icalendar_properties(text: str) -> dict[str, str]:
    """The iCalendar content lines of one component, unfolded, keyed by property NAME.

    RFC 5545 folds long lines by inserting CRLF followed by a single space or tab; a reader that
    does not unfold first sees a truncated value and, worse, sees the continuation as a line of its
    own. Unfolding happens before anything is matched.

    Only the first occurrence of each property is kept, which is the VCALENDAR-level ``METHOD``
    followed by the VEVENT's own properties — the single-VEVENT shape this product emits.
    """
    unfolded: list[str] = []
    # `splitlines()` handles CRLF, LF and CR alike, so the unfolding does not depend on which
    # line ending survived the transport.
    for line in text.splitlines():
        if line[:1] in _FOLD_PREFIXES and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    properties: dict[str, str] = {}
    for line in unfolded:
        head, separator, value = line.partition(":")
        if not separator:
            continue
        name = head.split(";", 1)[0].strip().upper()
        if name and name not in properties:
            properties[name] = value.strip()
    return properties


class Mailbox:
    """Reads Mailpit: the harness's oracle for "the guest was actually told".

    ==The confirmation is what makes a booking real to a guest, and it is produced by the WORKER,
    not by the request that returned 201.== So "the booking POST was fast" and "the guest has their
    appointment" are two different measurements, and only this one crosses the outbox.
    """

    def __init__(self, base_url: str, *, page_size: int = MAILBOX_PAGE_SIZE) -> None:
        self._base = base_url.rstrip("/")
        self.page_size = page_size

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(f"{self._base}{path}", method="GET")
        with _OPENER.open(request, timeout=30.0) as raw:
            return json.loads(raw.read().decode("utf-8"))

    def purge(self) -> None:
        request = urllib.request.Request(f"{self._base}/api/v1/messages", method="DELETE")
        with _OPENER.open(request, timeout=30.0):
            pass

    @staticmethod
    def reported_total(payload: dict[str, Any]) -> int | None:
        """Mailpit's own count of what it holds — the only number that can bound a read of it.

        ``messages_count`` is the total matching the (empty) filter and ``total`` is the
        mailbox-wide count; older builds publish only the second, so both are tried in that order.
        ``None`` means the envelope carried neither, which is a CHANGED CONTRACT and emphatically
        not a mailbox of zero.

        ==``bool`` is rejected explicitly.== In Python ``isinstance(True, int)`` is true, so a
        ``messages_count: true`` — an envelope whose contract has changed — would have read as a
        mailbox of ONE and the read would have declared itself whole after a single message. An
        unexpected type has to fall through to the ``None`` that means "changed contract", never
        to a number. A negative count is refused for the same reason.
        """
        for key in ("messages_count", "total"):
            value: Any = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return value
        return None

    @staticmethod
    def parse_invite(  # noqa: PLR0911 - each return names a DISTINCT reading of the calendar
        raw_source: str,
    ) -> InviteReading:
        """Read the iTIP identity out of an RFC822 source as ONE of :data:`three states
        <InviteReading>`.

        .. rubric:: ==Why this is not a ``CalendarInvite | None``.==

        It was, and that is a binary partition over a question with three answers: *no calendar
        part*, *this calendar part*, and *a calendar part I could not read*. The middle answer has
        no room in a boolean, so it fell to the optimistic side — a message whose ``.ics`` was
        missing its ``UID``, or whose ``SEQUENCE`` was not a number, came back looking exactly like
        the reminder that legitimately carries no calendar at all. ==Every consumer then asked ``is
        not None``, got "no invite here", and moved on.== The failure was silent by construction:
        a corrupted confirmation stopped counting as a confirmation, and a guest who also received
        a cancellation was reconciled as ``superseded`` — an ACCOUNTED outcome that C14 passes.
        A parse failure of the oracle presented as the product behaving correctly.

        So the failures are now :class:`UnreadableInvite`, which carries WHY, and which C14 counts
        and gates on; the absence is :class:`NoInvite`; and only a complete, well-typed identity
        comes back as a :class:`CalendarInvite`.

        ==Parsed with the stdlib ``email`` package, not with a regex over the raw bytes.== The
        ``.ics`` arrives as a MIME part that may be base64 or quoted-printable encoded, so scanning
        the source text for ``UID:`` finds nothing on exactly the messages that matter. Walking the
        parts and asking for the decoded payload is the difference between reading the calendar and
        reading the envelope it happens to be wrapped in.

        Property matching is ==line-anchored and exact==: an iCalendar content line begins at the
        start of a line with ``NAME:`` or ``NAME;PARAM:``. A substring search would let ``STATUS``
        match inside a description, which is how the other branch's ``in source`` check ended up
        passing over an invariant somebody had deleted.
        """
        try:
            message: Any = email.message_from_string(raw_source, policy=email.policy.default)
        except Exception as exc:
            return UnreadableInvite(
                f"the RFC822 source could not be parsed: {type(exc).__name__}: {exc}"
            )
        for part in message.walk():
            if part.get_content_type() != "text/calendar":
                continue
            try:
                payload: Any = part.get_payload(decode=True)
            except Exception as exc:
                return UnreadableInvite(
                    f"the text/calendar part could not be decoded: {type(exc).__name__}: {exc}"
                )
            if not isinstance(payload, bytes):
                return UnreadableInvite(
                    "the text/calendar part decoded to "
                    f"{type(payload).__name__}, not to bytes — there is a calendar here and it "
                    "cannot be read"
                )
            text = payload.decode("utf-8", errors="replace")
            fields = _icalendar_properties(text)
            required = ("UID", "METHOD", "STATUS", "SEQUENCE")
            missing = [name for name in required if name not in fields]
            if missing:
                return UnreadableInvite(
                    f"the text/calendar part is missing {', '.join(missing)}, so this announcement "
                    "has no identity"
                )
            raw_sequence = fields["SEQUENCE"]
            try:
                sequence = int(raw_sequence)
            except ValueError:
                return UnreadableInvite(
                    f"SEQUENCE is {raw_sequence!r}, which is not an integer, so a reschedule "
                    "cannot be told from a first announcement"
                )
            return CalendarInvite(
                uid=fields["UID"],
                method=fields["METHOD"].upper(),
                status=fields["STATUS"].upper(),
                sequence=sequence,
            )
        return NoInvite()

    @staticmethod
    def parse(item: Any) -> MailMessage | None:
        """One Mailpit envelope, or ``None`` when unreadable (counted, never dropped)."""
        if not isinstance(item, dict):
            return None
        recipients: Any = item.get("To", [])
        address = ""
        if isinstance(recipients, list) and recipients:
            first: Any = recipients[0]
            if isinstance(first, dict):
                address = str(first.get("Address", ""))
        if not address:
            return None
        try:
            created = datetime.fromisoformat(str(item.get("Created", "")).replace("Z", "+00:00"))
        except ValueError:
            return None
        return MailMessage(
            to=address.lower(),
            subject=str(item.get("Subject", "")),
            created=created,
            message_id=str(item.get("ID", "")),
        )

    def _incomplete(
        self, *, collected: list[MailMessage], total: int, page: int, unparseable: int, why: str
    ) -> MailboxRead:
        return MailboxRead(collected, False, total, page, self.page_size, unparseable, why)

    def _raw_source(self, message_id: str) -> str:
        request = urllib.request.Request(
            f"{self._base}/api/v1/message/{message_id}/raw", method="GET"
        )
        with _OPENER.open(request, timeout=30.0) as raw:
            return raw.read().decode("utf-8", errors="replace")

    def hydrate_invites(self, messages: list[MailMessage]) -> tuple[list[MailMessage], str]:
        """Attach each message's calendar identity by reading its RFC822 source.

        ==One request per message, and that cost buys the only unambiguous discriminator there is.==
        The list endpoint returns recipient, subject and timestamp — none of which identify WHICH
        booking a message announces, nor WHICH announcement it is. The `.ics` part does both.

        Returns the hydrated messages and a problem string (empty when every source was readable).
        A message whose source cannot be fetched is not silently left un-hydrated: it would then
        look exactly like a message with no calendar part, which is the reassuring reading.
        """
        hydrated: list[MailMessage] = []
        for message in messages:
            if not message.message_id:
                return hydrated, "a message came back with no ID, so its source cannot be read"
            try:
                source = self._raw_source(message.message_id)
            except Exception as exc:
                return (
                    hydrated,
                    f"could not read the source of message {message.message_id}: "
                    f"{type(exc).__name__}: {exc}",
                )
            hydrated.append(
                MailMessage(
                    to=message.to,
                    subject=message.subject,
                    created=message.created,
                    message_id=message.message_id,
                    invite=self.parse_invite(source),
                )
            )
        return hydrated, ""

    def _whole(
        self, collected: list[MailMessage], total: int, page: int, unparseable: int
    ) -> MailboxRead:
        """The read reached the reported total — ==but "reached the total" is not "read it all".==

        ``complete`` used to mean only that the paging arrived at Mailpit's count, so a read that
        had reached 279 of 279 while silently failing to parse three of those envelopes still
        declared itself whole. An envelope with no recipient or an unreadable ``Created`` stamp is a
        message that exists and did not make it into the list: the list is short, the reconciliation
        that follows is over a smaller set than it believes, and ==the shortfall presents as a
        booking that simply has no confirmation.==

        This is the third time on this branch that a reader has counted its own losses and then
        declared success anyway, so the decision lives in ONE place: a read is whole when it paged
        to the total AND lost nothing on the way.
        """
        if unparseable:
            return MailboxRead(
                collected,
                False,
                total,
                page,
                self.page_size,
                unparseable,
                f"{unparseable} envelope(s) of {total} could not be parsed (no recipient, or an "
                "unreadable Created stamp), so this list is SHORTER than the mailbox it reports",
            )
        return MailboxRead(collected, True, total, page, self.page_size, unparseable)

    def read_all(  # noqa: PLR0911, PLR0912 - each return names a DISTINCT broken read
        self,
    ) -> MailboxRead:
        """Page through the WHOLE mailbox, or report why the read is not whole.

        ==There is no ``limit`` parameter, on purpose.== How much this read should return is a
        property of what the run produced, not a constant somebody picked in advance; the only
        defensible bound is the total Mailpit itself publishes, and reaching it is the loop's
        termination condition. A failed request, an envelope that is not the contract, a page that
        does not advance, or a total never reached each return ``complete=False`` with the reason
        attached — because the one thing a truncated mailbox must never look like is a small one.

        .. rubric:: ==The cursor is Mailpit's; the ORACLE is the set of ids seen.==

        ``start`` used to be both, and the two are not the same statement. Advancing it by the
        length of each page and stopping at the reported total means a server that answers the SAME
        page twice — a proxy ignoring ``start``, a renamed parameter, a future Mailpit that
        paginates by cursor — ends the loop having read half the mailbox, with the arithmetic
        agreeing perfectly: 2 pages of 500 is the 1000 it was promised. The list then holds 500
        messages twice over, and ==a duplicated confirmation is one of the two things C14 exists to
        catch==, so the broken read arrives wearing the costume of the finding.

        Every id is recorded now, a page carrying one already seen is a refusal that names itself,
        and the loop ends when ``total`` DISTINCT messages have been observed. There is deliberately
        no second "and now reconcile the count" step: with repeats refused, the cursor and the
        unique count are the same number, and two statements of one fact are two things that drift.

        .. rubric:: ==And the termination test is EQUALITY, because ``>=`` is not the invariant.==

        It was ``len(seen) >= total``, which is a weaker claim than the one ``complete`` makes to
        C14. Two readings satisfy it while contradicting it:

        * the server hands back MORE distinct messages than its own envelope reports — a mailbox
          that grew mid-read, a total computed under a filter the list does not apply — and the read
          declares itself an exact reconciliation of a set it had already overrun;
        * ``total`` is re-read on EVERY page, so a total that DROPS between pages (a purge, a
          different backend behind a proxy) satisfies ``>=`` immediately and closes the read early.
          The messages already collected then belong to the mailbox that existed before the drop.

        Both produce ``complete=True`` over a mixture of two mailboxes, and ``complete`` is
        precisely what C14 consults to say the confirmation sample is whole — so the failure lands
        as *"the sample is entire"* rather than as a broken read. The total may therefore not
        decrease, an overrun is refused by name, and the loop ends only on ``len(seen) == total``.
        """
        collected: list[MailMessage] = []
        seen: set[str] = set()
        unparseable = 0
        start = 0
        total = 0
        previous_total: int | None = None
        for page in range(1, _MAX_MAILBOX_PAGES + 1):
            query = f"/api/v1/messages?start={start}&limit={self.page_size}"
            try:
                payload: Any = self._get(query)
            except Exception as exc:
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=f"GET {query} failed: {type(exc).__name__}: {exc}",
                )
            if not isinstance(payload, dict):
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=f"the messages envelope is not an object: {payload!r}",
                )
            reported = self.reported_total(payload)
            if reported is None:
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=(
                        "the messages envelope carried neither `messages_count` nor `total`, so "
                        "this read has nothing to reconcile against — the contract changed"
                    ),
                )
            if previous_total is not None and reported < previous_total:
                # ==A shrinking total closes the read EARLY under `>=`.== Whatever has already been
                # collected was taken from the larger mailbox, so the list and the bound it would
                # be reconciled against describe two different moments.
                return self._incomplete(
                    collected=collected,
                    total=reported,
                    page=page,
                    unparseable=unparseable,
                    why=(
                        f"the reported total DROPPED from {previous_total} to {reported} between "
                        f"pages, so this list mixes two different mailboxes — the {len(seen)} "
                        "messages already read were taken from the larger one"
                    ),
                )
            previous_total = reported
            total = reported
            raw_messages: Any = payload.get("messages")
            if not isinstance(raw_messages, list):
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=f"`messages` is not a list: {raw_messages!r}",
                )
            for item in raw_messages:
                # ==The id is read from the RAW envelope, not from the parsed message.== An
                # envelope this client cannot parse is still a message that exists and still
                # occupies a slot in the total, so it has to be counted as seen or the read can
                # never reconcile; and a page of repeated unparseable envelopes is exactly as
                # broken as a page of repeated good ones.
                identifier = str(item.get("ID", "")) if isinstance(item, dict) else ""
                if not identifier:
                    return self._incomplete(
                        collected=collected,
                        total=total,
                        page=page,
                        unparseable=unparseable,
                        why=(
                            f"an envelope on page {page} carries no `ID`, so this read cannot be "
                            "reconciled against the total the server reports — and the message's "
                            "own source could not be fetched either"
                        ),
                    )
                if identifier in seen:
                    return self._incomplete(
                        collected=collected,
                        total=total,
                        page=page,
                        unparseable=unparseable,
                        why=(
                            f"page {page} (start {start}) repeats message {identifier!r}, which an "
                            f"earlier page already returned. Paging is not advancing, so reaching "
                            f"the reported total of {total} would count the same messages twice "
                            "and call HALF a mailbox a whole one"
                        ),
                    )
                seen.add(identifier)
                message = self.parse(item)
                if message is None:
                    unparseable += 1
                else:
                    collected.append(message)
            if len(seen) > total:
                # ==Read MORE distinct messages than the server says it holds.== Under `>=` this
                # was the completion condition; it is in fact the proof that the list and the total
                # do not describe the same mailbox, and `complete` promises that they do.
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=(
                        f"{len(seen)} distinct messages were returned against a reported total of "
                        f"{total}: the read OVERRAN its own bound, so 'complete' would mean the "
                        "list matches a count it does not match"
                    ),
                )
            start += len(raw_messages)
            if not raw_messages:
                # An empty page BEFORE the reported total means the server and its own envelope
                # disagree; trusting it truncates the mailbox, which is the whole defect.
                if len(seen) == total:
                    return self._whole(collected, total, page, unparseable)
                return self._incomplete(
                    collected=collected,
                    total=total,
                    page=page,
                    unparseable=unparseable,
                    why=f"an empty page at start {start} of a reported total of {total}",
                )
            if len(seen) == total:
                return self._whole(collected, total, page, unparseable)
        return self._incomplete(
            collected=collected,
            total=total,
            page=_MAX_MAILBOX_PAGES,
            unparseable=unparseable,
            why=f"pagination did not terminate after {_MAX_MAILBOX_PAGES} pages",
        )
