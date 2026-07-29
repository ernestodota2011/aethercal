"""Run the two-week simulation end to end and write the report.

    python -m aethercal_sim

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

==The drain control has to stop and start a container, so it needs a compose command — and that
command is DERIVED, never accepted.== See :data:`COMPOSE_FILES`.
``--allow-missing-drain-control`` is the explicit, loud escape: C5 is then reported as NOT RUN and
the whole run is INCOMPLETE.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .client import Client
from .measure import (
    Latency,
    Mailbox,
    MailboxRead,
    MailMessage,
    OutboxSampler,
    wait_for_drain,
)
from .report import RunContext, render, render_json, verdict_for
from .scenarios import (
    MIXED_RACE_REFUSALS,
    RESCHEDULE_RACE_REFUSALS,
    BookedRef,
    ConfirmationCoverage,
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
    judge_cancel_idempotency,
    judge_confirmation_coverage,
    judge_organic_accounting,
    judge_race_concurrency,
    next_saturday,
    observe_cancel_webhooks,
    pick_micro_slot,
    race_cancel,
    race_cancel_vs_reschedule,
    race_distinct_slots,
    race_reschedule,
    race_same_slot,
    run_organic,
    slot_starts,
)
from .scenarios import book as book_slot
from .traffic import plan_two_weeks, summarise_plan
from .world import (
    NotADisposableStackError,
    StackConfig,
    assert_disposable_stack,
    load_stack,
    provision,
)

#: How Spanish-leaning each business's guests are. The third is deliberately bilingual.
LOCALE_MIX = {"clinica-sonrisa": 0.85, "katy-hvac": 0.15, "estudio-legal": 0.5}

#: Slots the adversarial phase needs beyond the burst itself: one for the taken-slot control and
#: three subjects for the mutation races.
_SPARE_SLOTS = 4

#: How long the run will wait for a micro appointment to genuinely end before giving up on the
#: no-show leg. A budget, never a truncation: a slot that does not fit is not chosen at all.
NO_SHOW_WAIT_BUDGET_SECONDS = 420.0

#: The directory this harness lives in, and the repository that holds it. Everything below is
#: derived from these two, because every one of them is a fact about the layout rather than a
#: decision an invocation gets to make.
SIM_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SIM_DIR.parent

#: ==Fixed, not a flag.== `--stack-file` used to let any path be passed, which meant nothing
#: structural stopped this harness — whose first acts are to purge a mailbox, create businesses and
#: (via run.sh) `down -v` a database — from being aimed at a live instance. The isolation guarantee
#: lived in the README and in whatever the operator had been told, i.e. in the two places that do
#: not execute. The path is now the one file `stack-up.sh` writes, and `assert_disposable_stack`
#: proves the target really is that stack before anything is touched.
STACK_FILE = SIM_DIR / ".stack.json"

#: ==The compose files C5 may act on — DERIVED, for exactly the reason `SINK_WEBHOOK_URL` is.==
#:
#: `--compose-cmd` used to accept an arbitrary invocation and hand it straight to `stop`/`start`.
#: So the stack this harness PROVES it is talking to and the stack that command can reach into were
#: two different objects: `assert_disposable_stack` verified a nonce planted in one database, and
#: then C5 stopped whatever container some other string named. The whole isolation guarantee has a
#: hole in it exactly the width of one CLI flag — and the flag was the only input in this harness
#: with no validation at all, in the one code path that manipulates containers.
#:
#: A value the harness can compute has no business being configurable: what is never accepted needs
#: no validation. These are the same three overlays `scripts/run.sh` builds and the same three the
#: report names, in the same order, and they are the whole reason `down -v` cannot reach a real
#: instance (`compose.sim.yml` renames the project). ==Deriving them also closes the hazard both
#: shell scripts carry a warning about== — "any future edit that drops that `-f` turns a safe
#: script into a destructive one" — because a list that lives in code cannot be shortened by an
#: invocation, and :func:`assert_compose_targets_stack` catches it if the FILE is edited instead.
COMPOSE_FILES = (
    REPO_ROOT / "deploy" / "docker-compose.yml",
    REPO_ROOT / "e2e" / "compose.e2e.yml",
    SIM_DIR / "compose.sim.yml",
)


def positive_int(raw: str) -> int:
    """An argparse type that refuses zero and negatives. ==A run's shape cannot be non-positive.==

    ``--workers 0`` builds a ``ThreadPoolExecutor(max_workers=0)`` and ``--contenders 0`` builds a
    ``threading.Barrier(0)``; ``--contenders 1`` is worse than either, because it "succeeds" while
    testing nothing — a one-way race has exactly one winner whatever the product does, and C2, the
    control that exists to catch a harness which is not really concurrent, would be firing a single
    request at a single slot and confirming it.

    Rejected at the CLI, where the message can say what was wrong, instead of surfacing as a
    traceback or, worse, as a green run. ``argparse`` turns the raised error into exit code 2.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"must be 1 or more, got {value}. A simulation with no guests, no contenders or no "
            "planned bookings measures nothing, and a run that measures nothing must not be able "
            "to report a verdict."
        )
    return value


