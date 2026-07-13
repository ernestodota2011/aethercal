"""The email composer must USE the event-type translations that are already in the database.

``event_types.title_translations`` has existed since migration 0004; the admin writes it, the API
returns it, and the booking page renders it through ``resolve_title``. The email composer takes a
``locale`` too — but it only ever reached the surrounding COPY. The title itself was read straight
off ``event_type.title``, the tenant's base-locale text.

So an English guest received::

    Subject: Booking confirmed: Consulta 30 min
    Hi Ada, your booking "Consulta 30 min" is confirmed.

Localised scaffolding wrapped around an unlocalised title — and the same in the ``.ics`` SUMMARY,
which lands in the guest's calendar and stays there long after the mail is deleted.

The root cause was a SECOND answer to "which title do we show?": the booking page had
``resolve_title``, the composer had ``event_type.title``. The fix is not a third answer — it is to
extract the rule itself (``resolve_translation``) so both shapes reach the SAME one. These tests pin
both halves: that the ORM row can reach the shared rule, and that every surface of the email now
uses it — subject, body, and the ``.ics`` SUMMARY.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.event_types import (
    EventTypeRead,
    resolve_description,
    resolve_title,
    resolve_translation,
)
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.notifications import compose_booking_notification

TenantFactory = Callable[..., Awaitable[Tenant]]

_START = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)

_CANONICAL_TITLE = "Consulta 30 min"
_ENGLISH_TITLE = "30-minute consultation"


def _event_type(**overrides: object) -> EventType:
    """A bare ORM EventType — not persisted; these are pure-lookup assertions."""
    base: dict[str, object] = {
        "title": _CANONICAL_TITLE,
        "title_translations": {"en": _ENGLISH_TITLE},
        "description": "Charla inicial.",
        "description_translations": {"en": "A short intro call."},
    }
    base.update(overrides)
    return EventType(**base)


async def _seed_booking(session: AsyncSession, tenant_factory: TenantFactory) -> Booking:
    """A booking whose event type carries an English title override in the database."""
    tenant = await tenant_factory(
        session, email="host@example.com", name="Grace Host", timezone="America/New_York"
    )
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="America/New_York", rules={})
    session.add(schedule)
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="consulta",
        title=_CANONICAL_TITLE,
        title_translations={"en": _ENGLISH_TITLE},
        description_translations={"en": "A short intro call."},
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )
    session.add(event_type)
    await session.flush()
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_START,
        end_at=_START + timedelta(minutes=30),
        guest_name="Ada Guest",
        guest_email="guest@example.com",
        guest_timezone="America/New_York",
    )
    session.add(booking)
    await session.flush()
    return booking


# --------------------------------------------------------------------------------------
# ONE rule, reachable from both shapes: the ORM row resolves through the same primitive the
# booking page's `resolve_title` delegates to.
# --------------------------------------------------------------------------------------


def test_the_shared_rule_resolves_the_orm_rows_values() -> None:
    """The whole point of the fix. Two implementations of "which title?" is what let the email
    ignore the translations for an entire release; there is now exactly one, and the ORM row —
    which is what the composer holds — can reach it."""
    event_type = _event_type()

    assert resolve_translation(event_type.title, event_type.title_translations, "en") == (
        _ENGLISH_TITLE
    )
    assert resolve_translation(event_type.title, event_type.title_translations, "es") == (
        _CANONICAL_TITLE
    )


def test_a_blank_override_is_not_a_translation() -> None:
    """What an admin form submits for "I did not fill this in". Serving it would mail a booking with
    an EMPTY title — the silent no-op, wearing a different hat."""
    event_type = _event_type(title_translations={"en": ""})

    assert resolve_translation(event_type.title, event_type.title_translations, "en") == (
        _CANONICAL_TITLE
    )


def test_the_booking_pages_resolver_delegates_to_the_same_rule() -> None:
    """``resolve_title``/``resolve_description`` are now thin wrappers over the primitive, so the
    page and the email can never drift apart again."""
    event = EventTypeRead.model_validate(
        {
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "host_id": uuid.uuid4(),
            "schedule_id": uuid.uuid4(),
            "slug": "consulta",
            "title": _CANONICAL_TITLE,
            "description": "Charla inicial.",
            "title_translations": {"en": _ENGLISH_TITLE},
            "description_translations": {"en": "A short intro call."},
            "location": None,
            "duration_seconds": 1800,
            "buffer_before_seconds": 0,
            "buffer_after_seconds": 0,
            "min_notice_seconds": 0,
            "max_advance_seconds": 2_592_000,
            "increment_seconds": None,
            "max_per_day": None,
            "questions": [],
            "active": True,
        }
    )

    assert resolve_title(event, "en") == _ENGLISH_TITLE
    assert resolve_description(event, "en") == "A short intro call."
    assert resolve_title(event, "es") == _CANONICAL_TITLE
    assert resolve_description(event, "es") == "Charla inicial."


# --------------------------------------------------------------------------------------
# The defect itself: every surface of the email must read them.
# --------------------------------------------------------------------------------------


async def test_the_english_email_carries_the_english_title(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)

    message = await compose_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        locale="en",
    )
    body = message.get_body(("plain",))
    assert body is not None

    assert message["Subject"] == f"Booking confirmed: {_ENGLISH_TITLE}"
    assert _ENGLISH_TITLE in body.get_content()
    assert _CANONICAL_TITLE not in body.get_content()


async def test_a_regional_locale_tag_still_finds_the_english_title(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """A browser sends ``en-US``; the tenant stored ``en``. The composer already normalises the tag
    for its copy — the title lookup must be normalised by the SAME rule, or every English speaker
    with a regional locale silently gets the Spanish title back."""
    booking = await _seed_booking(sqlite_session, tenant_factory)

    message = await compose_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        locale="en-US",
    )

    assert message["Subject"] == f"Booking confirmed: {_ENGLISH_TITLE}"


async def test_the_ics_summary_is_localized_too(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The ``.ics`` SUMMARY is what the guest sees FOREVER, in their own calendar — long after the
    email is deleted. An unlocalised title there is the most durable version of this bug."""
    booking = await _seed_booking(sqlite_session, tenant_factory)

    message = await compose_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        locale="en",
    )

    ics = next(
        part.get_content()
        for part in message.iter_attachments()
        if part.get_content_type() == "text/calendar"
    )
    event = Calendar.from_ical(ics).walk("VEVENT")[0]
    assert str(event["SUMMARY"]) == _ENGLISH_TITLE


async def test_the_spanish_email_still_carries_the_canonical_title(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The fallback path: Spanish is the base locale and has no override row."""
    booking = await _seed_booking(sqlite_session, tenant_factory)

    message = await compose_booking_notification(
        sqlite_session,
        kind=NotificationKind.CONFIRMATION,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        locale="es",
    )

    assert message["Subject"] == f"Reserva confirmada: {_CANONICAL_TITLE}"
