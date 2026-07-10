"""Reflex pages for the admin (F1-11, RF-18).

Deliberately plain, first-party Reflex components only — no custom/local TSX bundle — so the whole
admin renders through Reflex's own (single, deduplicated) React. Every page binds to
:class:`AdminState`; the login page is public and every other page guards on
``AdminState.require_auth`` via its ``on_load`` (wired in ``app.py``), and each data handler
re-checks auth server-side. Nav/redirect paths are prefix-free ("/", "/login"); the ``/admin`` mount
prefix is applied by Reflex's ``frontend_path`` basename, so nothing hardcodes the mount point.
Forms submit as ``on_submit`` payloads so credentials/inputs flow to the handler without being
mirrored into persistent state vars.
"""

from __future__ import annotations

import reflex as rx
from reflex.vars import ObjectVar

from aethercal.server.admin.state import AdminState

_NAV = (("Agenda", "/"), ("Event types", "/event-types"), ("Schedules", "/schedules"))


def _error(message: str) -> rx.Component:
    """Show an error banner only when there is a message (``message`` is a reflex ``str`` var)."""
    return rx.cond(message != "", rx.callout(message, color_scheme="red", role="alert"))


def _nav_bar() -> rx.Component:
    """The shared top navigation + logout button (shown on every authenticated page)."""
    return rx.hstack(
        *[rx.link(label, href=href) for label, href in _NAV],
        rx.spacer(),
        rx.button("Log out", on_click=AdminState.logout, variant="soft"),
        width="100%",
        align="center",
        padding_y="0.5em",
    )


def _shell(title: str, *content: rx.Component) -> rx.Component:
    """Wrap authenticated page content in the nav bar + a titled container."""
    return rx.container(
        rx.vstack(
            _nav_bar(),
            rx.heading(title, size="6"),
            *content,
            spacing="4",
            width="100%",
        ),
        size="4",
        padding="1em",
    )


# --------------------------------------------------------------------------------------
# Login (public).
# --------------------------------------------------------------------------------------


def login_page() -> rx.Component:
    """The single-operator login form (RF-18)."""
    return rx.center(
        rx.card(
            rx.vstack(
                rx.heading("AetherCal admin", size="6"),
                _error(AdminState.error),
                rx.form(
                    rx.vstack(
                        rx.input(name="username", placeholder="Username", required=True),
                        rx.input(
                            name="password",
                            placeholder="Password",
                            type="password",
                            required=True,
                        ),
                        rx.button("Sign in", type="submit", width="100%"),
                        spacing="3",
                        width="100%",
                    ),
                    on_submit=AdminState.login,
                    width="100%",
                ),
                spacing="4",
                min_width="20em",
            ),
        ),
        height="100vh",
    )


# --------------------------------------------------------------------------------------
# Bookings / agenda.
# --------------------------------------------------------------------------------------


def _booking_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["start"]),
        rx.table.cell(row["guest"]),
        rx.table.cell(row["email"]),
        rx.table.cell(row["status"]),
        rx.table.cell(
            rx.button(
                "Cancel",
                on_click=AdminState.cancel(row["id"]),
                color_scheme="red",
                variant="soft",
                size="1",
            )
        ),
    )


def bookings_page() -> rx.Component:
    """The agenda: every booking, with cancel + a reschedule form."""
    return _shell(
        "Agenda",
        _error(AdminState.error),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Start"),
                    rx.table.column_header_cell("Guest"),
                    rx.table.column_header_cell("Email"),
                    rx.table.column_header_cell("Status"),
                    rx.table.column_header_cell("Actions"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.bookings, _booking_row)),
            width="100%",
        ),
        rx.heading("Reschedule a booking", size="4"),
        rx.form(
            rx.hstack(
                rx.input(name="booking_id", placeholder="Booking id", required=True),
                rx.input(name="new_start", type="datetime-local", required=True),
                rx.button("Reschedule", type="submit"),
                spacing="3",
                align="end",
            ),
            on_submit=AdminState.reschedule,
            reset_on_submit=True,
        ),
    )


# --------------------------------------------------------------------------------------
# Event types.
# --------------------------------------------------------------------------------------


