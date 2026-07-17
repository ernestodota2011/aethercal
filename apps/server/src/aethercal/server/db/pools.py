"""The worker's TWO pools — and the ONE door to the one that bypasses row-level security.

.. rubric:: Why the worker needs two, and the web needs none

The worker is the process that executes every outbound effect: the email, the WhatsApp, the SMS, the
outbound webhook **carrying the guest's name and address**, and (from the payments batch) the
refunds. It is therefore the place where a cross-business leak would be *externally visible* — one
business's guest arriving at another's webhook. It is also the one place that legitimately has to
look across every business at once, because it cannot know whose work is due until it has looked.

That is the whole shape of it:

* **scan** (``aethercal_worker``, ``BYPASSRLS``) — every query IN THE WORKER whose ``tenant_id`` is
  not yet known, or which is deliberately instance-wide. And nothing else.
* **exec** (``aethercal_app``, RLS APPLIES) — once the item's business IS known, the work happens
  under row-level security with that business bound: read the booking, decrypt its credential, run
  the effect, settle the row.

The web process has neither. It holds one engine, on the app role, and a ``db`` test asserts that no
engine in it carries ``BYPASSRLS`` at all.

.. rubric:: ==The belt is derived from the CODE, not from a list in prose==

An earlier draft closed this question with "exactly these five queries need the bypass". A list is a
photograph, not a belt: three consumers of that very same specification did not fit in it (the
``/metrics`` scrape, the parked-payment tick, and ``/health/ready``). An enumeration of instances
rots the moment somebody adds the sixth.

So the bypass engine is **not reachable**. It is private to :class:`WorkerPools`, and the only way
to
get a session on it is :meth:`WorkerPools.scan_session`, which demands a :class:`BypassReason` — an
exhaustive enum, checked with ``assert_never``. Adding a consumer that needs the bypass means
declaring *why*, in the enum, at the call site. ``tests/test_bypass_belt.py`` asserts over the AST
of
the whole server source that there is no other door: **a new consumer that does not declare its
reason breaks CI.**

.. rubric:: The marker, and why it is not ``SELECT current_user``

``collect_metrics`` only means anything across every business, so it must REFUSE to run on a session
without the bypass — otherwise, under RLS, it fills with zeros and the dead-man switch reports a
healthy queue while the outbox burns. It detects the bypass with a MARKER on ``session.info`` that
only :meth:`WorkerPools.scan_session` sets — never with ``SELECT current_user``, which would be
true to the letter and would also destroy the offline suite: ``collect_metrics`` is exercised in
eleven places over SQLite, where roles do not exist. With the marker the offline suite survives by
marking its fixture (:func:`mark_bypass`), and the belt is derived from the same mechanism as the
enum.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BYPASS_MARKER = "aethercal_bypass_reason"
"""The key on ``session.info`` that says "this session is on the BYPASSRLS pool, deliberately"."""


class BypassReason(StrEnum):
    """Every reason a query may go onto the ``BYPASSRLS`` pool. ==Exhaustive by construction.=="""

    PLAN_OUTBOX = "plan_outbox"
    """``select_due`` — the drain cannot know whose intents are due until it has read them."""

    RECOVER_LEASES = "recover_leases"
    """``recover_expired_leases`` — a dead worker's rows belong to businesses nobody has named."""

    CLAIM_OUTBOX = "claim_outbox"
    """``claim_one`` — and it is NOT optional.

    The claim is ``UPDATE outbox ... WHERE id = :id AND status = 'pending'`` on a row whose
    ``tenant_id`` can only be learned by READING it. On the RLS pool, with no GUC bound yet, that
    UPDATE matches **zero rows** — ``claim_one`` returns ``None``, the drain records "unclaimed",
    and ==the drainer reclaims NOTHING, ever, without raising a single exception.=="""

    PLAN_DELIVERIES = "plan_deliveries"
    """``deliver_due``'s planning query: whose webhooks are due is the answer, not the question."""

    PLAN_CALENDARS = "plan_calendars"
    """``refresh_all_busy_caches``'s planning query over every active external connection."""

    PLAN_PARKED_PAYMENTS = "plan_parked_payments"
    """RESERVED for the payments batch, and declared HERE on purpose.

    A parked payment event cannot travel through the outbox: ``outbox.booking_id`` is NOT NULL and a
    parked event is, by definition, the one whose booking does not exist yet. Without a
    cross-business scan of its own, "it is never discarded" quietly becomes ==**"it is never
    retried"**==, and the dead-letter alarm never fires because nobody is looking."""

    OPERATOR_METRICS = "operator_metrics"
    """``collect_metrics`` — the instance-wide gauges behind ``GET /metrics`` and the worker's
    ``/health/ready``. It is the OPERATOR's view of every business at once, and it is the dead-man
    switch: under RLS without the bypass it would not fail, it would report **zeros**."""