def contender_count(raw: str) -> int:
    """==A race needs at least TWO contenders, and this is the type that says so.==

    The previous commit named this defect in its own message — *"`--contenders 1` is the one worth
    naming, because it does NOT crash"* — and then shipped a validator that accepted 1. Describing
    a hazard is not guarding it; that is the exact shape this whole directory exists to hunt, and
    it landed in the fix for it.

    One contender is the dangerous value precisely because nothing breaks. ``Barrier(1)`` releases
    immediately, the same-slot race reports one winner of one, and C10 passes — while C2, whose
    entire job is to prove the harness CAN see more than one winner, would be firing a single
    request at a single slot and confirming it did. The run would read `MEASURED` having tested no
    concurrency at all.
    """
    value = positive_int(raw)
    if value < 2:
        raise argparse.ArgumentTypeError(
            f"a race needs at least 2 contenders, got {value}. With one, `Barrier(1)` releases "
            "immediately and every race reports 'exactly one winner' whatever the product does — "
            "including C2, the control that exists to prove the opposite is observable."
        )
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aethercal_sim", description="Two-week load simulation.")
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument(
        "--workers", type=positive_int, default=8, help="simultaneous simulated guests"
    )
    parser.add_argument(
        "--contenders", type=contender_count, default=40, help="threads per adversarial race"
    )
    parser.add_argument(
        "--per-week", type=positive_int, default=40, help="bookings per business per week"
    )
    parser.add_argument("--allow-missing-drain-control", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("simulation-report.md"))
    parser.add_argument("--json-out", type=Path, default=Path("simulation-report.json"))
    args = parser.parse_args(argv)

    # ==The two outputs are written one after the other, so the same path means the JSON silently
    # eats the report.== Both defaults differ, so this only bites an invocation that passes them —
    # and it bites at the END of a run, after the load has been generated and the controls judged,
    # destroying the one artifact a person reads and leaving a file whose name says `.md` holding
    # JSON. Nothing downstream can tell that from a run that only ever wrote JSON.
    #
    # Compared RESOLVED, because `simulation-report.md` and `./simulation-report.md` and
    # `reports/../simulation-report.md` are one file wearing three names, and a check on the raw
    # strings would wave all but the first through.
    if args.out.resolve() == args.json_out.resolve():
        parser.error(
            f"--out and --json-out resolve to the SAME file ({args.out.resolve()}). "
            "The Markdown report is written first and the JSON would overwrite it, so the run "
            "would end holding one artifact where it reports two. Give them different paths."
        )
    return args


