"""Timezone-aware slot formatting and day grouping for the picker (RF-06).

The API hands us slots as absolute UTC instants plus the display timezone the guest asked for. This
module turns those into what a person reads: a localized clock time (24h in Spanish, 12h in
English), a localized day heading, and slots grouped under the local day they fall on. Month and
weekday names are a small in-module table so we localize deterministically without a heavy i18n
dependency (``locale``/CLDR would add a runtime and platform-variance surface we don't want here).

Each rendered :class:`SlotChoice` keeps the original instant as an ISO-8601 UTC string (``iso``) so
the form submits the exact absolute time the guest saw, immune to their display-zone choice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aethercal.booking.i18n import Locale
from aethercal.schemas.slots import SlotRead

# Localized names indexed to match ``date.weekday()`` (Mon=0) and ``date.month`` (Jan=1, so a
# leading placeholder keeps the 1-based index readable).
_WEEKDAYS: dict[Locale, tuple[str, ...]] = {
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
_MONTHS: dict[Locale, tuple[str, ...]] = {
    "es": (
        "",
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "en": (
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


@dataclass(frozen=True, slots=True)
class SlotChoice:
    """One bookable time the picker renders: a localized ``label`` and its absolute ``iso``."""

    label: str
    iso: str


@dataclass(frozen=True, slots=True)
class DayGroup:
    """The slots that fall on one local ``day``, under a localized ``heading``."""

    day: date
    heading: str
    slots: list[SlotChoice]


def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def today_in_zone(now: datetime, tz: str) -> date:
    """The calendar date it currently is in ``tz`` (used to anchor the default window)."""
    return now.astimezone(_zone(tz)).date()


def format_time(instant: datetime, tz: str, locale: Locale) -> str:
    """Localized clock time for ``instant`` in ``tz`` — 24-hour for ES, 12-hour AM/PM for EN."""
    local = instant.astimezone(_zone(tz))
    if locale == "en":
        hour_12 = local.hour % 12 or 12
        meridiem = "AM" if local.hour < 12 else "PM"
        return f"{hour_12}:{local.minute:02d} {meridiem}"
    return f"{local.hour:02d}:{local.minute:02d}"


def format_day_heading(day: date, locale: Locale) -> str:
    """A localized day heading — ``"martes 14 de julio"`` / ``"Tuesday, July 14"``."""
    weekday = _WEEKDAYS[locale][day.weekday()]
    month = _MONTHS[locale][day.month]
    if locale == "en":
        return f"{weekday}, {month} {day.day}"
    return f"{weekday} {day.day} de {month}"


def slot_aria_label(time_label: str, day_heading: str) -> str:
    """A screen-reader label combining a slot's ``time_label`` and localized ``day_heading``.

    A bare time ("09:00") doesn't tell an assistive-tech user which day the slot falls on (I2) —
    every rendered slot control pairs its visible time with this fuller label.
    """
    return f"{time_label}, {day_heading}"


def group_slots(slots: Sequence[SlotRead], tz: str, locale: Locale) -> list[DayGroup]:
    """Group slots (in chronological order) under the local day they fall on in ``tz``."""
    zone = _zone(tz)
    groups: list[DayGroup] = []
    index: dict[date, DayGroup] = {}
    for slot in slots:
        local = slot.start.astimezone(zone)
        day = local.date()
        group = index.get(day)
        if group is None:
            group = DayGroup(day=day, heading=format_day_heading(day, locale), slots=[])
            index[day] = group
            groups.append(group)
        group.slots.append(
            SlotChoice(
                label=format_time(slot.start, tz, locale),
                iso=slot.start.astimezone(UTC).isoformat(),
            )
        )
    return groups


__all__ = [
    "DayGroup",
    "SlotChoice",
    "format_day_heading",
    "format_time",
    "group_slots",
    "slot_aria_label",
    "today_in_zone",
]
