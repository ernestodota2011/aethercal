# Committed runs

One file per run worth keeping, named `YYYY-MM-DD-run-<id>.md` with its `.json` twin. The default
output paths at `simulation/` are gitignored precisely so that committing a report is a **decision**
rather than a side effect of having run one.

Reports accumulate here so a p95 can be compared with the last release instead of admired on its
own. The JSON exists for exactly that: the same numbers in a shape you can diff or plot. The plan is
seeded, so two runs at the same seed carry the same load and any movement is the product's, not the
harness's.

==Read §5 (controls) before §2 (latency).== A run whose verdict is not `MEASURED` proves nothing at
all, however good its numbers look — which is why the verdict is the first line of every file.

| Run | Date | Verdict | Notes |
|---|---|---|---|
| `d94af6c1` | 2026-07-29 | MEASURED | **240** planned / 210 created, 43 follow-ups, 40-way races, **all fifteen** controls held, and the id set is the required set exactly. The plan is **byte-identical** to `1b3a66f9`'s at the same seed, so none of the eight defects closed since it moved a planned value. §6's 545 classified reconciles with §1; 32 `slot_unavailable` split across both legs — **30** on the create leg and **2** on the reschedule leg — and `240 − 30 = 210` closes the create leg on its own. Confirmations 185 matched + 25 superseded = 210, 0 unaccounted, 0 duplicates, 0 uid collisions, 0 unparseable envelopes. C5 on a drained baseline (due=0), 6 stranded, 6 delivered. Peak 40 of 40 in flight (C15). 505 samples, 0 scrape failures. ==The first run whose C5 stopped a container the isolation check had actually verified, and the first booted by a script that could not proceed over a failed `down -v`== — see the withdrawal below. Same observability limit as every predecessor: a **dead** worker is invisible to the backlog metric (§7). |