def why_bypass(reason: BypassReason) -> str:  # noqa: PLR0911 - one branch per reason, on purpose
    """One sentence per reason — and, far more importantly, the ``assert_never`` that keeps the enum
    EXHAUSTIVE. A new member with no branch here fails the type check, so the bypass cannot be
    widened by accident: only by a decision somebody had to write down.
    """
    match reason:
        case BypassReason.PLAN_OUTBOX:
            return "planning the outbox drain across every business"
        case BypassReason.RECOVER_LEASES:
            return "recovering leases a dead worker was holding"
        case BypassReason.CLAIM_OUTBOX:
            return "claiming one intent, whose business is known only by reading it"
        case BypassReason.PLAN_DELIVERIES:
            return "planning the outbound-webhook deliveries that are due"
        case BypassReason.PLAN_CALENDARS:
            return "planning the busy-cache refresh over every connected calendar"
        case BypassReason.PLAN_PARKED_PAYMENTS:
            return "re-scanning parked payment events (payments batch)"
        case BypassReason.OPERATOR_METRICS:
            return "collecting the operator's instance-wide metrics"
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


class BypassRequiredError(RuntimeError):
    """A cross-business function was handed a session that is NOT on the bypass pool.

    It FAILS rather than returning zeros. A function whose only meaning is "across every business"
    must not be allowed to degrade quietly into "across none of them" — that is how a readiness
    probe ends up reporting ``outbox.due = 0`` for ever with the queue on fire.
    """


def mark_bypass(session: AsyncSession, reason: BypassReason) -> None:
    """Stamp the bypass marker on ``session``.

    ==Called from exactly ONE place in the source: :meth:`WorkerPools.scan_session`.== A structural
    test asserts it (``tests/test_bypass_belt.py``). Test fixtures may call it too — which is the
    whole point of preferring a marker to ``SELECT current_user``: the eleven offline
    ``collect_metrics`` tests run on SQLite, where roles do not exist, and they mark their fixture
    instead of being rewritten into a ``db``-marked suite.
    """
    session.info[BYPASS_MARKER] = reason


def bypass_reason(session: AsyncSession) -> BypassReason | None:
    """The reason this session was granted the bypass, or ``None`` if it was not."""
    reason = session.info.get(BYPASS_MARKER)
    return reason if isinstance(reason, BypassReason) else None


def require_bypass(session: AsyncSession, *, caller: str) -> None:
    """Refuse unless ``session`` came from :meth:`WorkerPools.scan_session`. ==Fail; never
    fill.=="""
    if bypass_reason(session) is not None:
        return
    raise BypassRequiredError(
        f"{caller} was called on a session that is not on the BYPASSRLS scan pool.\n"
        "\n"
        "It reads across EVERY business by design, so under row-level security it would not fail — "
        "it would quietly return ZEROS. An empty outbox backlog and a ready health check, for "
        "ever, "
        "with the queue on fire. That is precisely the failure this refusal exists to prevent.\n"
        "\n"
        "Obtain the session from `WorkerPools.scan_session(BypassReason.…)` (in the worker), or, "
        "in "
        "an offline test on SQLite, mark the fixture with `mark_bypass(session, …)`."
    )


@dataclass(frozen=True, slots=True)
class WorkerPools:
    """The worker's two session factories. ==The bypass one is PRIVATE; ``scan_session`` is its
    door.

    ``exec_maker`` is handed out freely — it is the app role, under RLS, and can do no harm.
    """

    _scan_maker: async_sessionmaker[AsyncSession]
    """``aethercal_worker`` (``BYPASSRLS``). Never handed out; reachable only through
    :meth:`scan_session`, which forces its caller to name a :class:`BypassReason`."""

    exec_maker: async_sessionmaker[AsyncSession]
    """``aethercal_app`` — RLS APPLIES. A session opened from it inside a
    :func:`~aethercal.server.db.guc.tenant_scope` carries that item's business in its GUC, so the
    effect runs with exactly the authority of the row it came from, and no more."""

    @asynccontextmanager
    async def scan_session(self, reason: BypassReason) -> AsyncGenerator[AsyncSession]:
        """A session on the ``BYPASSRLS`` pool — for a declared, exhaustively enumerated reason."""
        why_bypass(reason)  # exhaustiveness: a new member with no branch fails the type check
        session = self._scan_maker()
        mark_bypass(session, reason)
        try:
            yield session
        finally:
            await session.close()

    @classmethod
    def for_offline_tests(
        cls, sessionmaker: async_sessionmaker[AsyncSession]
    ) -> WorkerPools:  # pragma: no cover - a test-harness constructor, exercised BY the tests
        """Both pools over ONE sessionmaker. ==For the offline SQLite harness, and nowhere else.==

        SQLite has no roles and no row-level security, so there is nothing there to bypass and this
        collapses to what the drain always did. It exists so that several hundred offline outbox
        tests keep running without a PostgreSQL, not as a shortcut anybody may take in the product.

        ``tests/test_bypass_belt.py`` asserts, over the AST of the whole server source, that this
        constructor is **never called from ``apps/*/src``**. Calling it there would hand out a
        session MARKED as bypassed on a pool that is not — and ``collect_metrics``, which trusts
        that
        marker, would go straight back to reporting zeros. The escape hatch is real, so it is nailed
        shut on the side that matters.
        """
        return cls(_scan_maker=sessionmaker, exec_maker=sessionmaker)


__all__ = [
    "BYPASS_MARKER",
    "BypassReason",
    "BypassRequiredError",
    "WorkerPools",
    "bypass_reason",
    "mark_bypass",
    "require_bypass",
    "why_bypass",
]
