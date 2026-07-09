"""Occurrence: a single resolved instance of an event, as an absolute interval."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from aethercal.core.model.interval import TimeInterval


class Occurrence(BaseModel):
    """One concrete instance produced by expanding an :class:`Event` over a window.

    ``interval`` holds timezone-aware bounds (the absolute instants). ``is_override`` marks an
    instance that replaces a regular recurrence occurrence (a modified instance).
    """

    model_config = ConfigDict(frozen=True)

    interval: TimeInterval
    is_override: bool = False

    @property
    def start(self) -> datetime:
        return self.interval.start

    @property
    def end(self) -> datetime:
        return self.interval.end
