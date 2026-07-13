"""Bilingual copy and locale selection for the booking page (RNF-1: Spanish primary + English).

A dependency-free i18n layer: a flat ``{locale: {key: template}}`` catalog and a ``t`` helper that
formats a template with keyword arguments. Locale selection follows a small, explicit precedence —
an explicit ``?lang=`` query wins, then the browser's ``Accept-Language``, then Spanish. No gettext,
no runtime locale files: the catalog is data in this module so it is type-checked and testable.
"""

from __future__ import annotations

from typing import Literal, get_args

Locale = Literal["es", "en"]

DEFAULT_LOCALE: Locale = "es"
SUPPORTED_LOCALES: tuple[Locale, ...] = get_args(Locale)

# Flat catalog. Every key MUST exist in both locales (a test enforces parity). Templates use
# ``str.format`` fields, filled by ``t(locale, key, **kwargs)``.
MESSAGES: dict[Locale, dict[str, str]] = {
    "es": {
        "app_name": "AetherCal",
        "skip_to_content": "Saltar al contenido",
        "language": "Idioma",
        "spanish": "Español",
        "english": "English",
        "index_title": "Reserva una cita",
        "index_lead": "Elige el tipo de reunión que quieres agendar.",
        "index_empty": "Por ahora no hay tipos de reunión disponibles.",
        "duration_minutes": "{minutes} min",
        "book_cta": "Reservar",
        "timezone_label": "Zona horaria",
        "timezone_update": "Actualizar",
        "choose_time": "Elige un horario",
        "no_slots": "No hay horarios disponibles en estas fechas. Prueba con otra semana.",
        "availability_unavailable": (
            "La disponibilidad no está disponible en este momento. Vuelve a intentarlo en unos "
            "minutos."
        ),
        "prev_week": "Semana anterior",
        "next_week": "Semana siguiente",
        "back_to_times": "Volver a los horarios",
        "your_details": "Tus datos",
        "selected_time": "Horario elegido",
        "name_label": "Nombre completo",
        "email_label": "Correo electrónico",
        "notes_label": "Notas (opcional)",
        "phone_label": "Teléfono (opcional)",
        "phone_hint": "Incluye el código de país. Ejemplo: +1 305 413 1728.",
        "phone_consent_label": (
            "Acepto recibir recordatorios de esta cita por WhatsApp o SMS en este número."
        ),
        "phone_consent_hint": (
            "Puedes reservar sin dar tu teléfono. Solo te escribiremos si marcas esta casilla."
        ),
        "required_mark": "obligatorio",
        "confirm_booking": "Confirmar reserva",
        "error_name_required": "Escribe tu nombre.",
        "error_email_invalid": "Escribe un correo electrónico válido.",
        "error_phone_invalid": (
            "Escribe un teléfono válido con el código de país. Ejemplo: +1 305 413 1728."
        ),
        "error_phone_consent_without_number": (
            "Escribe tu teléfono o desmarca la casilla de recordatorios."
        ),
        "error_start_invalid": "Ese horario ya no es válido. Elige otro.",
        "error_question_required": "Este campo es obligatorio.",
        "error_question_email": "Escribe un correo electrónico válido.",
        "error_question_number": "Escribe un número válido.",
        "error_question_url": "Escribe un enlace válido (https://...).",
        "error_question_tel": "Escribe un teléfono válido.",
        "error_question_select": "Elige una de las opciones disponibles.",
        "error_form_has_issues": "Revisa los campos marcados.",
        "retry": "Reintentar",
        "confirmed_heading": "¡Listo! Tu cita de {title} está confirmada.",
        "confirmed_when": "Cuándo",
        "confirmed_email_note": "Te enviamos los detalles a {email}.",
        "confirmed_meeting_link": "Enlace de la reunión",
        "add_to_calendar_heading": "Agregar a tu calendario",
        "add_to_calendar_google": "Agregar a Google Calendar",
        "add_to_calendar_outlook": "Agregar a Outlook",
        "cancel_title": "Cancelar la cita",
        "cancel_prompt": "¿Seguro que quieres cancelar esta cita?",
        "cancel_confirm": "Sí, cancelar",
        "cancel_done": "Tu cita fue cancelada.",
        "reschedule_title": "Reprogramar la cita",
        "reschedule_prompt": "Elige un nuevo horario para tu cita.",
        "reschedule_done": "Tu cita fue reprogramada.",
        "reschedule_missing_context": (
            "Este enlace no es válido. Usa el enlace del correo de confirmación."
        ),
        "not_found_title": "No encontrado",
        "not_found_body": "No pudimos encontrar lo que buscas.",
        "error_slot_unavailable": "Ese horario ya no está disponible. Elige otro, por favor.",
        "error_link_invalid": "Este enlace expiró o no es válido. Solicita uno nuevo.",
        "error_not_active": "Esta cita ya no se puede modificar.",
        "error_generic": "Algo salió mal. Vuelve a intentarlo en unos minutos.",
        "error_rate_limited": "Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.",
        "honeypot_received_message": (
            "Gracias. Hemos recibido tu solicitud y te contactaremos pronto."
        ),
        "meta_description": "Reserva tu cita en línea de forma rápida y sencilla con AetherCal.",
        "footer_powered": "Con la tecnología de AetherCal",
    },
    "en": {
        "app_name": "AetherCal",
        "skip_to_content": "Skip to content",
        "language": "Language",
        "spanish": "Español",
        "english": "English",
        "index_title": "Book a meeting",
        "index_lead": "Choose the type of meeting you want to schedule.",
        "index_empty": "There are no meeting types available right now.",
        "duration_minutes": "{minutes} min",
        "book_cta": "Book",
        "timezone_label": "Time zone",
        "timezone_update": "Update",
        "choose_time": "Choose a time",
        "no_slots": "No times available in this range. Try another week.",
        "availability_unavailable": (
            "Availability is temporarily unavailable. Please try again in a few minutes."
        ),
        "prev_week": "Previous week",
        "next_week": "Next week",
        "back_to_times": "Back to times",
        "your_details": "Your details",
        "selected_time": "Selected time",
        "name_label": "Full name",
        "email_label": "Email",
        "notes_label": "Notes (optional)",
        "phone_label": "Phone (optional)",
        "phone_hint": "Include the country code. Example: +1 305 413 1728.",
        "phone_consent_label": (
            "I agree to receive reminders about this booking by WhatsApp or SMS at this number."
        ),
        "phone_consent_hint": (
            "You can book without giving your phone. We'll only message you if you tick this box."
        ),
        "required_mark": "required",
        "confirm_booking": "Confirm booking",
        "error_name_required": "Please enter your name.",
        "error_email_invalid": "Please enter a valid email address.",
        "error_phone_invalid": (
            "Please enter a valid phone number including the country code. "
            "Example: +1 305 413 1728."
        ),
        "error_phone_consent_without_number": (
            "Please enter your phone number, or untick the reminders box."
        ),
        "error_start_invalid": "That time is no longer valid. Please pick another.",
        "error_question_required": "This field is required.",
        "error_question_email": "Please enter a valid email address.",
        "error_question_number": "Please enter a valid number.",
        "error_question_url": "Please enter a valid link (https://...).",
        "error_question_tel": "Please enter a valid phone number.",
        "error_question_select": "Please choose one of the available options.",
        "error_form_has_issues": "Please review the highlighted fields.",
        "retry": "Try again",
        "confirmed_heading": "You're all set! Your {title} booking is confirmed.",
        "confirmed_when": "When",
        "confirmed_email_note": "We've sent the details to {email}.",
        "confirmed_meeting_link": "Meeting link",
        "add_to_calendar_heading": "Add to your calendar",
        "add_to_calendar_google": "Add to Google Calendar",
        "add_to_calendar_outlook": "Add to Outlook",
        "cancel_title": "Cancel booking",
        "cancel_prompt": "Are you sure you want to cancel this booking?",
        "cancel_confirm": "Yes, cancel",
        "cancel_done": "Your booking has been cancelled.",
        "reschedule_title": "Reschedule booking",
        "reschedule_prompt": "Pick a new time for your booking.",
        "reschedule_done": "Your booking has been rescheduled.",
        "reschedule_missing_context": (
            "This link isn't valid. Please use the link from your confirmation email."
        ),
        "not_found_title": "Not found",
        "not_found_body": "We couldn't find what you're looking for.",
        "error_slot_unavailable": "That time is no longer available. Please pick another.",
        "error_link_invalid": "This link has expired or is invalid. Please request a new one.",
        "error_not_active": "This booking can no longer be changed.",
        "error_generic": "Something went wrong. Please try again in a few minutes.",
        "error_rate_limited": "Too many requests. Please wait a moment and try again.",
        "honeypot_received_message": (
            "Thank you. We've received your request and will be in touch soon."
        ),
        "meta_description": "Book your appointment online, quickly and easily, with AetherCal.",
        "footer_powered": "Powered by AetherCal",
    },
}


