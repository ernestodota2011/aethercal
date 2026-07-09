"""The cal.com webhook contract AetherCal must remain compatible with (F0-12, RF-17).

AetherCal replaces the agency's cal.com; three existing services consume cal.com's webhooks. The
agency's ``cal-com-webhook`` service parses them with a Pydantic model that ignores unknown fields
and requires exactly the fields checked here (mirrored from that service's ``app/models.py``, the
source of truth). AetherCal's outgoing webhooks (F1-09) and the compatibility adapter (F1-14) must
keep producing a payload that passes ``validate_calcom_webhook``. The fixtures in
``tests/fixtures/calcom/`` were structurally verified against real cal.com API bookings but carry
only synthetic data (public repo).
"""

from __future__ import annotations

from typing import Any

TRIGGERS = frozenset({"BOOKING_CREATED", "BOOKING_RESCHEDULED", "BOOKING_CANCELLED"})


class CalcomContractError(ValueError):
    """A cal.com webhook payload is missing or malforms a field the agency consumer requires."""


def validate_calcom_webhook(event: dict[str, Any]) -> None:
    """Raise ``CalcomContractError`` unless ``event`` meets the required webhook contract."""
    trigger = event.get("triggerEvent")
    if trigger not in TRIGGERS:
        raise CalcomContractError(
            f"triggerEvent must be one of {sorted(TRIGGERS)}, got {trigger!r}"
        )

    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise CalcomContractError("payload must be an object")

    _require_str(payload, "startTime")
    _require_int(payload, "eventTypeId")

    organizer = payload.get("organizer")
    if not isinstance(organizer, dict):
        raise CalcomContractError("payload.organizer must be an object")
    _require_int(organizer, "id")
    _require_str(organizer, "name")
    _require_str(organizer, "email")

    attendees = payload.get("attendees")
    if not isinstance(attendees, list) or not attendees:
        raise CalcomContractError("payload.attendees must be a non-empty list")
    for index, attendee in enumerate(attendees):
        if not isinstance(attendee, dict):
            raise CalcomContractError(f"payload.attendees[{index}] must be an object")
        _require_str(attendee, "email")
        _require_str(attendee, "name")


def _require_str(obj: dict[str, Any], key: str) -> None:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise CalcomContractError(f"missing or invalid required string field {key!r}")


def _require_int(obj: dict[str, Any], key: str) -> None:
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CalcomContractError(f"missing or invalid required integer field {key!r}")
