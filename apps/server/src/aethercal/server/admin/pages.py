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

from aethercal.schemas.workflows import (
    CHANNEL_NAMES,
    TEMPLATE_VARIABLES,
    WORKFLOW_TRIGGER_NAMES,
)
from aethercal.server.admin.format import ALL_EVENT_TYPES, SHARED_SCHEDULE
from aethercal.server.admin.state import AdminState
from aethercal.ui import Calendar

_NAV = (
    ("Agenda", "/"),
    ("Health", "/health"),
    ("Hosts", "/hosts"),
    ("Event types", "/event-types"),
    ("Schedules", "/schedules"),
    ("Rules", "/workflows"),
)


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


def _calendar() -> rx.Component:
    """The AetherCal calendar bound to the admin state (ES locale, Monday-first, neutral theme).

    The component's built-in navigation toolbar (F2-NAV) owns previous / today / next AND the
    month/week/day/list/timeline view switcher, driving the controlled ``anchor`` / ``view`` state.
    Drag or resize reschedule (optimistically), a range-select opens the create panel, and a click
    selects a booking to manage — every gesture routed to a state handler that reuses the booking
    service.

    ==On the RF-28 timeline, dragging a booking ACROSS rows is refused.== The component offers the
    gesture, but the backend has no operation for it: a booking has no host of its own — the host
    lives on its EVENT TYPE — so moving it to another host would mean changing its event type, and
    with it its duration and its public link. That semantics has not been designed, so the drag is
    declined with its reason rather than guessed at (``_CROSS_HOST_DRAG_MESSAGE``).
    """
    return rx.box(
        Calendar.create(
            view=AdminState.calendar_view,
            events=AdminState.calendar_events,
            # RF-28: the timeline's rows are the tenant's HOSTS. The other four views ignore them.
            resources=AdminState.calendar_resources,
            anchor=AdminState.calendar_anchor,
            navigation=True,
            locale="es",
            first_day_of_week=1,
            theme="light",
            on_event_drop=AdminState.on_calendar_event_drop,
            on_event_resize=AdminState.on_calendar_event_resize,
            on_range_select=AdminState.on_calendar_range_select,
            on_event_click=AdminState.on_calendar_event_click,
            on_view_change=AdminState.on_calendar_view_change,
            on_range_change=AdminState.on_calendar_range_change,
        ),
        width="100%",
        min_height="32em",
    )


def _manage_panel() -> rx.Component:
    """The click-to-manage panel (RF-22): view the selected booking, reschedule it accessibly
    (no ID typing — it uses the selected booking), cancel, or close. The booking id is shown here
    (copyable) so it is discoverable for the manual fallback form too.
    """
    return rx.cond(
        AdminState.selected_booking_id != "",
        rx.card(
            rx.vstack(
                rx.heading("Reserva seleccionada", size="4"),
                rx.text(AdminState.selected_booking_guest),
                rx.text(AdminState.selected_booking_start),
                rx.code(AdminState.selected_booking_id),
                rx.form(
                    rx.hstack(
                        rx.input(name="new_start", type="datetime-local", required=True),
                        rx.button("Reprogramar", type="submit"),
                        spacing="3",
                        align="end",
                    ),
                    on_submit=AdminState.reschedule_selected,
                    reset_on_submit=True,
                ),
                rx.hstack(
                    rx.button(
                        "Cancelar reserva",
                        on_click=AdminState.cancel(AdminState.selected_booking_id),
                        color_scheme="red",
                        variant="soft",
                    ),
                    # RF-25. Offered rather than hidden behind a rule computed on the client: only
                    # the database knows, at the instant of the click, whether this booking is still
                    # confirmed and whether it has ended. A refusal comes back in the service's own
                    # words, which tells the operator more than a silently disabled button.
                    rx.button(
                        "Marcar no-show",
                        on_click=AdminState.mark_no_show(AdminState.selected_booking_id),
                        color_scheme="amber",
                        variant="soft",
                    ),
                    rx.button("Cerrar", on_click=AdminState.clear_selection, variant="soft"),
                    spacing="3",
                ),
                rx.text(
                    "También puedes arrastrar la reserva en el calendario para moverla. "
                    "Un no-show solo se puede marcar después de que la cita haya terminado, y NO "
                    "libera el horario: la hora ya pasó.",
                    size="1",
                    color_scheme="gray",
                ),
                spacing="3",
            ),
        ),
    )