def normalize_locale(value: str | None) -> Locale | None:
    """Return the supported :data:`Locale` for ``value`` (case-insensitive), or ``None``."""
    if value is None:
        return None
    candidate = value.strip().lower()
    for locale in SUPPORTED_LOCALES:
        if candidate == locale:
            return locale
    return None


def _from_accept_language(header: str | None) -> Locale | None:
    """Pick the first supported locale named in an ``Accept-Language`` header, else ``None``."""
    if not header:
        return None
    for part in header.split(","):
        tag = part.split(";", 1)[0].strip().lower()
        primary = tag.split("-", 1)[0]  # "en-US" -> "en"
        match = normalize_locale(primary)
        if match is not None:
            return match
    return None


def select_locale(
    *,
    query_lang: str | None,
    accept_language: str | None,
    default: Locale = DEFAULT_LOCALE,
) -> Locale:
    """Resolve the request locale: ``?lang=`` wins, then ``Accept-Language``, then the default."""
    return normalize_locale(query_lang) or _from_accept_language(accept_language) or default


def t(locale: Locale, key: str, /, **kwargs: object) -> str:
    """Look up ``key`` for ``locale`` and format it with ``kwargs`` (falls back to Spanish)."""
    template = MESSAGES[locale].get(key) or MESSAGES[DEFAULT_LOCALE].get(key, key)
    return template.format(**kwargs) if kwargs else template


__all__ = [
    "DEFAULT_LOCALE",
    "MESSAGES",
    "SUPPORTED_LOCALES",
    "Locale",
    "normalize_locale",
    "select_locale",
    "t",
]
