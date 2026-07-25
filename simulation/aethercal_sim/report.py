"""Render the run as Markdown: the measured numbers, the controls, and the limits.

.. rubric:: The limits section is generated, not written once

It would be easy to put "what this does not prove" in the README and let every run link to it. That
is exactly how a caveat rots: the harness grows a phase, the README does not, and a report goes out
claiming more than it measured. So the limits travel WITH the numbers, emitted by the same code that
emitted the numbers, and several of them are derived from what the run actually did (whether a
control ran, how many drain samples matched), so they cannot describe a run that did not happen.

==The verdict is deliberately not a grade.== It is one of three words and two of them are bad:

* ``VOID`` — a control FAILED. The run proves nothing, however good the numbers look.
* ``INCOMPLETE`` — a control did not run. Whatever it underwrites is unsupported.
* ``MEASURED`` — every control held, so the instrument was demonstrably biting when the numbers
  were taken.

A simulation cannot say "the product is good". It can only say "these numbers were really taken, and
here is the evidence that the instrument worked while taking them".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .measure import Latency, OutboxSampler
from .scenarios import Control, OrganicResult, RaceOutcome


@dataclass(slots=True)
class RunContext:
    run_id: str
    seed: int
    started_at: str
    finished_at: str
    host_description: str
    stack_description: str
    workers: int
    contenders: int


_VERDICT_BLURB = {
    "MEASURED": (
        "Every control held, so the numbers below were taken by an instrument that was "
        "demonstrably working."
    ),
    "VOID": (
        "A control FAILED. The numbers below are not evidence of anything — a control that does "
        "not bite means the harness was not measuring what it claims to measure."
    ),
    "INCOMPLETE": (
        "A control did not run. The claims it underwrites are unsupported by this run, whatever "
        "the other numbers say."
    ),
}


def _ms(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value):.1f}"


def _latency_row(latency: Latency) -> str:
    summary = latency.summary()
    return (
        f"| `{latency.name}` | {summary['count']} | {_ms(summary['min'])} "
        f"| {_ms(summary['p50'])} | {_ms(summary['p95'])} | {_ms(summary['p99'])} "
        f"| {_ms(summary['max'])} |"
    )


#: ==The controls a run MUST account for, by id.== Not "however many happened to be appended".
#:
#: Every one of these was, at some point, appended inside an `if` — so a missing prerequisite did
#: not produce a failing control, it produced NO control, and the report cheerfully announced
#: "7 of 7 held". A count that is derived from the list it is summarising cannot detect its own
#: omissions: "9 of 9" and "7 of 7" look equally perfect, and the second is a run that never asked
#: two of its questions. Naming the set here makes absence a first-class failure.
#:
#: Adding a control means adding its id here. That is the intended friction.
REQUIRED_CONTROL_IDS: frozenset[str] = frozenset(
    {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12"}
)


def missing_controls(controls: list[Control]) -> list[str]:
    """Required ids that never appeared in the run at all. Sorted, for a stable report."""
    present = {control.ident for control in controls}
    return sorted(REQUIRED_CONTROL_IDS - present, key=lambda ident: (len(ident), ident))


def verdict_for(controls: list[Control]) -> str:
    """VOID beats INCOMPLETE beats MEASURED — the worst true statement wins.

    ==A required control that is ABSENT is VOID, not INCOMPLETE.== "Did not run" is a fact the
    harness reports about itself (``Control.not_run``), and it is honest: the run is INCOMPLETE and
    says which question went unasked. But an id that never reached this list at all means the code
    path meant to ask it was skipped without saying so — a defect in the instrument, which is
    strictly worse than a known gap and must not be softened into one.
    """
    if missing_controls(controls):
        return "VOID"
    if any(control.ran and not control.passed for control in controls):
        return "VOID"
    if any(not control.ran for control in controls):
        return "INCOMPLETE"
    return "MEASURED"


def render(  # noqa: PLR0913, PLR0912, PLR0915 - a report IS every instrument's reading, in order
    *,
    context: RunContext,
    plan_summary: dict[str, Any],
    organic: OrganicResult,
    races: list[RaceOutcome],
    controls: list[Control],
    sampler: OutboxSampler,
    drain_stats: dict[str, float],
    drain_latency: Latency,
    no_show_outcome: str,
    sink_counts: dict[str, int],
    sink_unreadable: int,
    mail_total: int,
    mail_unparseable: int,
    drained: bool,
    drain_wait_seconds: float,
) -> str:
    verdict = verdict_for(controls)
    not_run = [control for control in controls if not control.ran]
    absent = missing_controls(controls)

    lines: list[str] = []
    add = lines.append

    add(f"# AetherCal — two-week load simulation, run `{context.run_id}`")
    add("")
    add(f"**Verdict: {verdict}.** {_VERDICT_BLURB[verdict]}")
    add("")
    add(f"- Seed `{context.seed}` — the plan is deterministic: same seed, same load")
    add(f"- Started `{context.started_at}` · finished `{context.finished_at}`")
    add(f"- Host: {context.host_description}")
    add(f"- Stack: {context.stack_description}")
    add(
        f"- Organic concurrency: {context.workers} simultaneous guests · "
        f"adversarial burst: {context.contenders} contenders"
    )
    add("")

    # ---- 1. volume ----
    add("## 1. Volume — what two simulated weeks looked like")
    add("")
    add(
        f"- Planned bookings: **{plan_summary['total']}**, over "
        f"**{plan_summary['distinct_days']}** weekdays"
    )
    add(f"- By business: `{plan_summary['by_business']}`")
    add(f"- By locale: `{plan_summary['by_locale']}`")
    add(f"- By event type: `{plan_summary['by_event_type']}`")
    add(
        f"- By weekday (0=Mon): `{plan_summary['by_weekday']}` — **no weekend entries: the "
        "organic schedule is Mon-Fri, and a closed day offers nothing to book**"
    )
    add("")
    add(f"- Bookings **created**: **{len(organic.booked)}**")
    add(f"- Cancelled: **{organic.cancelled}** · rescheduled: **{organic.rescheduled}**")
    add(
        f"- Organic collisions (two guests, one slot → `slot_unavailable`): "
        f"**{organic.collisions}**"
    )
    add(f"- Attempts that met a fully-booked day (no slots offered): {organic.no_slots_offered}")
    add("")

    # ---- 2. latency ----
    add("## 2. Latency (milliseconds, measured client-side, nearest-rank percentiles)")
    add("")
    add("| path | n | min | p50 | p95 | p99 | max |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for latency in (
        organic.slots_latency,
        organic.booking_latency,
        organic.cancel_latency,
        organic.reschedule_latency,
    ):
        if latency.count:
            add(_latency_row(latency))
    for race in races:
        if race.latency.count:
            add(_latency_row(race.latency))
    if drain_latency.count:
        add(_latency_row(drain_latency))
    add("")
    add(
        "`booking_create` is the guest's own wait for a 201. "
        "`booking_to_confirmation_email` is a **different and slower** question: it crosses the "
        "outbox and is produced by the worker, not by the request that returned 201."
    )
    add("")

    # ---- 3. the outbox ----
    add("## 3. The outbox and the drain")
    add("")
    add(
        f"- Backlog samples taken: **{len(sampler.samples)}** "
        f"(scrape failures: **{len(sampler.failures)}**)"
    )
    add(f"- **Peak `due` backlog: {sampler.peak_due()}**")
    add(f"- **Peak oldest-due age: {sampler.peak_oldest_age():.1f}s**")
    add(
        f"- Drained to zero at the end: **{'yes' if drained else 'NO'}** "
        f"(waited {drain_wait_seconds:.1f}s)"
    )
    if drain_stats:
        add(f"- Dead-man control figures: `{drain_stats}`")
    add(
        f"- Confirmation emails in the mailbox: **{mail_total}** "
        f"(unreadable timestamps: {mail_unparseable})"
    )
    add(
        f"- Webhook deliveries captured at the sink, by event: `{sink_counts}` "
        f"(unreadable payloads: {sink_unreadable})"
    )
    add("")
    if sampler.failures:
        add(
            "> [!warning] Some scrapes FAILED. Every backlog number above is computed only over "
            "the samples that succeeded, so the true peak may be higher than reported."
        )
        add("")

    # ---- 4. concurrency ----
    add("## 4. Adversarial concurrency — the part real traffic never produces")
    add("")
    add(
        "Each burst is released by a `threading.Barrier`: N threads block until all N are ready, "
        "then fire together. Two weeks of organic bookings would never collide like this."
    )
    add("")
    add("| race | contenders | winners | refusals | by code |")
    add("|---|---:|---:|---:|---|")
    for race in races:
        add(
            f"| `{race.name}` | {race.contenders} | **{race.winners}** | {race.refusals} "
            f"| `{race.refusals_by_code}` |"
        )
    add("")
    unexpected = [(race.name, message) for race in races for message in race.unexpected]
    if unexpected:
        add(
            "> [!danger] Responses that were neither a success nor a clean domain conflict. "
            "A 5xx or a transport failure under load is a finding, not a tidy table row:"
        )
        for name, message in unexpected:
            add(f"> - `{name}`: {message}")
        add("")
    add(f"- No-show transition (against a real end time, not a clock trick): **{no_show_outcome}**")
    add("")

    # ---- 5. controls ----
    add("## 5. Controls — why any of the above is believable")
    add("")
    add(
        "==A control that comes back green when it should have failed voids the run.== "
        "These are the cases that MUST be refused; without them the numbers above are anecdote."
    )
    add("")
    add(
        f"Required control set: **{len(REQUIRED_CONTROL_IDS)}** ids "
        f"(`{', '.join(sorted(REQUIRED_CONTROL_IDS, key=lambda i: (len(i), i)))}`). "
        f"Accounted for in this run: **{len(controls)}**."
    )
    add("")
    add("| id | guards | expected | observed | held |")
    add("|---|---|---|---|:--:|")
    for control in sorted(controls, key=lambda item: (len(item.ident), item.ident)):
        mark = "⏭️" if not control.ran else ("✅" if control.passed else "❌")
        add(
            f"| **{control.ident}** | {control.guards} | {control.expected} "
            f"| {control.observed} | {mark} |"
        )
    add("")
    if absent:
        add(
            f"> [!danger] ==**{len(absent)} required control(s) never appeared in this run at "
            f"all: {', '.join(absent)}.**== That is not a gap the harness reported — it is a gap "
            "the harness FAILED to report, which is why this run is VOID rather than INCOMPLETE. "
            "A pass count derived from the list it summarises cannot see its own omissions."
        )
        add("")

    # ---- 6. errors ----
    add("## 6. Error taxonomy (every outcome, successes included)")
    add("")
    add(
        f"Total classified responses: **{organic.tally.total()}** · "
        f"non-2xx: **{organic.tally.failures()}**"
    )
    add("")
    add("| status | machine code | count |")
    add("|---:|---|---:|")
    for status, code, count in organic.tally.rows():
        add(f"| {status} | `{code}` | {count} |")
    add("")

    # ---- 7. limits ----
    add("## 7. Limits — what this dataset does and does NOT support")
    add("")
    add("### It DOES support these claims")
    add("")
    add(
        "These are **mechanical guarantees**: properties of the code under concurrency. A "
        "compressed simulation tests them at least as harshly as calendar time would — harder, "
        "in fact, because real traffic never collides on purpose."
    )
    add("")
    add(
        "- **No double-booking under simultaneous contention.** Barrier-released bursts at one "
        "slot left exactly one winner, and the distinct-slot control (C2) proves the oracle could "
        "have seen more than one."
    )
    add(
        "- **Mutations are serialised, checked on the DIARY rather than on status codes.** "
        "Concurrent cancels of one booking emit exactly one `booking.cancelled` (C7); a reschedule "
        "race leaves exactly one successor (C8); a cancel racing a reschedule never leaves two "
        "live appointments (C9). ==The winner counts in §4 are not the oracle for the last two==: "
        "a cancel and a reschedule may BOTH legitimately answer 200, because the reschedule swaps "
        "in a successor and the cancel then finds the predecessor already cancelled and is an "
        "idempotent no-op. What must never happen is two live appointments, so that is what is "
        "asserted."
    )
    add(
        "- **Latency under this load, on this hardware.** The distributions in §2, at the sample "
        "sizes shown there."
    )
    add(
        "- **The drain keeps up, and a dead drain would be visible.** Peak backlog in §3, with the "
        "dead-man control (C5) proving the metric is live rather than a constant zero."
    )
    add(
        "- **Both locales and several guest timezones exercise the same paths** without a distinct "
        "error class appearing in §6."
    )
    add("")
    add("### It does NOT support these — by construction, not by omission")
    add("")
    add(
        '- ==**"≥5 bookings from real humans."** That is a fact about ADOPTION, and no volume of '
        "synthetic load can stand in for it.== Every booking here was made by this harness against "
        "a throwaway database. The rubric's human-uptake criterion is untouched by this run and "
        "must still be met by the pilot itself."
    )
    add(
        '- ==**"Two weeks elapsed."** The run compresses two weeks of VOLUME into minutes; it '
        "does not compress two weeks of TIME.== Nothing here can see certificate expiry, "
        "log-rotation faults, a cron that drifts, a slow memory leak, connection churn over days, "
        "a 24h reminder actually firing, or a DST transition. Duration-dependent failure is what a "
        "soak test finds, and this is not one."
    )
    add(
        "- **Real-world demand shape.** The weekday weights are plausible, not fitted — five pilot "
        "bookings cannot fit a distribution. Read §1 as a load shape, never as a forecast."
    )
    add(
        "- **Real email deliverability.** Mail lands in Mailpit over plaintext SMTP on the same "
        "host. This says nothing about SPF/DKIM/DMARC, spam placement, or whether any real inbox "
        "would accept the message."
    )
    add(
        "- **Real network conditions.** No reverse proxy, no TLS termination, no WAN latency, no "
        "CDN. Client and server share a host, so §2 is a floor for the product's own work — not "
        "what a guest in another city experiences."
    )
    add(
        "- **The booking PAGE.** The harness drives the API. Front-end budgets (LCP/CLS/INP) and "
        "the browser journey are measured elsewhere (`e2e/`, the CWV guard) and are out of scope."
    )
    add(
        "- **External calendar integrations.** No Google or CalDAV connection is configured, so "
        "the busy-cache refresh path and its degraded/unavailable modes are not exercised."
    )
    add("- **Payments.** No payment provider is configured; the money paths are untouched.")
    add(
        "- **Multi-worker safety.** The stack runs the shipped `replicas: 1`. This says nothing "
        "about what two workers would do — which the product documents as unsafe by design."
    )
    add(
        "- ==**A DEAD worker is not visible in these numbers.**== `/metrics/summary` is served BY "
        "the worker, so while the drain is down the backlog does not climb — the endpoint is "
        "unreachable (`Connection refused`). C5 confirms that directly. The backlog metric "
        "therefore detects a drain that is FALLING BEHIND, not one that is GONE; the second needs "
        "external liveness monitoring, and nothing in §3 substitutes for it."
    )
    if not_run:
        add(
            f"- ==**Controls that did NOT run in this execution:** "
            f"{', '.join(control.ident for control in not_run)}.== Whatever they underwrite is "
            "unsupported here."
        )
    add("")
    add("### Honest reading of the sample sizes")
    add("")
    add(
        f"The p99 figures in §2 rest on the counts in that table. A p99 over "
        f"{organic.booking_latency.count} observations is one or two slow requests, not a stable "
        "tail estimate; p50 and p95 are the trustworthy columns at this volume."
    )
    if drain_latency.count < len(organic.booked):
        add("")
        add(
            f"> [!warning] Drain latency has **{drain_latency.count}** samples for "
            f"**{len(organic.booked)}** bookings. The rest could not be matched to a message in "
            "the mailbox, so §2's `booking_to_confirmation_email` row describes the matched "
            "subset only."
        )
    add("")
    return "\n".join(lines)


def render_json(payload: dict[str, Any]) -> str:
    """The same run as machine-readable JSON, for diffing one run against the next."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)