def _new_booking_option(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.select.item(row["title"], value=row["id"])


def _new_booking_panel() -> rx.Component:
    """The range-select create panel: book a slot for a guest (reuses the booking service)."""
    return rx.cond(
        AdminState.show_new_booking,
        rx.card(
            rx.form(
                rx.vstack(
                    rx.heading("Nueva reserva", size="4"),
                    rx.select.root(
                        rx.select.trigger(placeholder="Tipo de evento"),
                        rx.select.content(rx.foreach(AdminState.event_types, _new_booking_option)),
                        name="event_type_id",
                        required=True,
                    ),
                    rx.input(
                        name="start",
                        default_value=AdminState.new_booking_start,
                        placeholder="Inicio (ISO, UTC)",
                        required=True,
                    ),
                    rx.input(name="guest_name", placeholder="Nombre del invitado", required=True),
                    rx.input(name="guest_email", placeholder="Email del invitado", required=True),
                    rx.input(name="guest_timezone", placeholder="Zona horaria (por defecto UTC)"),
                    rx.hstack(
                        rx.button("Crear reserva", type="submit"),
                        rx.button(
                            "Cancelar",
                            on_click=AdminState.close_new_booking,
                            variant="soft",
                            type="button",
                        ),
                        spacing="3",
                    ),
                    spacing="3",
                    width="100%",
                    max_width="24em",
                ),
                on_submit=AdminState.create_booking,
                reset_on_submit=True,
            ),
        ),
    )


def bookings_page() -> rx.Component:
    """The agenda: bookings on the AetherCal calendar (month/week/day/list), with drag-reschedule,
    range-select-to-create, and click-to-manage — the booking list is now the calendar's list view.
    """
    return _shell(
        "Agenda",
        _error(AdminState.error),
        _calendar(),
        _manage_panel(),
        _new_booking_panel(),
        rx.heading("Reprogramar manualmente", size="4"),
        rx.text(
            "Alternativa accesible al arrastre: reprograma por ID de reserva y nuevo inicio.",
            size="1",
            color_scheme="gray",
        ),
        rx.form(
            rx.hstack(
                rx.input(name="booking_id", placeholder="ID de reserva", required=True),
                rx.input(name="new_start", type="datetime-local", required=True),
                rx.button("Reprogramar", type="submit"),
                spacing="3",
                align="end",
            ),
            on_submit=AdminState.reschedule,
            reset_on_submit=True,
        ),
    )


# --------------------------------------------------------------------------------------
# The health panel (RF-25 / R9).
# --------------------------------------------------------------------------------------


def _metric_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["label"]),
        rx.table.cell(rx.text(row["value"], weight="bold")),
        rx.table.cell(rx.text(row["hint"], size="1", color_scheme="gray")),
    )


def health_page() -> rx.Component:
    """The health panel (RF-25 / R9): the outbox backlog and the no-show rate of THIS business.

    ==This is what makes a dead scheduler visible.== If the drain process dies, every booking still
    confirms, every intent is still queued, and no guest ever hears from the system again — in
    silence. The number to watch is not "pending" (a reminder for a booking three weeks out is
    pending and perfectly healthy) but how long the OLDEST DUE message has been waiting.

    The numbers are tenant-scoped. The operator's instance-wide view is ``GET /metrics``, which
    carries its own token precisely so that a business can never read the volume of its neighbours.
    """
    return _shell(
        "Health",
        _error(AdminState.error),
        rx.text(
            "Si los mensajes vencidos crecen y el más antiguo lleva horas esperando, nada está "
            "despachando la cola: las reservas se siguen confirmando y los avisos dejan de salir, "
            "sin ningún error.",
            size="1",
            color_scheme="gray",
        ),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Indicador"),
                    rx.table.column_header_cell("Valor"),
                    rx.table.column_header_cell("Qué significa"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.metrics, _metric_row)),
            width="100%",
        ),
    )


