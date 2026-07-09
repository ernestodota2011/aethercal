"""Domain errors raised by conflict detection."""

from __future__ import annotations

from aethercal.core.model import TimeInterval


class SlotConflictError(Exception):
    """Raised when a candidate interval collides with an occupying booking (RF-04).

    Carries the offending ``candidate`` and the specific booked ``conflict`` interval it
    overlapped, so a booking service can surface a precise message to the caller.
    """

    def __init__(self, candidate: TimeInterval, conflict: TimeInterval) -> None:
        self.candidate = candidate
        self.conflict = conflict
        super().__init__(
            f"candidate [{candidate.start.isoformat()}, {candidate.end.isoformat()}) "
            f"conflicts with booked [{conflict.start.isoformat()}, {conflict.end.isoformat()})"
        )
