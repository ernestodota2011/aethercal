"""Liveness endpoint: ``GET /api/v1/health`` → ``{"status": "ok"}`` (no auth)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """The health payload."""

    status: str


@router.get("/health")
async def health() -> HealthStatus:
    """Report that the process is up. Deliberately unauthenticated and DB-free."""
    return HealthStatus(status="ok")