def _event_type_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["slug"]),
        rx.table.cell(row["title"]),
        rx.table.cell(row["duration_min"]),
        rx.table.cell(row["active"]),
        rx.table.cell(
            rx.button(
                "Deactivate",
                on_click=AdminState.deactivate_event_type(row["id"]),
                color_scheme="red",
                variant="soft",
                size="1",
            )
        ),
    )


def event_types_page() -> rx.Component:
    """Event-type CRUD: a table, a create form, and a rename/re-duration form."""
    return _shell(
        "Event types",
        _error(AdminState.error),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Slug"),
                    rx.table.column_header_cell("Title"),
                    rx.table.column_header_cell("Minutes"),
                    rx.table.column_header_cell("Active"),
                    rx.table.column_header_cell("Actions"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.event_types, _event_type_row)),
            width="100%",
        ),
        rx.heading("Create an event type", size="4"),
        rx.form(
            rx.vstack(
                rx.input(name="slug", placeholder="slug (e.g. intro-call)", required=True),
                rx.input(name="title", placeholder="Title", required=True),
                rx.input(name="schedule", placeholder="Schedule name", required=True),
                rx.input(
                    name="duration_min",
                    type="number",
                    placeholder="Duration (minutes)",
                    required=True,
                ),
                rx.input(
                    name="max_advance_days",
                    type="number",
                    placeholder="Bookable up to N days ahead",
                    required=True,
                ),
                rx.button("Create", type="submit"),
                spacing="3",
                width="100%",
                max_width="24em",
            ),
            on_submit=AdminState.create_event_type,
            reset_on_submit=True,
        ),
        rx.heading("Update an event type", size="4"),
        rx.form(
            rx.hstack(
                rx.input(name="id", placeholder="Event type id", required=True),
                rx.input(name="title", placeholder="New title (optional)"),
                rx.input(name="duration_min", type="number", placeholder="New minutes (optional)"),
                rx.button("Update", type="submit"),
                spacing="3",
                align="end",
            ),
            on_submit=AdminState.update_event_type,
            reset_on_submit=True,
        ),
    )


# --------------------------------------------------------------------------------------
# Schedules.
# --------------------------------------------------------------------------------------


def _schedule_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["name"]),
        rx.table.cell(row["timezone"]),
        rx.table.cell(row["weekdays"]),
        rx.table.cell(row["id"]),
        rx.table.cell(
            rx.button(
                "Delete",
                on_click=AdminState.delete_schedule(row["id"]),
                color_scheme="red",
                variant="soft",
                size="1",
            )
        ),
    )


def schedules_page() -> rx.Component:
    """Schedule CRUD: a table, a create form, and a rename form."""
    return _shell(
        "Schedules",
        _error(AdminState.error),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Name"),
                    rx.table.column_header_cell("Timezone"),
                    rx.table.column_header_cell("Weekdays"),
                    rx.table.column_header_cell("Id"),
                    rx.table.column_header_cell("Actions"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.schedules, _schedule_row)),
            width="100%",
        ),
        rx.heading("Create a schedule", size="4"),
        rx.form(
            rx.vstack(
                rx.input(name="name", placeholder="Name (e.g. Weekdays)", required=True),
                rx.input(name="timezone", placeholder="IANA timezone (e.g. UTC)", required=True),
                rx.input(
                    name="weekdays", placeholder="Weekdays CSV, 0=Mon..6=Sun (e.g. 0,1,2,3,4)"
                ),
                rx.input(name="start", type="time", placeholder="Start", required=True),
                rx.input(name="end", type="time", placeholder="End", required=True),
                rx.button("Create", type="submit"),
                spacing="3",
                width="100%",
                max_width="24em",
            ),
            on_submit=AdminState.create_schedule,
            reset_on_submit=True,
        ),
        rx.heading("Rename a schedule", size="4"),
        rx.form(
            rx.hstack(
                rx.input(name="id", placeholder="Schedule id", required=True),
                rx.input(name="name", placeholder="New name (optional)"),
                rx.input(name="timezone", placeholder="New timezone (optional)"),
                rx.button("Update", type="submit"),
                spacing="3",
                align="end",
            ),
            on_submit=AdminState.update_schedule,
            reset_on_submit=True,
        ),
    )


__all__ = ["bookings_page", "event_types_page", "login_page", "schedules_page"]