# --------------------------------------------------------------------------------------
# Hosts, the host selector, and where a host's bookings are written (RF-30).
# --------------------------------------------------------------------------------------


def _host_option(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.select.item(row["name"], value=row["id"])


def _host_select(*, required: bool) -> rx.Component:
    """The host an event type belongs to. ==The field whose absence WAS the RF-30 defect.==

    With no host field, the service took the tenant's first user — so a business's second host could
    never be given an event type, and nothing ever said so. It is required: there is no sensible
    default, and a default is precisely what went wrong.
    """
    return rx.select.root(
        rx.select.trigger(placeholder="Host"),
        rx.select.content(rx.foreach(AdminState.hosts, _host_option)),
        name="host_id",
        required=required,
    )


def _owner_select() -> rx.Component:
    """Who owns a weekly pattern: one host, or the BUSINESS (every host may use it).

    ``(business)`` is an explicit option rather than the blank one. A schedule shared by two hosts
    on purpose and a schedule two hosts share because nobody was ever asked look identical in the
    database — the difference is only ever made here.
    """
    return rx.select.root(
        rx.select.trigger(placeholder="Owner (blank = the whole business)"),
        rx.select.content(
            rx.select.item(SHARED_SCHEDULE, value=SHARED_SCHEDULE),
            rx.foreach(AdminState.hosts, _host_option),
        ),
        name="owner_id",
    )


def _host_table_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["name"]),
        rx.table.cell(row["email"]),
        rx.table.cell(row["timezone"]),
        rx.table.cell(
            rx.button(
                "Calendars",
                on_click=AdminState.select_host(row["id"]),
                variant="soft",
                size="1",
            )
        ),
        rx.table.cell(
            rx.button(
                "Delete",
                on_click=AdminState.delete_host(row["id"]),
                color_scheme="red",
                variant="soft",
                size="1",
            )
        ),
        rx.table.cell(rx.code(row["id"])),
    )


def _connection_table_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["account"]),
        rx.table.cell(row["calendar"]),
        rx.table.cell(rx.code(row["id"])),
    )


def _connections_panel() -> rx.Component:
    """A host's connected calendar accounts, and the calendar each one writes bookings into.

    EVERY connection, not the first: the read path unions them all, and a connection the panel never
    showed is a calendar whose busy times nobody could designate — an ignored busy set is a
    double-booking waiting to happen.
    """
    return rx.cond(
        AdminState.selected_host_id != "",
        rx.card(
            rx.vstack(
                rx.heading("Connected calendars", size="4"),
                rx.text(
                    "Send this host's bookings to a DEDICATED calendar — never the primary "
                    "calendar "
                    "of a personal account. One target per host: designating a new one retires the "
                    "old.",
                    size="1",
                    color_scheme="gray",
                ),
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Account"),
                            rx.table.column_header_cell("Bookings are written to"),
                            rx.table.column_header_cell("Connection id"),
                        )
                    ),
                    rx.table.body(rx.foreach(AdminState.connections, _connection_table_row)),
                    width="100%",
                ),
                rx.form(
                    rx.vstack(
                        rx.input(name="connection_id", placeholder="Connection id", required=True),
                        rx.input(
                            name="calendar_id",
                            placeholder="Calendar id (e.g. bookings@group.calendar.google.com)",
                            required=True,
                        ),
                        rx.button("Designate", type="submit"),
                        spacing="3",
                        width="100%",
                        max_width="28em",
                    ),
                    on_submit=AdminState.designate_calendar,
                    reset_on_submit=True,
                ),
                spacing="3",
                width="100%",
            ),
        ),
    )


