"""EventType and Buffer: the definition of a bookable meeting (input to the slots engine)."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, model_validator

_ZERO = timedelta(0)


class Buffer(BaseModel):
    """Padding reserved around a meeting so back-to-back bookings get breathing room.

    ``before`` extends the busy check earlier than the slot start and ``after`` extends it past
    the slot end; both are non-negative and default to zero (no padding).
    """

    model_config = ConfigDict(frozen=True)

    before: timedelta = _ZERO
    after: timedelta = _ZERO

    @model_validator(mode="after")
    def _validate(self) -> Buffer:
        if self.before < _ZERO:
            raise ValueError("Buffer.before must be >= 0")
        if self.after < _ZERO:
            raise ValueError("Buffer.after must be >= 0")
        return self


class EventType(BaseModel):
    """A bookable meeting type: how long it is, how it is spaced, and its booking window.

    ``duration`` is the slot length (> 0). ``buffer`` pads the busy check around each slot.
    ``increment`` is the step between candidate slot starts; when ``None`` it defaults to
    ``duration`` (back-to-back slots), and a smaller value yields overlapping candidate starts
    (e.g. 30-min meetings offered every 15 min). ``min_notice`` is the lead time a slot must be
    in the future (>= 0) and ``max_advance`` is how far ahead booking is allowed (> 0).
    """

    model_config = ConfigDict(frozen=True)

    duration: timedelta
    buffer: Buffer = Buffer()
    increment: timedelta | None = None
    min_notice: timedelta = _ZERO
    max_advance: timedelta

    @model_validator(mode="after")
    def _validate(self) -> EventType:
        if self.duration <= _ZERO:
            raise ValueError("EventType.duration must be strictly positive")
        if self.increment is not None and self.increment <= _ZERO:
            raise ValueError("EventType.increment must be strictly positive when set")
        if self.min_notice < _ZERO:
            raise ValueError("EventType.min_notice must be >= 0")
        if self.max_advance <= _ZERO:
            raise ValueError("EventType.max_advance must be strictly positive")
        return self

    @property
    def effective_increment(self) -> timedelta:
        """The step between candidate slot starts (``increment`` or ``duration`` if unset)."""
        return self.increment if self.increment is not None else self.duration
