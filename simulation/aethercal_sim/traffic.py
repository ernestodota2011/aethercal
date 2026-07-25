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
        for day in weekdays_in_window:
            share = WEEKDAY_WEIGHTS[day.weekday()] / weight_total
            expected = target_total * share
            # Round STOCHASTICALLY so the fractional part is not systematically lost: 3.4 becomes 3
            # or 4 with the right frequency, and the two-week total lands on target instead of
            # drifting low on every single day of the window.
            count = int(expected) + (1 if rng.random() < (expected - int(expected)) else 0)
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
