"""AetherCal API contract: Pydantic request/response schemas.

Only the stable, feature-agnostic primitives live at the package root; feature waves import their
models from ``aethercal.schemas.<feature>`` to keep this namespace collision-free.
"""

from __future__ import annotations

from aethercal.schemas.base import ErrorResponse, Page

__all__ = ["ErrorResponse", "Page"]
