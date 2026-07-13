"""Booking: a reserved interval with a lifecycle status."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aethercal.core.model.interval import TimeInterval


class BookingStatus(StrEnum):
    """Lifecycle of a booking. ``cancelled`` is the ONLY status that frees the slot.

    * ``pending`` — reserved but not yet confirmed. It holds its slot.
    * ``confirmed`` — the booking is live.
    * ``cancelled`` — the slot is free again (the guest cancelled, or a reschedule replaced this row
      with its successor).
    * ``no_show`` — the guest never showed up. It **keeps occupying** its slot: the appointment time
      has already passed, so freeing it would corrupt history and let a retroactive booking be
      written over it. This is exactly why the ``WHERE status <> 'cancelled'`` partial unique index
      needs no change to accommodate it.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class Booking(BaseModel):
    """A reserved time interval. A booking occupies its slot unless it is cancelled."""

    model_config = ConfigDict(frozen=True)

    interval: TimeInterval
    status: BookingStatus = BookingStatus.CONFIRMED

    @property
    def occupies(self) -> bool:
        """True if this booking blocks its slot (i.e. it is not cancelled).

        Deliberately "everything except cancelled" rather than an allow-list of occupying statuses:
        a status added later then defaults to the SAFE side (it holds its slot) instead of silently
        re-offering a slot something else already owns.
        """
        return self.status is not BookingStatus.CANCELLED