def hosts_page() -> rx.Component:
    """Hosts (RF-30): who takes the bookings, and where their calendar events are written."""
    return _shell(
        "Hosts",
        _error(AdminState.error),
        rx.text(
            "Every event type belongs to one host, and their availability is what its slots are "
            "computed from. A host who still hosts an event type, or owns a schedule, cannot be "
            "deleted.",
            size="1",
            color_scheme="gray",
        ),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Name"),
                    rx.table.column_header_cell("Email"),
                    rx.table.column_header_cell("Timezone"),
                    rx.table.column_header_cell("Calendars"),
                    rx.table.column_header_cell("Actions"),
                    rx.table.column_header_cell("Id"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.hosts, _host_table_row)),
            width="100%",
        ),
        _connections_panel(),
        rx.heading("Add a host", size="4"),
        rx.form(
            rx.vstack(
                rx.input(name="name", placeholder="Name", required=True),
                rx.input(name="email", placeholder="Email", required=True),
                rx.input(name="timezone", placeholder="IANA timezone (default UTC)"),
                rx.button("Add", type="submit"),
                spacing="3",
                width="100%",
                max_width="24em",
            ),
            on_submit=AdminState.create_host,
            reset_on_submit=True,
        ),
        rx.heading("Update a host", size="4"),
        rx.form(
            rx.vstack(
                rx.input(name="id", placeholder="Host id", required=True),
                rx.input(name="name", placeholder="Name", required=True),
                rx.input(name="email", placeholder="Email", required=True),
                rx.input(name="timezone", placeholder="IANA timezone (default UTC)"),
                rx.button("Update", type="submit"),
                spacing="3",
                width="100%",
                max_width="24em",
            ),
            on_submit=AdminState.update_host,
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
        rx.table.cell(row["title_en"]),
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
    """Event-type CRUD: a table, a create form, and a rename/re-duration form.

    ``title_en``/``description_en`` (below the canonical Spanish ``title``) are the sole EN
    override the platform supports today (A4) — blank leaves no translation on create, and leaves
    the stored one untouched on update; the table's "Title (EN)" column is how a saved/edited
    override is surfaced back to the operator.
    """
    return _shell(
        "Event types",
        _error(AdminState.error),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Slug"),
                    rx.table.column_header_cell("Title"),
                    rx.table.column_header_cell("Title (EN)"),
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
                # RF-30. The form had NO host field, and the service filled the gap with the
                # tenant's first user — so a business's second host could never be given an event
                # type, silently. The choice is the operator's, and it is required.
                _host_select(required=True),
                rx.input(name="slug", placeholder="slug (e.g. intro-call)", required=True),
                rx.input(name="title", placeholder="Title", required=True),
                rx.input(name="title_en", placeholder="Title (English, optional)"),
                rx.input(name="description_en", placeholder="Description (English, optional)"),
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
            rx.vstack(
                rx.input(name="id", placeholder="Event type id", required=True),
                rx.input(name="title", placeholder="New title (optional)"),
                rx.input(name="duration_min", type="number", placeholder="New minutes (optional)"),
                # EN translations: a blank field PRESERVES the saved override (no silent data loss);
                # tick the matching clear checkbox to REMOVE it explicitly.
                rx.input(name="title_en", placeholder="New title EN (blank keeps current)"),
                rx.checkbox("Clear title EN translation", name="clear_title_en"),
                rx.input(
                    name="description_en", placeholder="New description EN (blank keeps current)"
                ),
                rx.checkbox("Clear description EN translation", name="clear_description_en"),
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
        # RF-30: whose pattern this is. "(business)" means every host may use it — a decision, and
        # not the same thing as two hosts sharing a schedule because nobody was ever asked.
        rx.table.cell(row["owner"]),
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
                    rx.table.column_header_cell("Owner"),
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
                _owner_select(),
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
        rx.heading("Update a schedule", size="4"),
        rx.text(
            "Un campo en blanco se deja como está. El dueño solo cambia si eliges uno: "
            f"'{SHARED_SCHEDULE}' devuelve el horario a todo el negocio.",
            size="1",
            color_scheme="gray",
        ),
        rx.form(
            rx.hstack(
                rx.input(name="id", placeholder="Schedule id", required=True),
                rx.input(name="name", placeholder="New name (optional)"),
                rx.input(name="timezone", placeholder="New timezone (optional)"),
                # RF-30. The column shipped and this form never exposed it, so a schedule created
                # for one host could not be transferred and a shared one could not be assigned: the
                # database had the field and nobody could move it.
                _owner_select(),
                rx.button("Update", type="submit"),
                spacing="3",
                align="end",
            ),
            on_submit=AdminState.update_schedule,
            reset_on_submit=True,
        ),
    )


# --------------------------------------------------------------------------------------
# Notification rules + templates (RF-24).
# --------------------------------------------------------------------------------------


def _workflow_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    """One rule. The toggle is TWO explicit buttons chosen by the row's stored state, never one
    button carrying "the opposite of whatever the row says" — that is a guess about state the row
    may already hold stale, and this toggle pauses (or releases) real messages to real guests."""
    return rx.table.row(
        rx.table.cell(row["name"]),
        rx.table.cell(row["trigger"]),
        rx.table.cell(row["offset_min"]),
        rx.table.cell(row["scope"]),
        rx.table.cell(row["steps"]),
        rx.table.cell(row["active"]),
        rx.table.cell(
            rx.cond(
                row["active"] == "yes",
                rx.button(
                    "Switch off",
                    on_click=AdminState.deactivate_workflow(row["id"]),
                    color_scheme="red",
                    variant="soft",
                    size="1",
                ),
                rx.button(
                    "Switch on",
                    on_click=AdminState.activate_workflow(row["id"]),
                    variant="soft",
                    size="1",
                ),
            )
        ),
        rx.table.cell(rx.code(row["id"])),
    )


def _template_row(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["channel"]),
        rx.table.cell(row["kind"]),
        rx.table.cell(row["locale"]),
        rx.table.cell(row["subject"]),
        rx.table.cell(row["body"]),
        rx.table.cell(
            rx.button(
                "Delete",
                on_click=AdminState.delete_template(row["id"]),
                color_scheme="red",
                variant="soft",
                size="1",
            )
        ),
        rx.table.cell(rx.code(row["id"])),
    )


def _named_option(name: str) -> rx.Component:
    return rx.select.item(name, value=name)


def _scope_option(row: ObjectVar[dict[str, str]]) -> rx.Component:
    return rx.select.item(row["title"], value=row["id"])


def _scope_select() -> rx.Component:
    """The event type a rule governs. ``(all)`` is an EXPLICIT option, not the empty one: on the
    update form a blank means "leave the scope alone", so widening a rule to EVERY event type has to
    be something the operator says — never something a field they never touched does for them."""
    return rx.select.root(
        rx.select.trigger(placeholder="Event type (blank = unchanged)"),
        rx.select.content(
            rx.select.item(ALL_EVENT_TYPES, value=ALL_EVENT_TYPES),
            rx.foreach(AdminState.event_types, _scope_option),
        ),
        name="event_type_id",
    )


def _step_fields() -> rx.Component:
    """One ``kind`` per channel — which IS the schema's "one step per channel" rule. Two steps on
    one channel are two dedupe keys, so the guest would get the same message twice."""
    return rx.vstack(
        rx.text(
            "Steps — one content kind per channel; leave a channel blank to send nothing on it.",
            size="1",
            color_scheme="gray",
        ),
        rx.input(name="email_kind", placeholder="email kind (e.g. reminder)"),
        rx.input(name="whatsapp_kind", placeholder="whatsapp kind (needs a template)"),
        rx.input(name="sms_kind", placeholder="sms kind (needs a template)"),
        spacing="2",
        width="100%",
    )


def workflows_page() -> rx.Component:
    """Rule + template CRUD (RF-24).

    Editing a rule here does not merely rewrite a row: the service reconciles the queued steps of
    every booking that rule already governs, so "remind 24 h before" changed to "1 h before" moves
    the reminder of the guest who is ALREADY in the diary. That is the whole reason this screen
    exists rather than a thin form over a table.
    """
    return _shell(
        "Notification rules",
        _error(AdminState.error),
        rx.text(
            "Editing a rule also re-times the messages already queued for the bookings it "
            "governs — "
            "including the ones already in the diary.",
            size="1",
            color_scheme="gray",
        ),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Name"),
                    rx.table.column_header_cell("Trigger"),
                    rx.table.column_header_cell("Offset (min)"),
                    rx.table.column_header_cell("Event type"),
                    rx.table.column_header_cell("Steps"),
                    rx.table.column_header_cell("Active"),
                    rx.table.column_header_cell("Actions"),
                    rx.table.column_header_cell("Id"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.workflows, _workflow_row)),
            width="100%",
        ),
        rx.heading("Create a rule", size="4"),
        rx.form(
            rx.vstack(
                rx.input(name="name", placeholder="Name (e.g. 24h reminder)", required=True),
                rx.select.root(
                    rx.select.trigger(placeholder="Trigger"),
                    rx.select.content(*[_named_option(name) for name in WORKFLOW_TRIGGER_NAMES]),
                    name="trigger",
                    required=True,
                ),
                rx.input(
                    name="offset_min",
                    type="number",
                    placeholder="Offset in minutes (-1440 = 24 h before the start)",
                ),
                _scope_select(),
                _step_fields(),
                rx.button("Create", type="submit"),
                spacing="3",
                width="100%",
                max_width="28em",
            ),
            on_submit=AdminState.create_workflow,
            reset_on_submit=True,
        ),
        rx.heading("Update a rule", size="4"),
        rx.text(
            "A blank field is left unchanged. Filling in ANY step kind REPLACES the rule's whole "
            "step list.",
            size="1",
            color_scheme="gray",
        ),
        rx.form(
            rx.vstack(
                rx.input(name="id", placeholder="Rule id", required=True),
                rx.input(name="name", placeholder="New name (optional)"),
                rx.select.root(
                    rx.select.trigger(placeholder="New trigger (optional)"),
                    rx.select.content(*[_named_option(name) for name in WORKFLOW_TRIGGER_NAMES]),
                    name="trigger",
                ),
                rx.input(name="offset_min", type="number", placeholder="New offset (optional)"),
                _scope_select(),
                _step_fields(),
                rx.button("Update", type="submit"),
                spacing="3",
                width="100%",
                max_width="28em",
            ),
            on_submit=AdminState.update_workflow,
            reset_on_submit=True,
        ),
        rx.heading("Message templates", size="4"),
        rx.text(
            "A step with no template is skipped at send time and the guest is never messaged "
            "— so a "
            "rule cannot be saved until the body its steps render exists. Only these variables are "
            f"substituted: {', '.join('{{' + name + '}}' for name in sorted(TEMPLATE_VARIABLES))}.",
            size="1",
            color_scheme="gray",
        ),
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Channel"),
                    rx.table.column_header_cell("Kind"),
                    rx.table.column_header_cell("Locale"),
                    rx.table.column_header_cell("Subject"),
                    rx.table.column_header_cell("Body"),
                    rx.table.column_header_cell("Actions"),
                    rx.table.column_header_cell("Id"),
                )
            ),
            rx.table.body(rx.foreach(AdminState.templates, _template_row)),
            width="100%",
        ),
        rx.heading("Write a template", size="4"),
        rx.form(
            rx.vstack(
                rx.select.root(
                    rx.select.trigger(placeholder="Channel"),
                    rx.select.content(*[_named_option(name) for name in CHANNEL_NAMES]),
                    name="channel",
                    required=True,
                ),
                rx.input(name="kind", placeholder="Kind (e.g. reminder)", required=True),
                rx.input(name="locale", placeholder="Locale (es / en)", required=True),
                rx.input(name="subject", placeholder="Subject (email only; blank for phone)"),
                rx.text_area(name="body", placeholder="Body", required=True),
                rx.button("Save template", type="submit"),
                spacing="3",
                width="100%",
                max_width="28em",
            ),
            on_submit=AdminState.create_template,
            reset_on_submit=True,
        ),
        rx.heading("Edit a template's text", size="4"),
        rx.text(
            "Its channel / kind / locale are immutable: re-pointing a template would silently "
            "change what every step resolving through it sends.",
            size="1",
            color_scheme="gray",
        ),
        rx.form(
            rx.vstack(
                rx.input(name="id", placeholder="Template id", required=True),
                rx.input(name="subject", placeholder="New subject (blank keeps current)"),
                rx.text_area(name="body", placeholder="New body (blank keeps current)"),
                rx.button("Update template", type="submit"),
                spacing="3",
                width="100%",
                max_width="28em",
            ),
            on_submit=AdminState.update_template,
            reset_on_submit=True,
        ),
    )


__all__ = [
    "bookings_page",
    "event_types_page",
    "health_page",
    "hosts_page",
    "login_page",
    "schedules_page",
    "workflows_page",
]
