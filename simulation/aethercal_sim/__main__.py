"""Run the two-week simulation end to end and write the report.

    python -m aethercal_sim --compose-cmd "docker compose -f ... -f ... -f ..."

The phases run in this order, and the order is deliberate:

1. **Provision** the world (several businesses, four event types each, a webhook per business).
2. **Organic load** — the two-week plan, executed by a pool of simultaneous guests.
3. **Adversarial concurrency** — the barrier-released races, plus the distinct-slot control that
   audits the race oracle itself.
4. **Controls** — every claim's negative case, including the drain dead-man, which stops the worker.
5. **Drain** — wait for the outbox to empty, then match confirmations to bookings for the
   booking→confirmation latency.

The dead-man control comes late on purpose: it deliberately stops the worker, and doing that in the
middle of the organic phase would fold a self-inflicted stall into the very backlog figures the run
is trying to report.

==The drain control has to stop and start a container, so it needs the compose command.== Without
``--compose-cmd`` the run REFUSES to start rather than quietly reporting a backlog nobody proved was
real. ``--allow-missing-drain-control`` is the explicit, loud escape: C5 is then reported as NOT RUN
and the whole run is INCOMPLETE.
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .client import Client
from .measure import Latency, Mailbox, OutboxSampler, wait_for_drain
from .report import RunContext, render, render_json, verdict_for
from .scenarios import (
    Control,
    control_closed_day,
    control_day_cap,
    control_drain_deadman,
    control_lineage_after_race,
    control_no_show_before_end,
    control_outbox_drained,
    control_single_winner,
    control_taken_slot,
    count_sink_events,
    fetch_slots,
    next_saturday,
    pick_micro_slot,
    race_cancel,
    race_cancel_vs_reschedule,
    race_distinct_slots,
    race_reschedule,
    race_same_slot,
    run_organic,
    sink_events_for_booking,
    slot_starts,
)
from .scenarios import book as book_slot
from .traffic import plan_two_weeks, summarise_plan
from .world import load_stack, provision

#: How Spanish-leaning each business's guests are. The third is deliberately bilingual.
LOCALE_MIX = {"clinica-sonrisa": 0.85, "katy-hvac": 0.15, "estudio-legal": 0.5}

#: Slots the adversarial phase needs beyond the burst itself: one for the taken-slot control and
#: three subjects for the mutation races.
_SPARE_SLOTS = 4

#: How long the run will wait for a micro appointment to genuinely end before giving up on the
#: no-show leg. A budget, never a truncation: a slot that does not fit is not chosen at all.
NO_SHOW_WAIT_BUDGET_SECONDS = 420.0

_DEFAULT_STACK_FILE = Path(__file__).resolve().parent.parent / ".stack.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aethercal_sim", description="Two-week load simulation.")
    parser.add_argument("--stack-file", type=Path, default=_DEFAULT_STACK_FILE)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--workers", type=int, default=8, help="simultaneous simulated guests")
    parser.add_argument("--contenders", type=int, default=40, help="threads per adversarial race")
    parser.add_argument("--per-week", type=int, default=40, help="bookings per business per week")
    parser.add_argument("--compose-cmd", default="", help="docker compose invocation (for C5)")
    parser.add_argument("--allow-missing-drain-control", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("simulation-report.md"))
    parser.add_argument("--json-out", type=Path, default=Path("simulation-report.json"))
    return parser.parse_args(argv)


def _compose(compose_cmd: str, metrics_token: str, *args: str) -> None:
    """Run a compose subcommand on the simulation project.

    ==The operator token has to be in the ENVIRONMENT of this call.== ``compose.sim.yml`` declares
    ``AETHERCAL_METRICS_TOKEN: ${AETHERCAL_SIM_METRICS_TOKEN:?…}`` — required, with no default, so
    that the worker and the harness can never hold different strings. The harness inherits its shell
    from whoever launched it and that variable is normally NOT set there, so compose would refuse
    the command and the dead-man control would die with an unhelpful exit 1.

    The token is passed through from ``.stack.json``, which ``stack-up.sh`` wrote from the same
    variable it exported to compose in the first place: still one decision, carried rather than
    re-derived.

    ``stderr`` is surfaced on failure. The first version of this swallowed it and reported only
    "exit status 1" for what was really a legible message from compose about a missing variable.
    """
    result = subprocess.run(  # noqa: PLW1510 - returncode is inspected below, with stderr attached
        [*shlex.split(compose_cmd), *args],
        capture_output=True,
        timeout=180,
        text=True,
        env={**os.environ, "AETHERCAL_SIM_METRICS_TOKEN": metrics_token},
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`docker compose {' '.join(args)}` failed ({result.returncode}):\n"
            f"{result.stderr.strip()[:800]}"
        )


def _host_description() -> str:
    return (
        f"{platform.node()} · {platform.system()} {platform.release()} · "
        f"python {platform.python_version()}"
    )


_MISSING_COMPOSE = """ERROR: --compose-cmd is required.

