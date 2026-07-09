"""Booking: a reserved interval with a lifecycle status."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aethercal.core.model.interval import TimeInterval


class BookingStatus(StrEnum):
    """Lifecycle of a booking. Only cancelled bookings free their slot."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Booking(BaseModel):
    """A reserved time interval. A booking occupies its slot unless it is cancelled."""

    model_config = ConfigDict(frozen=True)

    interval: TimeInterval
    status: BookingStatus = BookingStatus.CONFIRMED

    @property
    def occupies(self) -> bool:
        """True if this booking blocks its slot (i.e. it is not cancelled)."""
        return self.status is not BookingStatus.CANCELLED
