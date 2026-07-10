"""The 24 h booking reminder (RF-10): the scheduling seam, the pure job body, and the live runner.

Three layers, isolated so the logic is fully offline-testable:

* :class:`TaskRunner` — the tiny "schedule a callable to run at a datetime" protocol. Tests inject a
  fake that records the scheduled call; production wires :class:`ApschedulerTaskRunner`.
* :func:`run_booking_reminder` — the **pure** job body. Loads the booking, sends the reminder only
  when it is still ``confirmed`` and not already reminded (idempotency delegated to the ledger).
* the live edges — :func:`run_booking_reminder_job` (the picklable entrypoint APScheduler stores by
  reference) and :class:`ApschedulerTaskRunner` (a persistent ``SQLAlchemyJobStore``) — carry a
  ``# pragma: no cover - live`` seam. Importing this module constructs and starts no scheduler.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.config import DatabaseConfig
from aethercal.server.db.models import Booking
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender
from aethercal.server.services.notifications import send_booking_notification


class TaskRunner(Protocol):
    """Schedules a callable to run once at a datetime, keyed by a stable job id."""

    def schedule(
        self,
        func: Callable[..., object],
        *,
        run_at: datetime,
        job_id: str,
        kwargs: Mapping[str, object] | None = None,
    ) -> None:
        """Schedule ``func(**kwargs)`` to run at ``run_at``, replacing any job with ``job_id``."""
        ...


def reminder_job_id(booking_id: uuid.UUID) -> str:
    """Deterministic job id for a booking's reminder → rescheduling replaces it (RF-10)."""
    return f"reminder:{booking_id}"


async def run_booking_reminder(
    session: AsyncSession, *, booking_id: uuid.UUID, sender: EmailSender, now: datetime
) -> bool:
    """Send the 24 h reminder for ``booking_id`` if it is still confirmed and not already reminded.

    A missing booking, or one that is no longer ``confirmed`` (e.g. cancelled), sends nothing and
    returns ``False``. Otherwise it delegates to :func:`send_booking_notification` with kind
    ``reminder`` — idempotent through the :class:`SentNotification` ledger, so re-running an
    already-reminded booking is a no-op that returns ``False``. Returns whether an email was sent.
    Flushes; the caller owns the commit.
    """
    booking = await session.get(Booking, booking_id)
    if booking is None or booking.status != BookingStatus.CONFIRMED:
        return False
    return await send_booking_notification(
        session,
        kind=NotificationKind.REMINDER,
        booking=booking,
        cancel_url=None,
        reschedule_url=None,
        sender=sender,
        now=now,
    )


def schedule_reminder(runner: TaskRunner, *, booking: Booking, send_at: datetime) -> None:
    """Schedule the 24 h-before reminder for ``booking`` at ``send_at`` (RF-10).

    ``send_at`` is ``booking.start_at - 24h`` (computed by the caller — F1-05 on booking create).
    Only the picklable ``booking_id`` is handed to the job; the live entrypoint rebuilds its own
    session + sender so the reminder survives a process restart in the persistent jobstore.
    """
    runner.schedule(
        run_booking_reminder_job,
        run_at=send_at,
        job_id=reminder_job_id(booking.id),
        kwargs={"booking_id": str(booking.id)},
    )


async def run_booking_reminder_job(booking_id: str) -> None:  # pragma: no cover - live
    """Live scheduler entrypoint: rebuild a session + SMTP sender from the environment and run the
    reminder. APScheduler stores this by reference with only the picklable ``booking_id`` string, so
    the infrastructure is reconstructed here. Never run by the offline suite.
    """
    engine = create_async_engine(DatabaseConfig.from_env().url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        sender = SmtpEmailSender(SmtpConfig.from_env())
        async with maker() as session, session.begin():
            await run_booking_reminder(
                session,
                booking_id=uuid.UUID(booking_id),
                sender=sender,
                now=datetime.now(UTC),
            )
    finally:
        await engine.dispose()


class ApschedulerTaskRunner:  # pragma: no cover - live
    """A live :class:`TaskRunner` backed by APScheduler with a persistent Postgres jobstore.

    Constructing it (or importing this module) starts nothing — the caller owns ``.start()``.
    APScheduler is only partially typed, so the scheduler is held behind an ``Any`` seam. Reminders
    scheduled through :meth:`schedule` survive a process restart via ``SQLAlchemyJobStore``.
    """

    def __init__(self, scheduler: Any) -> None:
        self._scheduler = scheduler

    @classmethod
    def with_postgres_jobstore(cls, database_url: str) -> ApschedulerTaskRunner:
        """Build a runner whose jobs persist in Postgres. Does NOT start the scheduler."""
        scheduler: Any = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=database_url)}
        )
        return cls(scheduler)

    def schedule(
        self,
        func: Callable[..., object],
        *,
        run_at: datetime,
        job_id: str,
        kwargs: Mapping[str, object] | None = None,
    ) -> None:
        """Register a one-shot ``date`` job, overwriting any existing job with ``job_id``."""
        self._scheduler.add_job(
            func,
            trigger="date",
            run_date=run_at,
            id=job_id,
            kwargs=dict(kwargs or {}),
            replace_existing=True,
        )


__all__ = [
    "ApschedulerTaskRunner",
    "TaskRunner",
    "reminder_job_id",
    "run_booking_reminder",
    "run_booking_reminder_job",
    "schedule_reminder",
]