The drain dead-man control (C5) stops and restarts the worker container, and it is what makes every
backlog number in this report falsifiable: without it, an instrument wired to a constant zero draws
exactly the same flat, healthy graph as a queue that is genuinely keeping up.

Pass the compose invocation, e.g.
  --compose-cmd "docker compose -f deploy/docker-compose.yml -f e2e/compose.e2e.yml \
-f simulation/compose.sim.yml"

or pass --allow-missing-drain-control to run anyway; C5 is then reported as NOT RUN and the
verdict becomes INCOMPLETE."""


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915, PLR0912 - a run IS its phases
    args = parse_args(argv)
    if not args.compose_cmd and not args.allow_missing_drain_control:
        print(_MISSING_COMPOSE, file=sys.stderr)
        return 2

    run_id = uuid.uuid4().hex[:8]
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    stack = load_stack(args.stack_file)

    mailbox = Mailbox(stack.mailpit_url)
    mailbox.purge()
    Client(stack.sink_url).request("DELETE", "/_captured")

    print(f"==> run {run_id}: provisioning {len(stack.businesses)} businesses")
    world = provision(stack, run_id=run_id)

    sampler = OutboxSampler(stack.worker_url, stack.metrics_token, interval_seconds=0.5)
    sampler.start()

    # ---- Phase 1: the organic two-week load -------------------------------------------------
    # The window opens on the next Monday, so a 14-day plan covers exactly two working weeks.
    today = date.today()
    window_start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    plan = plan_two_weeks(
        business_slugs=[business.slug for business in world.businesses],
        locale_mix=LOCALE_MIX,
        start=window_start,
        seed=args.seed,
        bookings_per_business_per_week=args.per_week,
    )
    summary = summarise_plan(plan)
    print(f"==> phase 1: {summary['total']} planned bookings, {args.workers} concurrent guests")
    organic = run_organic(world, stack, plan, workers=args.workers, seed=args.seed)
    print(
        f"    created {len(organic.booked)} · cancelled {organic.cancelled} · "
        f"rescheduled {organic.rescheduled} · organic collisions {organic.collisions}"
    )

    # ---- Phase 2: adversarial concurrency ---------------------------------------------------
    print(f"==> phase 2: adversarial races, {args.contenders} contenders")
    target = world.businesses[0]
    client = Client(stack.api_url, target.config.api_key)
    races = []
    controls: list[Control] = []
    cancel_race_events: dict[str, int] = {}

    # A far-future week nothing else in the run has touched, so each race starts from clean slots.
    race_day = window_start + timedelta(days=21)
    offer = fetch_slots(
        client,
        event_type_id=target.event_types["standard"].id,
        day=race_day,
        timezone=target.config.timezone,
        days=5,
    )
    starts = slot_starts(offer)
    needed = args.contenders + _SPARE_SLOTS
    if len(starts) < needed:
        print(f"ERROR: {len(starts)} slots on offer, need {needed}", file=sys.stderr)
        sampler.stop()
        return 2

    same_slot = race_same_slot(stack, target, start=starts[0], contenders=args.contenders)
    races.append(same_slot)
    print(f"    same slot: {same_slot.winners} winner(s) of {same_slot.contenders}")
    # ==C10 — THE claim of the whole report, wired to the verdict.== Until this existed the
    # same-slot race lived in a table and nowhere else: the run would have stamped MEASURED with
    # five winners on one slot, because nothing ever compared its result against anything.
    controls.append(
        control_single_winner(
            same_slot,
            ident="C10",
            guards="RF-04 itself: N simultaneous bookings of ONE slot leave exactly one",
            expected_refusal="slot_unavailable",
        )
    )

    # ==The control for the oracle.== Same burst, same code path, N different slots → N winners.
    distinct = race_distinct_slots(
        stack, target, starts=starts[1 : 1 + args.contenders], tag=run_id
    )
    races.append(distinct)
    controls.append(
        Control(
            ident="C2",
            guards="the race oracle itself: it CAN see more than one winner",
            expected=f"{distinct.contenders} winners on {distinct.contenders} distinct slots",
            observed=f"{distinct.winners} winners, refusals {distinct.refusals_by_code}",
            passed=distinct.winners == distinct.contenders,
        )
    )
    print(f"    distinct slots (control): {distinct.winners} winner(s) of {distinct.contenders}")

    # C1 — the slot the same-slot race just filled must now be refused.
    controls.append(control_taken_slot(stack, target, start=starts[0]))

    def subject(index: int, label: str) -> str | None:
        """Book one fresh booking for a mutation race; ``None`` if it could not be created."""
        response = book_slot(
            client,
            event_type_id=target.event_types["standard"].id,
            start=starts[index],
            guest_name=f"{label} Race Subject",
            guest_email=f"{label.lower()}.subject.{run_id}@guests.sim.test",
            guest_timezone="UTC",
            locale="en",
        )
        return str(response.body["id"]) if response.ok else None

    # ==Every control below is appended on EVERY path.== They used to live inside the `if` that
    # created their subject, so a booking that failed to be created did not produce a failing
    # control — it produced no control at all, and the report announced "7 of 7 held" for a run
    # that had quietly stopped asking two of its questions. A missing prerequisite is now a
    # NOT RUN row (and an INCOMPLETE verdict), which is a fact; silence was not.
    _C7 = "an idempotent cancel emits exactly ONE booking.cancelled webhook"
    _C8 = "a reschedule race leaves ONE successor, not two live appointments"
    _C9 = "a cancel racing a reschedule never leaves TWO live appointments"

    cancel_id = subject(args.contenders + 1, "Cancel")
    if cancel_id is None:
        controls.append(
            Control.not_run("C7", _C7, "exactly 1 booking.cancelled", "its subject never booked")
        )
    else:
        races.append(race_cancel(stack, target, booking_id=cancel_id, contenders=args.contenders))
        # Let the drain deliver whatever was queued, then ask the sink the question that matters.
        time.sleep(12)
        cancel_race_events = sink_events_for_booking(stack.sink_url, cancel_id)
        controls.append(
            Control(
                ident="C7",
                guards=_C7,
                expected="exactly 1 booking.cancelled at the sink for this booking",
                observed=f"{cancel_race_events}",
                passed=cancel_race_events.get("booking.cancelled", 0) == 1,
            )
        )

    reschedule_id = subject(args.contenders + 2, "Reschedule")
    later_starts: list[str] = []
    if reschedule_id is not None:
        later_starts = slot_starts(
            fetch_slots(
                client,
                event_type_id=target.event_types["standard"].id,
                day=race_day + timedelta(days=7),
                timezone=target.config.timezone,
                days=5,
            )
        )[: args.contenders]
    if reschedule_id is None or len(later_starts) < 2:
        why = (
            "its subject never booked"
            if reschedule_id is None
            else f"only {len(later_starts)} target slots on offer, need 2"
        )
        controls.append(Control.not_run("C8", _C8, "exactly 1 successor survives", why))
    else:
        races.append(race_reschedule(stack, target, booking_id=reschedule_id, starts=later_starts))
        # ==What the race LEFT BEHIND, which is the invariant that matters.== One HTTP winner is
        # not the contract; one live appointment is.
        controls.append(
            control_lineage_after_race(
                stack,
                target,
                ident="C8",
                guards=_C8,
                guest_email=f"reschedule.subject.{run_id}@guests.sim.test",
                date_from=race_day,
                date_to=race_day + timedelta(days=28),
            )
        )

    mixed_id = subject(args.contenders + 3, "Mixed")
    spare_starts: list[str] = []
    if mixed_id is not None:
        spare_starts = slot_starts(
            fetch_slots(
                client,
                event_type_id=target.event_types["standard"].id,
                day=race_day + timedelta(days=14),
                timezone=target.config.timezone,
                days=5,
            )
        )
    if mixed_id is None or not spare_starts:
        why = "its subject never booked" if mixed_id is None else "no target slot on offer"
        controls.append(Control.not_run("C9", _C9, "at most 1 active booking survives", why))
    else:
        races.append(
            race_cancel_vs_reschedule(stack, target, booking_id=mixed_id, start=spare_starts[0])
        )
        # ==Both calls answering 200 here is CORRECT, so the winner count is not the oracle.== The
        # reschedule swaps in a successor; the cancel then finds the predecessor already cancelled
        # and is an idempotent no-op. Either order is legitimate — what must never happen is the
        # guest ending up holding two live appointments.
        controls.append(
            control_lineage_after_race(
                stack,
                target,
                ident="C9",
                guards=_C9,
                guest_email=f"mixed.subject.{run_id}@guests.sim.test",
                date_from=race_day,
                date_to=race_day + timedelta(days=28),
                at_most=True,
            )
        )

    # ---- Phase 3: the remaining controls ----------------------------------------------------
    print("==> phase 3: controls")
    controls.append(control_closed_day(stack, target, saturday=next_saturday(window_start)))
    controls.append(control_day_cap(stack, target, day=window_start + timedelta(days=35)))

    # ---- The no-show leg: an appointment that REALLY ends inside this run --------------------
    #
    # ==The slot is chosen by when it ENDS, and the wait is never truncated.== This used to take
    # the first offered slot and then cap the wait with `min(...)`: if that slot ended after the
    # cap, the harness stopped waiting early, marked the no-show anyway, and filed whatever came
    # back as the outcome — so a `409 not_ended`, which is the guard correctly refusing, would have
    # been recorded as the result of a POSITIVE test. Picking by end time removes the need for a
    # cap: either a slot fits the budget and is waited out in full, or none does and C11 says NOT
    # RUN.
    _C6 = "the no-show guard: an appointment that has not ended cannot be a no-show"
    _C11 = "the no-show transition itself, against a real end time"
    no_show_outcome = "not attempted"
    micro = target.event_types["micro"]
    micro_start = pick_micro_slot(
        slot_starts(
            fetch_slots(client, event_type_id=micro.id, day=date.today(), timezone="UTC", days=2)
        ),
        duration_seconds=micro.duration_seconds,
        budget_seconds=NO_SHOW_WAIT_BUDGET_SECONDS,
        now=datetime.now(UTC),
    )
    micro_booking = (
        book_slot(
            client,
            event_type_id=micro.id,
            start=micro_start,
            guest_name="No Show Subject",
            guest_email=f"noshow.{run_id}@guests.sim.test",
            guest_timezone="UTC",
            locale="en",
        )
        if micro_start is not None
        else None
    )
    if micro_booking is None or not micro_booking.ok:
        why = (
            f"no offered slot ends within {NO_SHOW_WAIT_BUDGET_SECONDS:.0f}s"
            if micro_booking is None
            else f"the micro slot could not be booked ({micro_booking.status})"
        )
        no_show_outcome = f"NOT RUN — {why}"
        controls.append(Control.not_run("C6", _C6, "409 not_ended", why))
        controls.append(Control.not_run("C11", _C11, "200 and status becomes no_show", why))
    else:
        micro_id = str(micro_booking.body["id"])
        # C6 first: while the appointment is still running, a no-show must be REFUSED.
        controls.append(control_no_show_before_end(stack, target, booking_id=micro_id))
        end_at = datetime.fromisoformat(str(micro_booking.body["end"]).replace("Z", "+00:00"))
        wait = (end_at - datetime.now(UTC)).total_seconds() + 5
        if wait > 0:
            print(f"    waiting {wait:.0f}s for the micro appointment to really end")
            time.sleep(wait)
        marked = client.post(f"/api/v1/bookings/{micro_id}/no-show")
        # ==Assert the EFFECT, not just the status.== A 200 whose row is still `confirmed` would
        # be a silent no-op, which is this repo's signature defect; so the booking is re-read.
        after = client.get(f"/api/v1/bookings/{micro_id}")
        final_status = str(after.body.get("status")) if isinstance(after.body, dict) else "unknown"
        no_show_outcome = f"{marked.status} {marked.error_code or 'ok'} → status={final_status}"
        controls.append(
            Control(
                ident="C11",
                guards=_C11,
                expected="200, and the booking's status really becomes no_show",
                observed=no_show_outcome,
                passed=marked.ok and final_status == "no_show",
            )
        )

    # C5 — the drain dead-man. It stops the worker, so it runs last.
    drain_stats: dict[str, float] = {}
    if args.compose_cmd:
        print("==> control C5: stopping the worker to prove the backlog metric is live")

        def make_work() -> None:
            offer_now = fetch_slots(
                client,
                event_type_id=target.event_types["standard"].id,
                day=window_start + timedelta(days=28),
                timezone=target.config.timezone,
                days=3,
            )
            for index, start in enumerate(slot_starts(offer_now)[:6]):
                book_slot(
                    client,
                    event_type_id=target.event_types["standard"].id,
                    start=start,
                    guest_name=f"Deadman Probe {index}",
                    guest_email=f"deadman.{run_id}.{index}@guests.sim.test",
                    guest_timezone="UTC",
                    locale="en",
                )

        control, drain_stats = control_drain_deadman(
            sampler,
            stop_worker=lambda: _compose(args.compose_cmd, stack.metrics_token, "stop", "worker"),
            start_worker=lambda: _compose(args.compose_cmd, stack.metrics_token, "start", "worker"),
            make_work=make_work,
        )
        controls.append(control)
    else:
        controls.append(
            Control.not_run(
                "C5",
                "the backlog metric is LIVE (a dead drain is visible, and recovers)",
                "due climbs while the worker is stopped, then returns to 0",
                "no --compose-cmd was given, so the worker could not be stopped",
            )
        )

    # ---- Drain, then measure booking → confirmation ------------------------------------------
    print("==> waiting for the outbox to drain")
    drained, drain_wait = wait_for_drain(sampler, timeout_seconds=300.0)
    sampler.stop()
    # ==C12 — a failed drain now INVALIDATES the run.== `drained` was computed, printed in §3 and
    # then ignored: a run whose queue never emptied still stamped MEASURED, while the
    # booking→confirmation figures silently described only the messages that escaped. Scrape
    # failures gate too, because a backlog series with holes in it understates its own peak. (C5's
    # deliberate outage pauses the sampler, so those never land in this count.)
    controls.append(
        control_outbox_drained(
            drained=drained,
            waited_seconds=drain_wait,
            scrape_failures=len(sampler.failures),
        )
    )

    drain_latency = Latency("booking_to_confirmation_email")
    first_seen: dict[str, float] = {}
    for message in mailbox.messages():
        stamp = message.created.timestamp()
        if message.to not in first_seen or stamp < first_seen[message.to]:
            first_seen[message.to] = stamp
    for ref in organic.booked:
        stamp = first_seen.get(ref.guest_email.lower())
        if stamp is None:
            continue
        delta_ms = (stamp - ref.created_at_wall) * 1000.0
        # A negative delta means the two clocks disagree, not that mail arrived before it was sent.
        # Recording it would silently drag the whole distribution down.
        if delta_ms >= 0:
            drain_latency.record(delta_ms)

    sink_counts, sink_unreadable = count_sink_events(stack.sink_url)

    context = RunContext(
        run_id=run_id,
        seed=args.seed,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        host_description=_host_description(),
        stack_description=(
            "throwaway docker compose project `aethercal-sim` "
            "(deploy/docker-compose.yml + e2e/compose.e2e.yml + simulation/compose.sim.yml)"
        ),
        workers=args.workers,
        contenders=args.contenders,
    )
    args.out.write_text(
        render(
            context=context,
            plan_summary=summary,
            organic=organic,
            races=races,
            controls=controls,
            sampler=sampler,
            drain_stats=drain_stats,
            drain_latency=drain_latency,
            no_show_outcome=no_show_outcome,
            sink_counts=sink_counts,
            sink_unreadable=sink_unreadable,
            mail_total=mailbox.count(),
            mail_unparseable=mailbox.unparseable,
            drained=drained,
            drain_wait_seconds=drain_wait,
        ),
        encoding="utf-8",
    )
    args.json_out.write_text(
        render_json(
            {
                "run_id": run_id,
                "seed": args.seed,
                "verdict": verdict_for(controls),
                "plan": summary,
                "created": len(organic.booked),
                "cancelled": organic.cancelled,
                "rescheduled": organic.rescheduled,
                "organic_collisions": organic.collisions,
                "latency": {
                    latency.name: latency.summary()
                    for latency in (
                        organic.slots_latency,
                        organic.booking_latency,
                        organic.cancel_latency,
                        organic.reschedule_latency,
                        drain_latency,
                    )
                },
                "races": [
                    {
                        "name": race.name,
                        "contenders": race.contenders,
                        "winners": race.winners,
                        "refusals": race.refusals_by_code,
                        "unexpected": race.unexpected,
                    }
                    for race in races
                ],
                "controls": [
                    {
                        "id": control.ident,
                        "passed": control.passed,
                        "ran": control.ran,
                        "observed": control.observed,
                    }
                    for control in controls
                ],
                "outbox": {
                    "peak_due": sampler.peak_due(),
                    "peak_oldest_age_seconds": sampler.peak_oldest_age(),
                    "samples": len(sampler.samples),
                    "scrape_failures": len(sampler.failures),
                    "drained": drained,
                    "deadman": drain_stats,
                },
                "sink": sink_counts,
                "cancel_race_sink_events": cancel_race_events,
                "errors": [
                    {"status": status, "code": code, "count": count}
                    for status, code, count in organic.tally.rows()
                ],
            }
        ),
        encoding="utf-8",
    )

    verdict = verdict_for(controls)
    print(f"==> {verdict}: wrote {args.out} and {args.json_out}")
    # A VOID run must not exit 0 — it would go green in any pipeline that ever wraps this.
    return 0 if verdict == "MEASURED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
