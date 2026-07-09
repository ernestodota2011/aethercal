"""Contract tests: cal.com webhook fixtures satisfy the shape AetherCal must stay compatible with.

See tests/fixtures/calcom/README.md for provenance. The contract mirrors the agency's
cal-com-webhook consumer (app/models.py); F1-09/F1-14 must keep producing payloads that pass
validate_calcom_webhook.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from aethercal.server.integrations.calcom.contract import (
    TRIGGERS,
    CalcomContractError,
    validate_calcom_webhook,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "calcom"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "trigger"),
    [
        ("booking_created.json", "BOOKING_CREATED"),
        ("booking_cancelled.json", "BOOKING_CANCELLED"),
        ("booking_rescheduled.json", "BOOKING_RESCHEDULED"),
    ],
)
def test_fixture_satisfies_the_calcom_contract(name: str, trigger: str) -> None:
    event = _load(name)
    assert event["triggerEvent"] == trigger
    assert trigger in TRIGGERS
    validate_calcom_webhook(event)  # raises if the contract is violated
    payload = event["payload"]
    assert payload["attendees"][0]["email"]
    assert payload["organizer"]["email"]


def test_rescheduled_fixture_references_the_previous_booking() -> None:
    # F1-14 must correlate a reschedule with the booking it replaces.
    assert _load("booking_rescheduled.json")["payload"]["rescheduleUid"]


def test_cancelled_fixture_carries_a_cancellation_reason() -> None:
    assert _load("booking_cancelled.json")["payload"]["cancellationReason"]


def test_contract_rejects_missing_organizer() -> None:
    bad = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "startTime": "2026-07-10T16:00:00.000Z",
            "eventTypeId": 1,
            "attendees": [{"email": "a@b.c", "name": "A"}],
        },
    }
    with pytest.raises(CalcomContractError, match="organizer"):
        validate_calcom_webhook(bad)


def test_contract_rejects_empty_attendees() -> None:
    bad = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "startTime": "2026-07-10T16:00:00.000Z",
            "eventTypeId": 1,
            "organizer": {"id": 1, "name": "A", "email": "a@b.c"},
            "attendees": [],
        },
    }
    with pytest.raises(CalcomContractError, match="attendees"):
        validate_calcom_webhook(bad)


def test_contract_rejects_unknown_trigger() -> None:
    with pytest.raises(CalcomContractError, match="triggerEvent"):
        validate_calcom_webhook({"triggerEvent": "BOOKING_NONSENSE", "payload": {}})
