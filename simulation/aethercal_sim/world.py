"""The stack the harness was handed, and the world it builds on top of it.

``.stack.json`` is written by ``scripts/stack-up.sh`` and read here. ==Its absence is a hard error,
never a default.== A harness that invented ``http://localhost:8000`` when the file was missing would
happily run against whatever answered on that port — which, on a developer's machine, is exactly how
a "simulation" ends up writing hundreds of synthetic bookings into something nobody intended. The
file is the proof that a throwaway stack was deliberately brought up for this run.

.. rubric:: The four event types, and why each exists

``standard`` (30 min) and ``long`` (60 min) carry the organic two-week traffic on a **Mon-Fri
09:00-17:00** schedule, so weekends are closed the way a real business's are (``rules`` keys are
``date.weekday()``: Monday is 0, so 5 and 6 are absent rather than empty).

``micro`` (2 min) exists for one reason: **a no-show cannot be produced by compressing time.** The
product refuses to mark a booking that has not ended (``BookingNotEndedError``), and every bookable
slot is in the future — so on a 30-minute event type no booking made during a run can ever become a
no-show, and the transition would go untested while the report implied otherwise. A two-minute event
type on an always-open schedule ends *during* the run, which lets the real transition run against
the real guard instead of being skipped, faked, or unlocked with a clock trick.

``capped`` carries ``max_per_day=2`` and exists only to be refused — the daily-cap control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import Client

#: Mon-Fri 09:00-17:00, in each business's own timezone.
BUSINESS_HOURS: dict[str, list[dict[str, str]]] = {
    str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)
}

#: Always open. Only the ``micro`` event type uses it — see the module docstring.
ALWAYS_OPEN: dict[str, list[dict[str, str]]] = {
    str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)
}

#: How far ahead the world is bookable — comfortably past the two-week window the plan spans.
MAX_ADVANCE_SECONDS = 60 * 24 * 60 * 60

MICRO_DURATION_SECONDS = 120
CAPPED_MAX_PER_DAY = 2


@dataclass(frozen=True, slots=True)
class BusinessConfig:
    slug: str
    name: str
    timezone: str
    tenant_id: str
    host_user_id: str
    api_key: str


@dataclass(frozen=True, slots=True)
class StackConfig:
    api_url: str
    worker_url: str
    booking_url: str
    mailpit_url: str
    sink_url: str
    sink_webhook_url: str
    metrics_token: str
    businesses: list[BusinessConfig]


class StackUnavailableError(RuntimeError):
    """No ``.stack.json``. The run stops; it does not guess a URL."""


def load_stack(path: Path) -> StackConfig:
    """Read ``.stack.json``, or refuse to run."""
    if not path.exists():
        raise StackUnavailableError(
            f"{path} does not exist, so no throwaway stack was brought up for this run.\n"
            "\n"
            "Run simulation/scripts/stack-up.sh first. The harness deliberately does NOT fall back "
            "to a default URL: a simulation that guesses where to point is one that can end up "
            "writing hundreds of synthetic bookings into whatever happens to be listening — which "
            "is the single outcome this harness exists to make impossible."
        )
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise StackUnavailableError(f"{path} is not a JSON object")
    businesses_raw: Any = raw.get("businesses", [])
    if not isinstance(businesses_raw, list) or not businesses_raw:
        raise StackUnavailableError(f"{path} lists no businesses; stack-up.sh did not finish")
    businesses = [
        BusinessConfig(
            slug=str(item["slug"]),
            name=str(item["name"]),
            timezone=str(item["timezone"]),
            tenant_id=str(item["tenantId"]),
            host_user_id=str(item["hostUserId"]),
            api_key=str(item["apiKey"]),
        )
        for item in businesses_raw
        if isinstance(item, dict)
    ]
    return StackConfig(
        api_url=str(raw["apiUrl"]),
        worker_url=str(raw["workerUrl"]),
        booking_url=str(raw["bookingUrl"]),
        mailpit_url=str(raw["mailpitUrl"]),
        sink_url=str(raw["sinkUrl"]),
        sink_webhook_url=str(raw["sinkWebhookUrl"]),
        metrics_token=str(raw["metricsToken"]),
        businesses=businesses,
    )


@dataclass(frozen=True, slots=True)
class EventTypeRef:
    key: str
    id: str
    slug: str
    duration_seconds: int


@dataclass(frozen=True, slots=True)
class Business:
    config: BusinessConfig
    event_types: dict[str, EventTypeRef]
    webhook_secret: str

    @property
    def slug(self) -> str:
        return self.config.slug


@dataclass(frozen=True, slots=True)
class World:
    businesses: list[Business]

    def by_slug(self, slug: str) -> Business:
        for business in self.businesses:
            if business.slug == slug:
                return business
        raise KeyError(slug)


def _create_schedule(
    client: Client, *, name: str, timezone: str, rules: dict[str, list[dict[str, str]]]
) -> str:
    response = client.must(
        "POST", "/api/v1/schedules/", {"name": name, "timezone": timezone, "rules": rules}
    )
    body: Any = response.body
    return str(body["id"])


def _create_event_type(  # noqa: PLR0913 - one keyword per event-type field the world declares
    client: Client,
    *,
    host_id: str,
    schedule_id: str,
    key: str,
    slug: str,
    title: str,
    duration_seconds: int,
    max_per_day: int | None = None,
    increment_seconds: int | None = None,
) -> EventTypeRef:
    payload: dict[str, Any] = {
        "host_id": host_id,
        "schedule_id": schedule_id,
        "slug": slug,
        "title": title,
        # Both locales, because the organic traffic books in both and a world whose event types were
        # monolingual could not tell a localisation defect from a working one.
        "title_translations": {"es": title, "en": title},
        "duration_seconds": duration_seconds,
        "min_notice_seconds": 0,
        "max_advance_seconds": MAX_ADVANCE_SECONDS,
    }
    if max_per_day is not None:
        payload["max_per_day"] = max_per_day
    if increment_seconds is not None:
        payload["increment_seconds"] = increment_seconds
    response = client.must("POST", "/api/v1/event-types/", payload)
    body: Any = response.body
    return EventTypeRef(
        key=key,
        id=str(body["id"]),
        slug=str(body["slug"]),
        duration_seconds=int(body["duration_seconds"]),
    )


def provision(stack: StackConfig, *, run_id: str) -> World:
    """Build every business's schedules, event types and webhook. Fails loudly and early."""
    businesses: list[Business] = []
    for config in stack.businesses:
        client = Client(stack.api_url, config.api_key)
        hours_schedule = _create_schedule(
            client, name=f"hours-{run_id}", timezone=config.timezone, rules=BUSINESS_HOURS
        )
        always_schedule = _create_schedule(
            client, name=f"always-{run_id}", timezone=config.timezone, rules=ALWAYS_OPEN
        )

        event_types = {
            "standard": _create_event_type(
                client,
                host_id=config.host_user_id,
                schedule_id=hours_schedule,
                key="standard",
                slug=f"consulta-{run_id}",
                title="Consulta inicial",
                duration_seconds=30 * 60,
            ),
            "long": _create_event_type(
                client,
                host_id=config.host_user_id,
                schedule_id=hours_schedule,
                key="long",
                slug=f"evaluacion-{run_id}",
                title="Evaluacion completa",
                duration_seconds=60 * 60,
            ),
            "micro": _create_event_type(
                client,
                host_id=config.host_user_id,
                schedule_id=always_schedule,
                key="micro",
                slug=f"micro-{run_id}",
                title="Micro cita",
                duration_seconds=MICRO_DURATION_SECONDS,
                increment_seconds=MICRO_DURATION_SECONDS,
            ),
            "capped": _create_event_type(
                client,
                host_id=config.host_user_id,
                schedule_id=hours_schedule,
                key="capped",
                slug=f"capped-{run_id}",
                title="Cita con tope diario",
                duration_seconds=30 * 60,
                max_per_day=CAPPED_MAX_PER_DAY,
            ),
        }

        webhook = client.must(
            "POST",
            "/api/v1/webhooks",
            {
                "url": stack.sink_webhook_url,
                "events": ["booking.created", "booking.cancelled", "booking.rescheduled"],
            },
        )
        webhook_body: Any = webhook.body
        secret = str(webhook_body.get("secret", ""))
        if not secret:
            raise RuntimeError(
                f"{config.slug}: the API created a webhook without returning its secret."
            )
        businesses.append(Business(config=config, event_types=event_types, webhook_secret=secret))
    return World(businesses=businesses)
