"""Build the Reflex admin app and its ASGI mount target (F1-11, RF-18).

:func:`build_admin_app` configures the process-global runtime and assembles the four pages, guarding
every non-login page on ``AdminState.require_auth`` plus its data loader via ``on_load`` (and each
handler re-checks auth server-side). Routes are prefix-free ("/", "/login", ...).
:func:`build_admin_asgi` sets Reflex's ``frontend_path`` to the ``/admin`` mount prefix (the router
basename + asset prefix, so redirects/links never eject the operator out of the admin
subtree) and returns the ASGI app the server factory mounts. That config lives in the ASGI builder —
not in ``build_admin_app`` — so the (tested) app assembly never mutates Reflex's global config and
leaks into other Reflex users. The Reflex *backend* (state handlers) runs in-process and hits the
service layer
directly; the compiled *frontend* is a deploy-time (Node) concern, like the live scheduler in
``server.app``, so serving is guarded behind ``# pragma: no cover``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import reflex as rx

from aethercal.server.admin import pages
from aethercal.server.admin.mount import ADMIN_MOUNT_PATH
from aethercal.server.admin.runtime import AdminRuntime, configure_runtime
from aethercal.server.admin.state import AdminState

if TYPE_CHECKING:
    from starlette.types import ASGIApp


def build_admin_app(runtime: AdminRuntime) -> rx.App:
    """Assemble the Reflex admin bound to ``runtime`` (configures the process-global runtime)."""
    configure_runtime(runtime)
    app = rx.App()
    app.add_page(pages.login_page, route="/login", title="Sign in · AetherCal admin")
    app.add_page(
        pages.bookings_page,
        route="/",
        # ``load_event_types`` populates the event-type choices the range-select create panel needs.
        on_load=[
            AdminState.require_auth,
            AdminState.load_bookings,
            AdminState.load_event_types,
        ],
        title="Agenda · AetherCal admin",
    )
    app.add_page(
        pages.event_types_page,
        route="/event-types",
        on_load=[AdminState.require_auth, AdminState.load_event_types],
        title="Event types · AetherCal admin",
    )
    app.add_page(
        pages.schedules_page,
        route="/schedules",
        on_load=[AdminState.require_auth, AdminState.load_schedules],
        title="Schedules · AetherCal admin",
    )
    app.add_page(
        pages.workflows_page,
        route="/workflows",
        # ``load_workflows`` also loads the templates and the event types: a rule cannot be authored
        # without either (a step needs a body to render, and a rule needs a scope to choose).
        on_load=[AdminState.require_auth, AdminState.load_workflows],
        title="Notification rules · AetherCal admin",
    )
    return app


def build_admin_asgi(
    runtime: AdminRuntime,
) -> ASGIApp:  # pragma: no cover - live serve (needs Node)
    """Build the admin's ASGI app for mounting under ``/admin`` (frontend built at deploy time).

    Sets ``frontend_path`` so Reflex serves/routes the whole app under the ``/admin`` mount prefix —
    routes stay prefix-free and the router basename applies the prefix, so nothing hardcodes the
    mount point. Done here (not in :func:`build_admin_app`) so the tested app assembly never mutates
    Reflex's global config.
    """
    rx.config.get_config().frontend_path = ADMIN_MOUNT_PATH
    return build_admin_app(runtime)()


__all__ = ["build_admin_app", "build_admin_asgi"]