> [!warning] ==Thirteen earlier runs were published here as `MEASURED` and have been withdrawn.==
> Their numbers were plausible; what was wrong was the **certificate**, thirteen times over.
>
> `1b3a66f9` — withdrawn because the gate that had cleared this branch was **reading half of it**.
> The diff is ~466 000 characters against a 200 000 cap, silently truncated, so more than half had
> never been reviewed by any of the passes that came back clean. Re-run one file at a time it
> returned **eight** findings, and three of them are instruments this run's numbers were taken
> with: the mailbox reader used its **cursor** as its oracle, so a server answering the same page
> twice would have been read as whole with the arithmetic agreeing; the slots query interpolated
> the timezone by hand, which survives for `America/*` and breaks any fixed-offset zone; and C5
> **stopped a container nothing had verified** — `--compose-cmd` took an arbitrary invocation and
> handed it straight to `stop worker`, so the stack the run *proves* it is talking to and the stack
> that command could reach were two different objects. ==That last one is why this is a withdrawal
> and not a supersession: the isolation guarantee this directory exists to enforce had a hole in it
> exactly the width of one CLI flag, in the only code path that manipulates containers.== Its boot
> also ran `docker compose down -v || true`, so nothing in it can claim the empty database every
> number assumes — true here by luck (the container was fresh), unprovable in general.
>
> ==Two of the three did NOT fire in it, and saying so matters more than the withdrawal.== Its
> mailbox held 279 messages against a page size of 500, so it paged **once** and no repeat was
> possible; and its businesses are all `America/*`, so the unencoded `+` never appeared. Live traps
> that happened not to spring, and nothing in the run could have told anyone that — which is the
> whole reason they are guarded now rather than re-derived per run.
>
> `bd6232d4` — withdrawn with **no wrong number in it**, and the second one withdrawn on that
> footing. Every total reconciled, its plan is byte-identical to the replacement's at the same seed,
> and ten defects closed after it left its arithmetic untouched. What it cannot claim is the one
> thing every report here implies: that the throwaway stack it describes was thrown away.
> ==`docker compose down -v` had never once succeeded from either shell script.== `compose.sim.yml`
> requires `AETHERCAL_SIM_METRICS_TOKEN` with `:?`, compose interpolates the whole file on *every*
> subcommand, and both teardowns ran it out of a shell that never had the variable — so `down`
> failed on an exigency written for `up`, and the stack stayed up. `bd6232d4` reported exit 0 while
> doing it, because the trap printed the failure and propagated nothing.
>
> ==It was found by the fix that made a failed teardown visible, on that fix's first live run==,
> which is the argument for that fix made by the thing it found. Two of `bd6232d4`'s instruments are
> also gone: its confirmation reconciliation stopped at the **first** complete count, so a late
> arrival — or a late duplicate, half of what C14 is for — was unobservable by construction. The
> replacement reads to a quiet window instead, and can report that the window never had to restart.
> `bd6232d4` could not have reported either way.
>
> `e4a99ccc` — withdrawn after a gate run **by slices** (one file per review) found seven defects
> that no full-diff run had seen, three of them high and one a security gap. The two that touch its
> numbers: ==its "239 planned" was never the 240 that was asked for== — per-day volume was rounded
> independently, which is unbiased per day and says nothing about the sum, and §1 quotes that number
> while C13 reconciles against it; and C5 did not require a **drained baseline**, so "the restarted
> worker delivered ≥ 6" was equally true of six intents that had been queued before it began. The
> rest could not fire in it but could have: a scrape starting after `pause()` returned, a sampler
> left running by any early return, and a webhook destination read from the stack file and handed
> to the product without ever being checked.
>
> `dc008532` — withdrawn because three of the instruments certifying it could still pass over a
> loss. The mailbox read called itself COMPLETE while silently failing to parse envelopes (the total
> added up and the list was short); `stop()` could return while the sampler was still writing, so
> §3 and C12 were assembled over a changing state; and C4 judged its third probe with
> `"day_full" in ...`, where the broken-read outcome embeds the response BODY — so a failed query
> whose body merely mentioned `day_full` satisfied the cap. ==None of the three fired in that run;
> all three could have, and nothing in it would have said so.==
>
> `925780c6` — withdrawn because §2 was measuring the wrong messages. A booking was "matched" to
> *the earliest mail to its guest*, and every guest also receives their cancellation or reschedule
> notice — so the rule worked only because confirmations normally arrive first, ==a property of the
> order of events rather than a check.== For a booking whose confirmation was retired and whose
> cancellation was sent, the earliest and only message is the cancellation: it counted as confirmed
> and §2 absorbed a timestamp minutes late, for another reason. Its published
> `booking_to_confirmation_email` p50 of 6175.8 ms carried those; the replacement, over confirmations
> only, is **4583.7 ms**.
>
> `2cbac654` — withdrawn because C8 and C9 could pass over a system that never moved. If every
> request in the mutation race had failed, the ORIGINAL booking would still be sitting there, and a
> diary holding exactly one live appointment is precisely what *"one successor survives"* looks
> like; C9's *"at most one"* is even easier to satisfy with nothing at all. ==Both measured a final
> state compatible with two different histories and only one of them is the history they claim to
> test== — the same defect as C2 being read as proof of simultaneity, one control over. They now
> require the race to have produced a winner and the survivor **not to be the subject**.
>
> `ef379d88` — withdrawn because §4's central premise was never measured. Its winner counts are
> exactly what a correct 40-way race produces **and exactly what a harness that had serialised its
> threads would produce too**: N different slots booked one after another still yields N winners,
> and one slot booked N times in sequence still leaves exactly one. ==C2 and C10 were satisfiable by
> a run in which nothing overlapped==, and the `threading.Barrier` meant to prevent that was a
> mechanism nobody observed. C15 measures it now, and the replacement run records a peak of 40
> requests in flight at once — which is the first time this directory can say that rather than
> assume it.
>
> `ed6db4f8` — the only one withdrawn without a defect in it. Every number it published was correct
> and reconciled, and its own JSON shows the two later fixes never bit in it: **0 scrape failures**,
> so `pause()`'s race never recorded a boundary failure, and full durable readings, so the metrics
> reader never met a malformed payload. It is withdrawn because the reader and the sampler changed
> underneath it and §3 grew a line it does not carry. ==A certificate should be produced by the code
> it certifies==, and this directory has withdrawn five reports for weaker reasons than that.
>
> `19228cd7` — withdrawn for a hole in the numbers rather than in a control. ==§6 calls itself
> "every outcome, successes included" and was short by one response per reschedule attempted==: the
> follow-up's slots read was the single HTTP call in the organic phase wired to neither instrument,
> so §2's `slots_read` counted only the create leg. Its own arithmetic shows it — 519 classified =
> 239 slots + 239 bookings + 24 cancels + 17 reschedules, with 41 reads missing. A taxonomy with a
> hole in it cannot be reconciled against anything, which is the one job C13 exists to do.
>
> `e36c28c3` — the first run under C13 and C14, and it is withdrawn for the reason that keeps
> recurring one layer further in: ==two of the controls certifying it still trusted reads they had
> not checked.== C4's third probe passes when the day has left the offer, so an empty offer is the
> answer it *hopes for* — and it tested only `response.ok`, so a 2xx whose body was not the slots
> contract read as the cap biting. C7 skipped any sink delivery whose base64 or JSON failed to
> decode, so a duplicate `booking.cancelled` with an unparseable body was invisible to the count it
> then called "exactly one". Neither fired in that run; both could have, and nothing would have
> said so.
>
> `b72197a2` — the first run whose isolation was *derived*, and its controls were sound as far as
> they went. What they did not go to was the two phases that produce §1 and §2. The organic worker
> could lose its own failures (a slots query that FAILED counted as a fully-booked day; any non-2xx
> booking that was not a collision dropped through `if not response.ok: return`), and the
> confirmation sample was taken through a hardcoded `limit=20000` and a silent `if delta_ms >= 0`.
> ==The loss is not hypothetical in this run: its §6 reports **35** `slot_unavailable` responses
> and its §1 reports **32** collisions.== The missing three were reschedule refusals owned by no
> category and reconciled against nothing — visible only by subtracting one section of the report
> from another, which is exactly the arithmetic no reader performs.
>
> ==Two of the traps did NOT fire in it, and saying so matters more than the withdrawal.== Its
> drain-latency sample is 207 for 207 bookings, so the `delta_ms >= 0` filter discarded nothing;
> and it held 273 messages against a ceiling of 20 000. ==Both were live traps that happened not to
> spring, and nothing in the run could have told anyone that.== That is the whole reason they are
> controls now (C13, C14) rather than conditions somebody re-derives per run.
>
> `696a49f6` — the verdict was not bound to the assertions the report presented as proven: the
> double-booking race took no part in it (five winners on one slot would still have read green), a
> control whose prerequisite failed vanished instead of failing, a failed drain invalidated nothing,
> and the latency clock stopped before the response body was read. Re-judged under the current rules
> it returns **VOID**, missing `C10`/`C11`/`C12`.
>
> `dc6cfd8a` — the verdict logic was sound, but two controls still passed **on a broken source**: C3
> read "0 slots" without checking the query had succeeded (a closed Saturday and a dead API are
> indistinguishable that way), and C9's "at most one live appointment" was trivially satisfied by an
> unreadable diary. Its latency also predates the fix that measures reading the body. And it reached
> a throwaway container because the operator aimed it there — not because the code refused anything
> else.
>
> Each was replaced rather than annotated: a withdrawn measurement left in the table is one
> somebody eventually quotes.