def _compose(metrics_token: str, *args: str) -> str:
    """Run a compose subcommand on the simulation project, and return its stdout.

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
        [*compose_command(), *args],
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
    return result.stdout


def compose_command() -> list[str]:
    """The one compose invocation this harness may make, built from :data:`COMPOSE_FILES`."""
    argv = ["docker", "compose"]
    for path in COMPOSE_FILES:
        argv += ["-f", str(path)]
    return argv


def assert_compose_targets_stack(stack: StackConfig) -> str:
    """==Prove the derived compose invocation resolves to the project that was VERIFIED.==

    :func:`~aethercal_sim.world.assert_disposable_stack` establishes that the target database
    carries this run's nonce and that its project is ``aethercal-sim``. That says nothing about
    what ``docker compose … stop worker`` would reach — a different question, answered by a
    different set of files. Deriving the files (rather than accepting a command) makes the two
    agree by construction; this reads the answer back out of compose itself and refuses if it does
    not, so a hand-edited ``compose.sim.yml`` whose ``name:`` no longer renames the project cannot
    turn C5 into `stop` against a real instance's worker.

    ==Fail-closed, and BEFORE anything is created or purged.== It is called next to the stack
    assertion, not next to C5: a refusal that fires an hour into a run has already generated the
    load it was supposed to prevent. Anything that stops the project name being READ — no docker,
    a missing overlay, a compose that will not parse — is a refusal too, because "could not check"
    and "checked and it matched" must never be the same outcome.
    """
    try:
        raw = _compose(stack.metrics_token, "config", "--format", "json")
    except Exception as exc:
        raise NotADisposableStackError(
            f"could not resolve the simulation compose project ({type(exc).__name__}: {exc}).\n"
            "C5 stops and starts a container, so the invocation that does it has to be shown to "
            "point at the verified stack. It could not be, so nothing will be stopped."
        ) from exc
    try:
        config: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotADisposableStackError(
            f"`docker compose config` did not return JSON ({exc}); the project name this harness "
            "would act on cannot be established."
        ) from exc
    name = config.get("name") if isinstance(config, dict) else None
    if name != stack.compose_project:
        raise NotADisposableStackError(
            f"the derived compose files resolve to project {name!r}, not the verified "
            f"{stack.compose_project!r}.\n"
            "==That means `stop`/`start` would reach containers this run never proved anything "
            "about.== The most likely cause is an edit to simulation/compose.sim.yml that dropped "
            "or changed its `name:` — the one line that keeps `down -v` away from a real instance."
        )
    return str(name)


def _host_description() -> str:
    return (
        f"{platform.node()} · {platform.system()} {platform.release()} · "
        f"python {platform.python_version()}"
    )


#: How long the confirmation reconciliation keeps re-reading the mailbox before giving up.
#:
#: ==A deadline, not a sleep.== C12 has already established that the outbox drained, so every
#: confirmation has been HANDED to SMTP by the time this starts; this window only covers Mailpit
#: finishing its own write. It ends the moment the set is complete, and a timeout is reported as a
#: timeout rather than as a smaller sample.
CONFIRMATION_MATCH_TIMEOUT_SECONDS = 30.0
CONFIRMATION_MATCH_POLL_SECONDS = 3.0

#: Consecutive reads that must show the SAME set of messages before the mailbox is called settled.
#: Any new message resets the count — a late duplicate is only observable if the window restarts.
_QUIET_POLLS = 2


def measure_confirmations(  # noqa: PLR0912, PLR0915 - the quiet window IS the extra branches
    mailbox: Mailbox,
    booked: list[BookedRef],
    *,
    timeout_seconds: float = CONFIRMATION_MATCH_TIMEOUT_SECONDS,
    poll_seconds: float = CONFIRMATION_MATCH_POLL_SECONDS,
) -> tuple[Latency, MailboxRead, ConfirmationCoverage]:
    """Match every created booking to its confirmation, and account for the ones that do not match.

    .. rubric:: The two silent subtractions this replaces

    The previous version read the mailbox once with a hardcoded ``limit=20000`` and then filtered
    the deltas with a bare ``if delta_ms >= 0``. Both losses were invisible to the verdict, and
    ==both moved the published latency in the same, flattering direction==: a truncated mailbox
    drops samples, and a non-negative filter drops specifically the FASTEST ones — the only
    confirmations that can precede a reference instant stamped after the POST returned.

    Now the mailbox is PAGED to its own reported total (:meth:`Mailbox.read_all`), the reference is
    the instant the POST was SENT (:class:`BookedRef`), and what remains is reconciled: how many of
    the created bookings were found, and how many deltas came back negative anyway. Both feed C14,
    so an incomplete sample cannot be published as a complete one.

    Negative deltas are still kept OUT of the distribution — a negative latency is not a latency —
    but they are counted, named in the report, and they void the run. The difference between that
    and the old behaviour is the whole point: ==the filter is no longer free.==
    """
    started = time.monotonic()
    attempts = 0
    read = _read_and_hydrate(mailbox)
    confirmations: dict[str, list[MailMessage]] = {}
    superseded_by: dict[str, int] = {}
    matched = 0
    last_identities: frozenset[str] = frozenset()
    quiet_polls = 0
    while True:
        attempts += 1
        confirmations = confirmations_by_recipient(read.messages)
        superseded_by = superseding_notices_by_recipient(read.messages)
        matched = sum(
            1 for ref in booked if len(confirmations.get(ref.guest_email.lower(), [])) == 1
        )
        accounted = sum(
            1
            for ref in booked
            if confirmations.get(ref.guest_email.lower())
            or superseded_by.get(ref.guest_email.lower())
        )
        # ==Reaching the target is not the end of the observation, it is the START of the quiet
        # window.== Finishing at the first poll that satisfies `accounted` stops exactly when the
        # asynchronous case the polling exists to cover is still in flight: Mailpit may not have
        # persisted everything SMTP already delivered, and a LATE duplicate — half the reason C14
        # exists — arrives by definition after the first one. So the loop continues until the set of
        # message ids has stopped changing for `_QUIET_POLLS` consecutive reads, and ==any new
        # message RESETS that window==, which is what makes a late arrival observable at all.
        identities = frozenset(message.message_id for message in read.messages)
        if identities == last_identities:
            quiet_polls += 1
        else:
            quiet_polls = 0
            last_identities = identities
        if not read.complete:
            break
        if accounted >= len(booked) and quiet_polls >= _QUIET_POLLS:
            break
        if time.monotonic() - started >= timeout_seconds:
            break
        time.sleep(poll_seconds)
        read = _read_and_hydrate(mailbox)

    latency = Latency("booking_to_confirmation_email")
    negatives = 0
    worst_negative = 0.0
    duplicates = 0
    superseded = 0
    unaccounted = 0
    seen_uids: dict[str, str] = {}
    collided_uids = 0
    for ref in booked:
        address = ref.guest_email.lower()
        found = confirmations.get(address, [])
        if len(found) > 1:
            duplicates += 1
            continue
        if not found:
            # ==No confirmation is not automatically a loss, and a live run PROVED it.== The product
            # retires a still-queued confirmation when the booking is cancelled or rescheduled
            # before the outbox sends it — the row goes to `voided`, "a booking transition retired
            # it before it ran". A first version of this control demanded a confirmation for every
            # booking and turned a clean run VOID over 25 of them, against 40 voided outbox rows.
            # That is the defect this branch keeps removing: a control that fails while the product
            # is behaving correctly.
            #
            # So the discriminator is whether the guest was told ANYTHING. Told nothing at all is
            # still a lost message, and still gates.
            if superseded_by.get(address):
                superseded += 1
            else:
                unaccounted += 1
            continue
        message = found[0]
        # ==One confirmation must belong to ONE booking.== The recipient is unique per planned
        # booking, so a uid appearing twice would mean two guests were matched to one announcement.
        uid = message.invite.uid if message.invite is not None else ""
        if uid and uid in seen_uids:
            collided_uids += 1
            continue
        seen_uids[uid] = ref.booking_id
        delta_ms = (message.created.timestamp() - ref.sent_at_wall) * 1000.0
        if delta_ms < 0:
            negatives += 1
            worst_negative = min(worst_negative, delta_ms)
            continue
        latency.record(delta_ms)
    return (
        latency,
        read,
        ConfirmationCoverage(
            created=len(booked),
            matched=matched,
            negative_deltas=negatives,
            worst_negative_ms=worst_negative,
            read_complete=read.complete,
            read_problem=read.problem,
            reported_total=read.reported_total,
            page_size=read.page_size,
            attempts=attempts,
            waited_seconds=time.monotonic() - started,
            duplicate_confirmations=duplicates,
            colliding_uids=collided_uids,
            superseded=superseded,
            unaccounted=unaccounted,
            messages_with_invite=sum(1 for m in read.messages if m.invite is not None),
        ),
    )


def _read_and_hydrate(mailbox: Mailbox) -> MailboxRead:
    """Page the mailbox and attach each message's calendar identity, as ONE read.

    A hydration failure degrades the whole read rather than leaving some messages un-hydrated: an
    un-hydrated message is indistinguishable from one that legitimately carries no calendar part,
    and that is the reassuring reading of a failure.
    """
    read = mailbox.read_all()
    if not read.complete:
        return read
    hydrated, problem = mailbox.hydrate_invites(read.messages)
    if problem:
        return MailboxRead(
            hydrated,
            False,
            read.reported_total,
            read.pages,
            read.page_size,
            read.unparseable,
            problem,
        )
    return MailboxRead(
        hydrated, True, read.reported_total, read.pages, read.page_size, read.unparseable
    )


def superseding_notices_by_recipient(messages: list[MailMessage]) -> dict[str, int]:
    """Announcements that SUPERSEDE a confirmation, by recipient. ==Pure, so it is testable.==

    A cancellation (``CANCEL``/``CANCELLED``) or a reschedule (``REQUEST`` at a bumped sequence).
    Their presence is what makes "this booking has no confirmation" an accounted outcome rather
    than a lost message — see :func:`measure_confirmations`.
    """
    grouped: dict[str, int] = {}
    for message in messages:
        invite = message.invite
        if invite is None or invite.is_confirmation:
            continue
        if invite.method == "CANCEL" or invite.sequence > 0:
            grouped[message.to] = grouped.get(message.to, 0) + 1
    return grouped


def confirmations_by_recipient(messages: list[MailMessage]) -> dict[str, list[MailMessage]]:
    """Group the CONFIRMATIONS — and only those — by recipient. ==Pure, so it is testable.==

    This replaces "the earliest message to that address". That rule worked by accident: a guest's
    confirmation does normally arrive before their cancellation or reschedule notice, so taking the
    earliest usually picked the right one. ==But it is a property of the ORDER OF EVENTS, not a
    check==, and it says nothing at all in the case that matters — a booking whose confirmation was
    never sent and whose cancellation was. There the earliest (and only) message is the
    cancellation, the booking counts as confirmed, C14 reports a complete sample, and §2 publishes
    the delay of a message sent minutes later for another reason. ==A delivery failure would be
    published as a high latency.==

    A confirmation is now identified by its iTIP identity: ``METHOD:REQUEST`` + ``STATUS:CONFIRMED``
    + ``SEQUENCE:0`` in the message's own ``.ics``. Locale-independent, tenant-independent, and not
    a substring of anything.
    """
    grouped: dict[str, list[MailMessage]] = {}
    for message in messages:
        if message.invite is not None and message.invite.is_confirmation:
            grouped.setdefault(message.to, []).append(message)
    return grouped


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915, PLR0912 - a run IS its phases
    args = parse_args(argv)

    run_id = uuid.uuid4().hex[:8]
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    stack = load_stack(STACK_FILE)

    # ==Nothing below this line may run against anything but this run's throwaway stack.==
    # The very next statements purge a mailbox and a webhook sink, and provisioning follows. This
    # check is what makes the isolation a property of the code rather than of the instructions the
    # operator happened to receive: it verifies the compose project, that every endpoint is
    # loopback, and that the database carries the 128-bit marker stack-up.sh planted seconds ago.
    # It raises before any write, so a refusal leaves the target untouched.
    nonce = assert_disposable_stack(stack)
    print(f"==> target verified as the throwaway stack (marker {nonce[:8]}...)")

    # ==And the containers C5 will stop are part of the SAME verified thing.== Checked here, next
    # to its sibling and before the first write, rather than at C5 an hour later.
    if not args.allow_missing_drain_control:
        project = assert_compose_targets_stack(stack)
        print(f"==> compose invocation verified as project `{project}` (derived, not accepted)")

    mailbox = Mailbox(stack.mailpit_url)
    mailbox.purge()
    Client(stack.sink_url).request("DELETE", "/_captured")

    print(f"==> run {run_id}: provisioning {len(stack.businesses)} businesses")
    world = provision(stack, run_id=run_id)

    sampler = OutboxSampler(stack.worker_url, stack.metrics_token, interval_seconds=0.5)
    sampler.start()
    # ==The sampler is a THREAD, so its shutdown cannot depend on the happy path.== Every
    # `return` and every raise between here and the verdict used to leave it running: the two
    # explicit `stop()` calls covered the paths somebody remembered. A background thread that
    # outlives the run keeps scraping a stack `run.sh` is already tearing down, and its failures
    # land in a report nobody reads. One `finally` owns the lifecycle now.
    try:
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

        # A far-future week nothing else in the run has touched, so each race starts from clean
        # slots.
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
        print(
            f"    distinct slots (control): {distinct.winners} winner(s) of {distinct.contenders}"
        )

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
                Control.not_run(
                    "C7", _C7, "exactly 1 booking.cancelled", "its subject never booked"
                )
            )
        else:
            races.append(
                race_cancel(stack, target, booking_id=cancel_id, contenders=args.contenders)
            )
            # ==Observed, not slept through.== This used to be `time.sleep(12)` and a single read: a
            # webhook arriving at 13 seconds made C7 fail in a way INDISTINGUISHABLE from the
            # duplication it exists to detect. `observe_cancel_webhooks` drains the outbox first (so
            # every queued delivery has been attempted), polls until the event appears or an
            # explicit
            # deadline passes, then keeps watching long enough for a late duplicate to be caught.
            cancel_observation = observe_cancel_webhooks(stack.sink_url, cancel_id, sampler=sampler)
            cancel_race_events = cancel_observation.counts
            print(
                f"    cancel race: drained={cancel_observation.drained} events={cancel_race_events}"
            )
            controls.append(judge_cancel_idempotency(cancel_observation, ident="C7", guards=_C7))

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
        # ==The race must be the size that was ASKED FOR, or say so.== `later_starts` is sliced to
        # `--contenders`, and the guard used to accept anything from 2 upwards: a thin offer
        # silently
        # produced a 5-way reschedule race in a run whose §4 header says 40 contenders. The
        # RaceOutcome
        # would have recorded the true number, so nothing was a lie — but a run that quietly
        # delivers a
        # fraction of the configured load is a run whose contention claim nobody chose. C15's peak
        # is
        # measured against the contenders that actually fired, so it could not catch this either.
        if reschedule_id is None or len(later_starts) < args.contenders:
            why = (
                "its subject never booked"
                if reschedule_id is None
                else (
                    f"only {len(later_starts)} target slots on offer, need {args.contenders} — the "
                    "reschedule race would have run at a fraction of the configured contention"
                )
            )
            controls.append(Control.not_run("C8", _C8, "exactly 1 successor survives", why))
        else:
            reschedule_race = race_reschedule(
                stack, target, booking_id=reschedule_id, starts=later_starts
            )
            races.append(reschedule_race)
            # ==What the race LEFT BEHIND, which is the invariant that matters.== One HTTP winner is
            # not the contract; one live appointment is — but the diary alone cannot tell "one
            # successor
            # survived" from "nothing happened and the original is still there", so the race and the
            # subject's own id go in as the discriminators.
            controls.append(
                control_lineage_after_race(
                    stack,
                    target,
                    ident="C8",
                    guards=_C8,
                    guest_email=f"reschedule.subject.{run_id}@guests.sim.test",
                    date_from=race_day,
                    date_to=race_day + timedelta(days=28),
                    race=reschedule_race,
                    original_id=reschedule_id,
                    allowed_refusals=RESCHEDULE_RACE_REFUSALS,
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
            mixed_race = race_cancel_vs_reschedule(
                stack, target, booking_id=mixed_id, start=spare_starts[0]
            )
            races.append(mixed_race)
            # ==Both calls answering 200 here is CORRECT, so the winner count is not the oracle.==
            # The
            # reschedule swaps in a successor; the cancel then finds the predecessor already
            # cancelled
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
                    race=mixed_race,
                    original_id=mixed_id,
                    allowed_refusals=MIXED_RACE_REFUSALS,
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
        # back as the outcome — so a `409 not_ended`, which is the guard correctly refusing, would
        # have
        # been recorded as the result of a POSITIVE test. Picking by end time removes the need for a
        # cap: either a slot fits the budget and is waited out in full, or none does and C11 says
        # NOT
        # RUN.
        _C6 = "the no-show guard: an appointment that has not ended cannot be a no-show"
        _C11 = "the no-show transition itself, against a real end time"
        no_show_outcome = "not attempted"
        micro = target.event_types["micro"]
        # ==The window opens YESTERDAY, and that is not padding — it is a timezone bug this control
        # caught.== The slots window is a range of DATES resolved against the event type's schedule
        # timezone, while `date.today()` here is the harness's UTC date. For a business in
        # America/New_York every UTC instant between 00:00 and 04:00 belongs to the PREVIOUS local
        # day,
        # so "today (UTC) onwards" returned a first slot two hours out and the leg correctly
        # reported
        # that nothing ended within budget. One day back and three wide covers every offset on earth
        # (±14 h), so "now" is always inside the window.
        #
        # ==The lax version hid this entirely:== it took `starts[0]`, truncated its wait with
        # `min(...)`,
        # marked the no-show two hours early, and would have filed the resulting `409 not_ended` —
        # the
        # guard correctly refusing — as the observed outcome of the POSITIVE test.
        micro_start = pick_micro_slot(
            slot_starts(
                fetch_slots(
                    client,
                    event_type_id=micro.id,
                    day=date.today() - timedelta(days=1),
                    timezone="UTC",
                    days=3,
                )
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
            final_status = (
                str(after.body.get("status")) if isinstance(after.body, dict) else "unknown"
            )
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
        if not args.allow_missing_drain_control:
            print("==> control C5: stopping the worker to prove the backlog metric is live")

            def make_work() -> int:
                """Strand real work behind the stopped worker, and report HOW MUCH really landed.

                ==The count is returned rather than assumed.== C5's pass conditions are
                stated against the work that was actually created, so a probe that booked four
                of its six slots is judged against four. A constant written in two places is
                one that eventually disagrees with itself, and here the disagreement would show
                as the metric "missing" work that was never made.
                """
                offer_now = fetch_slots(
                    client,
                    event_type_id=target.event_types["standard"].id,
                    day=window_start + timedelta(days=28),
                    timezone=target.config.timezone,
                    days=3,
                )
                created = 0
                for index, start in enumerate(slot_starts(offer_now)[:6]):
                    probe = book_slot(
                        client,
                        event_type_id=target.event_types["standard"].id,
                        start=start,
                        guest_name=f"Deadman Probe {index}",
                        guest_email=f"deadman.{run_id}.{index}@guests.sim.test",
                        guest_timezone="UTC",
                        locale="en",
                    )
                    created += 1 if probe.ok else 0
                return created

            def stop_worker() -> None:
                _compose(stack.metrics_token, "stop", "worker")

            def start_worker() -> None:
                _compose(stack.metrics_token, "start", "worker")

            control, drain_stats = control_drain_deadman(
                sampler,
                stop_worker=stop_worker,
                start_worker=start_worker,
                make_work=make_work,
            )
            controls.append(control)
        else:
            controls.append(
                Control.not_run(
                    "C5",
                    "the backlog metric is LIVE (a dead drain is visible, and recovers)",
                    "due climbs while the worker is stopped, then returns to 0",
                    "--allow-missing-drain-control was passed, so the worker was never stopped",
                )
            )

        # ---- Drain, then measure booking → confirmation ------------------------------------------
        print("==> waiting for the outbox to drain")
        drained, drain_wait = wait_for_drain(sampler, timeout_seconds=300.0)
        # ==C12 — a failed drain now INVALIDATES the run.== `drained` was computed, printed in §3
        # and
        # then ignored: a run whose queue never emptied still stamped MEASURED, while the
        # booking→confirmation figures silently described only the messages that escaped. Scrape
        # failures gate too, because a backlog series with holes in it understates its own peak.
        # (C5's
        # deliberate outage pauses the sampler, so those never land in this count.)
        controls.append(
            control_outbox_drained(
                drained=drained,
                waited_seconds=drain_wait,
                scrape_failures=len(sampler.failures),
            )
        )

        # ---- C13: the organic phase has to add up before its numbers mean anything --------------
        controls.append(judge_organic_accounting(organic, planned=summary["total"]))
        # ---- C15: and §4's bursts have to have really overlapped, or its winner counts mean
        # nothing
        controls.append(judge_race_concurrency(races))

        drain_latency, mail_read, coverage = measure_confirmations(mailbox, organic.booked)
        controls.append(judge_confirmation_coverage(coverage))

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
                mail_read=mail_read,
                coverage=coverage,
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
                    # ==The whole taxonomy, not just the flattering members of it.== A diff
                    # between two
                    # runs must be able to show a category appearing, which is why the
                    # create/follow-up
                    # outcomes travel in the machine-readable twin rather than only in the prose.
                    "organic_outcomes": {
                        "planned": summary["total"],
                        "create": organic.create_outcomes(),
                        "follow_up": organic.follow_up_outcomes(),
                        "follow_ups_attempted": organic.follow_ups_attempted,
                        "unexpected_failures": organic.unexpected_organic_failures(),
                        "slots_read_failures": organic.slots_read_failed[:10],
                        "booking_unreadable": organic.booking_unreadable[:10],
                    },
                    "confirmations": {
                        "created": coverage.created,
                        "matched": coverage.matched,
                        "superseded": coverage.superseded,
                        "unaccounted": coverage.unaccounted,
                        "duplicate_confirmations": coverage.duplicate_confirmations,
                        "colliding_uids": coverage.colliding_uids,
                        "negative_deltas": coverage.negative_deltas,
                        "worst_negative_ms": coverage.worst_negative_ms,
                        "mailbox_read_complete": coverage.read_complete,
                        "mailbox_read_problem": coverage.read_problem,
                        "mailbox_reported_total": coverage.reported_total,
                        "mailbox_page_size": coverage.page_size,
                        "mailbox_readable_messages": len(mail_read.messages),
                        "mailbox_unparseable": mail_read.unparseable,
                        "mailbox_pages": mail_read.pages,
                        "reads": coverage.attempts,
                        "waited_seconds": coverage.waited_seconds,
                    },
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
                            "peak_overlap": race.peak_overlap,
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
                        "discarded_at_pause": sampler.discarded_at_pause,
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
    finally:
        sampler.stop()


if __name__ == "__main__":
    raise SystemExit(main())
