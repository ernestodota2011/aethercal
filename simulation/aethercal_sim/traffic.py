"""The shape of two weeks of demand — a pure, seeded function with no I/O.

Everything here is deterministic given ``seed``: same seed, same plan, on any machine. That is what
makes one run comparable to the one before it. A harness whose load differed every time could report
a p95 but never say whether it had moved.

.. rubric:: What this deliberately is NOT

It is a plausible **shape**, not a claim about real demand. The weekday weights below say
Mondays are busier than Fridays because that is true of most appointment businesses; they are
not fitted to the pilot's traffic, because five bookings cannot fit anything. The value of the
shape is that it spreads load across days, businesses, locales and event types instead of
hammering one row - not that it predicts anybody's calendar. The report says so in as many words.

.. rubric:: Weekends

There are none. The organic event types sit on a Mon-Fri schedule, so a weekend day offers no slots
at all and this planner emits nothing for one. That doubles as a control: if the executed run ever
lands a booking on a day the plan skipped, it did not come from the plan.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

#: Mon-Fri only, keyed by ``date.weekday()``. Monday and Tuesday carry the week; Friday is quiet.
WEEKDAY_WEIGHTS: dict[int, float] = {0: 1.25, 1: 1.20, 2: 1.05, 3: 1.00, 4: 0.70}

#: Two working weeks.
WINDOW_DAYS = 14

FollowUp = Literal["none", "cancel", "reschedule"]

#: Roughly one booking in eight is later cancelled and one in twelve moved. Both are ordinary in an
#: appointment business, and each exercises a different mutation path than the create.
CANCEL_SHARE = 0.12
RESCHEDULE_SHARE = 0.08

#: The share of bookings taking the hour-long event type — the minority offering, as it usually is.
LONG_EVENT_SHARE = 0.25

_ES_NAMES = (
    "Ana Torres",
    "Carlos Mendez",
    "Lucia Fernandez",
    "Miguel Angel Ruiz",
    "Sofia Ramirez",
    "Javier Ortega",
    "Valentina Cruz",
    "Diego Herrera",
    "Camila Rojas",
    "Andres Pineda",
    "Isabel Navarro",
    "Ricardo Salas",
    "Paula Dominguez",
    "Tomas Aguilar",
    "Elena Castro",
)

_EN_NAMES = (
    "James Whitfield",
    "Emily Carter",
    "Michael Brennan",
    "Sarah Lindqvist",
    "David Okonkwo",
    "Jessica Hall",
    "Robert Nguyen",
    "Ashley Brooks",
    "Daniel Foster",
    "Megan Reilly",
    "Christopher Vance",
    "Laura Bennett",
    "Kevin Doyle",
    "Rachel Stone",
    "Brian Callahan",
)

#: Guests do not all sit in their business's timezone — a Katy customer travelling, a Miami patient
#: booking from Madrid. The slot grid converts per request, so a single-timezone run would never
#: exercise that conversion.
_GUEST_ZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/Madrid",
    "UTC",
)


@dataclass(frozen=True, slots=True)
class PlannedBooking:
    """One intended booking. The actual slot is chosen at execution time from what is on offer."""

    seq: int
    business_slug: str
    event_key: str
    day: date
    locale: str
    guest_name: str
    guest_email: str
    guest_timezone: str
    follow_up: FollowUp


def _guest_email(name: str, seq: int) -> str:
    """A run-unique, ASCII address under a domain that can never leave the throwaway stack."""
    handle = "".join(char if char.isalnum() else "." for char in name.lower()).strip(".")
    return f"{handle}.{seq}@guests.sim.test"


def allocate_by_weight(*, total: int, weights: list[float], rng: random.Random) -> list[int]:
    """Split ``total`` across ``weights`` so the parts sum to ``total`` EXACTLY. ==Pure.==

    The planner used to round each day independently — ``int(expected) + (1 if rng.random() <
    frac)`` — which is unbiased per day and says nothing about the sum. Fourteen independent coin
    flips do not have to land on the requested total, so "239 planned" could describe a plan that
    was never 239. ==§1 quotes that number and C13 reconciles against it==, which held only because
    C13 compares what the plan PRODUCED, not what was ASKED for; the two were free to differ.

    Largest remainder instead: every day takes its integer part, and the leftover — always fewer
    than ``len(weights)`` items — goes to the largest fractional parts. ==Ties are broken with the
    SEEDED rng==, never by list order, so the determinism the whole plan rests on survives while
    the total becomes an identity rather than an expectation.
    """
    if total <= 0 or not weights:
        return [0] * len(weights)
    # ==El contrato dice "suman total EXACTAMENTE"; eso solo era cierto si los pesos ya
    # venian normalizados.== Con pesos [1, 1] las partes sumaban el DOBLE, y el `remainder
    # <= 0` de abajo devolvia esa lista en silencio: una promesa del docstring que nada
    # hacia cumplir. Se normaliza aqui — idempotente sobre pesos ya normalizados — y una
    # suma no positiva o un peso negativo son un error explicito, no una respuesta
    # plausible calculada sobre una entrada que no significa nada.
    if any(weight < 0 for weight in weights):
        raise ValueError(f"pesos negativos no admiten reparto: {weights}")
    escala = sum(weights)
    if escala <= 0:
        raise ValueError(f"la suma de pesos debe ser positiva, es {escala}")
    exact = [total * weight / escala for weight in weights]
    counts = [int(value) for value in exact]
    remainder = total - sum(counts)
    if remainder <= 0:
        return counts
    # Shuffle first so equal fractional parts are ordered by the seed rather than by index, then
    # sort by fraction descending — Python's sort is stable, so the shuffle IS the tie-break.
    order = list(range(len(weights)))
    rng.shuffle(order)
    order.sort(key=lambda index: exact[index] - counts[index], reverse=True)
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def plan_two_weeks(
    *,
    business_slugs: list[str],
    locale_mix: dict[str, float],
    start: date,
    seed: int,
    bookings_per_business_per_week: int,
) -> list[PlannedBooking]:
    """Build the deterministic two-week demand plan.

    ``locale_mix`` maps a business slug to its probability of booking in Spanish; the remainder book
    in English. ``bookings_per_business_per_week`` is the target BEFORE weekday weighting, which
    redistributes it across Mon-Fri without materially changing the total.
    """
    rng = random.Random(seed)
    plan: list[PlannedBooking] = []
    seq = 0

    weekdays_in_window = [
        start + timedelta(days=offset)
        for offset in range(WINDOW_DAYS)
        if (start + timedelta(days=offset)).weekday() in WEEKDAY_WEIGHTS
    ]
    weight_total = sum(WEEKDAY_WEIGHTS[day.weekday()] for day in weekdays_in_window)
    target_total = bookings_per_business_per_week * 2

    for business_slug in business_slugs:
        spanish_share = locale_mix.get(business_slug, 0.5)
        per_day = allocate_by_weight(
            total=target_total,
            weights=[WEEKDAY_WEIGHTS[day.weekday()] / weight_total for day in weekdays_in_window],
            rng=rng,
        )
        for day, count in zip(weekdays_in_window, per_day, strict=True):
            for _ in range(count):
                seq += 1
                locale = "es" if rng.random() < spanish_share else "en"
                name = rng.choice(_ES_NAMES if locale == "es" else _EN_NAMES)
                roll = rng.random()
                follow_up: FollowUp = (
                    "cancel"
                    if roll < CANCEL_SHARE
                    else "reschedule"
                    if roll < CANCEL_SHARE + RESCHEDULE_SHARE
                    else "none"
                )
                plan.append(
                    PlannedBooking(
                        seq=seq,
                        business_slug=business_slug,
                        event_key="long" if rng.random() < LONG_EVENT_SHARE else "standard",
                        day=day,
                        locale=locale,
                        guest_name=name,
                        guest_email=_guest_email(name, seq),
                        guest_timezone=rng.choice(_GUEST_ZONES),
                        follow_up=follow_up,
                    )
                )
    return plan


def summarise_plan(plan: list[PlannedBooking]) -> dict[str, Any]:
    """Counts a reader can check the executed run against."""
    by_business: dict[str, int] = {}
    by_locale: dict[str, int] = {}
    by_weekday: dict[str, int] = {}
    by_follow_up: dict[str, int] = {}
    by_event: dict[str, int] = {}
    for item in plan:
        by_business[item.business_slug] = by_business.get(item.business_slug, 0) + 1
        by_locale[item.locale] = by_locale.get(item.locale, 0) + 1
        by_weekday[str(item.day.weekday())] = by_weekday.get(str(item.day.weekday()), 0) + 1
        by_follow_up[item.follow_up] = by_follow_up.get(item.follow_up, 0) + 1
        by_event[item.event_key] = by_event.get(item.event_key, 0) + 1
    return {
        "total": len(plan),
        "by_business": by_business,
        "by_locale": by_locale,
        "by_weekday": by_weekday,
        "by_follow_up": by_follow_up,
        "by_event_type": by_event,
        "distinct_days": len({item.day for item in plan}),
    }
