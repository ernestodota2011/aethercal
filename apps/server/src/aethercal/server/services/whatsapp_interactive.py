"""Bidirectional interactive WhatsApp processing via Evolution API (Horizon 1).

Handles incoming guest responses:
- "1" / "si" / "confirmo" -> Confirms attendance for the upcoming appointment, stamping
  ``bookings.attendance_confirmed_at`` (a first-wins, idempotent seal).
- "2" / "no" / "cancelar" -> Cancels the booking in-transaction via `cancel_booking`.
- "STOP" / "BAJA" / "ALTO" -> Registers instance-level phone suppression (D-12).

Only UPCOMING bookings are eligible for confirm/cancel: the reminder goes out before the
appointment, so a reply that arrives after it has no live appointment to act on and is answered
as ``no_booking_found`` — a late or replayed reply must never cancel a visit that already
happened.

All transitions and notifications obey RLS, multi-tenant isolation, and the transactional outbox.

.. rubric:: The reply lexicon, and why OPT_OUT is checked FIRST

``parse_reply_action`` is the only place where a guest's free-form text becomes a state change,
and the state changes are not symmetric. Confirming attendance is reversible; cancelling is
recoverable; ==an opt-out suppression is neither== — it writes an instance-level tombstone (D-12)
and every future message to that number is refused. So the rule order is a safety property, not a
style choice: opt-out is settled before any confirm/cancel rule can claim the text, and the opt-out
lexicon only fires on phrases that mean "stop messaging me" (a bare "no" is a cancellation, never a
suppression).

The rest of the lexicon is calibrated against the benchmark corpus
(``simulation/datasets/whatsapp_intents_benchmark.jsonl``: 625 samples — numeric selections,
Latin-American dialects, emojis, typos, regulatory opt-outs and adversarial injections) through
``simulation/whatsapp_intent_loop.py``. Typos are tolerated with a bounded edit distance instead of
an ever-growing keyword list, and the adversarial samples pin the other direction: an unparseable
injection must stay ``UNKNOWN`` rather than match a selection.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import Booking
from aethercal.server.services.bookings import BookingEffects, cancel_booking
from aethercal.server.services.phone_verification import normalize_e164, suppress_phone

_logger = logging.getLogger(__name__)


class WhatsAppReplyAction(StrEnum):
    """Normalized intent parsed from an inbound WhatsApp message."""

    CONFIRM_ATTENDANCE = "confirm_attendance"
    CANCEL = "cancel"
    OPT_OUT = "opt_out"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WhatsAppProcessResult:
    """The result of processing an inbound WhatsApp interaction."""

    action: WhatsAppReplyAction
    status: str
    booking_id: uuid.UUID | None
    reply_message: str | None


def _normalize_text(text: str) -> str:
    """Normalize text: strip accents/marks, lower-case, collapse whitespace.

    Marks and format characters (accents, emoji variation selectors ``U+FE0E/U+FE0F``, the
    zero-width joiner, the enclosing keycap ``U+20E3``) are removed so that ``1️⃣``, ``SÍ`` and
    ``sí`` all read as the same plain text. Emojis themselves survive normalization: they are
    matched separately, on the raw text, by :func:`_emoji_action`.
    """
    stripped = "".join(
        char for char in text.strip() if unicodedata.category(char) not in {"Mn", "Me", "Cf"}
    )
    nfkd = unicodedata.normalize("NFKD", stripped.lower())
    without_accents = "".join(char for char in nfkd if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip()


# --------------------------------------------------------------------------------------
# Lexicons. Every entry is here because a real message (the benchmark corpus) needed it,
# and each set is deliberately narrow: a false OPT_OUT suppresses a guest forever.
# --------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")

#: A bare mention of any of these words IS an opt-out request. They have no other meaning a
#: guest would put in a reply to an appointment reminder.
_OPT_OUT_WORDS = frozenset(
    {"stop", "baja", "alto", "desuscribir", "unsubscribe", "parar", "detener", "quitar", "bloquear"}
)

#: Multi-word opt-out requests. Substrings, because the request is a sentence ("no me envíen
#: más mensajes", "eliminar de la lista") where word-order filler varies.
_OPT_OUT_PHRASES = (
    "no me env",
    "no me escrib",
    "no me mand",
    "no quiero recibir",
    "no autorizo",
    "no enviar",
    "eliminar de la lista",
    "remover de la lista",
    "remover suscrip",
    "cancelar suscrip",
    "cancelar las alertas",
    "cancelar avisos",
    "dejen de molestar",
)

#: Token prefixes that name a deletion request ("borren mi número", "desuscribirme de este canal").
_OPT_OUT_PREFIXES = ("borr", "desuscrib")

#: A leading option selection: ``1``/``uno`` confirms, ``2``/``dos`` cancels. The lookahead is
#: what keeps ``123456`` and the injection ``1; EXEC ...`` out: after the digit only a real
#: separator (or end of text) is accepted, never another digit or a command separator.
_SELECTION_RE = re.compile(
    r"^(?:(?:el|la|opcion|numero|seleccion)\s+)?[#(\[/]?\s*(1|2|uno|dos)(?=$|\s|[)\].!*:,\-])"
)
_SELECTION_CONFIRM = frozenset({"1", "uno"})

#: Confirm words that mean yes ANYWHERE in the message ("claro que sí", "listo parce").
_CONFIRM_WORDS = frozenset(
    {
        "sip",
        "yes",
        "afirmativo",
        "correcto",
        "exacto",
        "confirmo",
        "confirmar",
        "confirm",
        "confirmado",
        "confirmada",
        "confirmadisimo",
        "asistire",
        "asistir",
        "asistencia",
        "dale",
        "listo",
        "joya",
        "obvio",
        "claro",
        "posta",
        "sipo",
        "bacan",
        "filete",
        "firme",
        "simona",
        "simon",
        "tranquilo",
        "cachai",
        "hagale",
    }
)

#: Confirm words that only count as the FIRST token. "si" mid-sentence is the Spanish
#: conjunction ("no sé si alcance a volver"), not an answer; "seguro" mid-sentence appears in
#: queries ("¿atienden por seguro médico?"); "puesto" ("puesto para mañana") is only an answer
#: when it leads.
_CONFIRM_LEADING_WORDS = frozenset({"si", "seguro", "puesto"})

#: Cancel words that only count as the FIRST token: "no" as a whole reply declines the
#: appointment, but "no" mid-sentence is just negation ("seguro, no falto" is a YES).
_CANCEL_LEADING_WORDS = frozenset({"no", "nop", "noup", "negativo"})

#: Confirm phrases whose meaning is carried by the expression, not by a single token.
_CONFIRM_PHRASES = (
    "de una",
    "de diez",
    "de mas que",
    "nos vemos",
    "para alla",
    "ahi estare",
    "ahi estoy",
    "ahi llego",
    "ahi nos",
    "alla estare",
    "alla estoy",
    "alla llego",
    "alla caigo",
    "alla nos",
    "alla voy",
    "sin falta",
    "no falto",
    "a huevo",
    "al chile",
    "cuenten conmigo",
    "con mi presencia",
    "dalo por hecho",
    "va que va",
    "sale y vale",
    "bien pueda",
    "por supuesto",
    "de acuerdo",
)

#: Cancellation idioms that do not name the verb ("qué pena", "pucha", "nel").
_CANCEL_PHRASES = (
    "no puedo",
    "no podre",
    "no voy",
    "no ire",
    "no asist",
    "no lleg",
    "no alcanz",
    "no cuenten",
    "no estar",
    "no me es posible",
    "no me da el tiempo",
    "no me resulta",
    "no se va a armar",
    "imposible",
    "que pena",
    "se me ",
    "me toca cancelar",
    "tengo que cancelar",
    "me salio un",
    "nel",
    "ni a palos",
    "ni hablar",
    "no va pa esa",
    "mala mia",
    "pucha",
    "pailas",
    "para nada",
)

#: A move request ("cambiar de fecha", "mover la cita") is handled as a reschedule, which the
#: product treats as a cancellation plus instructions: the decision is one ``<verb> <object>``
#: pair, not two growing phrase lists.
_RESCHEDULE_VERBS = frozenset({"cambiar", "cambio", "mover", "muevo", "reprogramar", "reagendar"})
_RESCHEDULE_OBJECTS = frozenset({"fecha", "hora", "horario", "dia", "turno", "cita", "reserva"})

#: A request for a different date carries the reschedule with no verb at all. "otro dia" is
#: deliberately absent: "el otro día me caí" is a narrative, not a request.
_RESCHEDULE_PHRASES = ("otra fecha", "otra hora", "otro horario")

#: Verb stems that cancel when NEGATED ("no puedo", "no iré", "no llegaré"). They are checked
#: as a ``no <verb>`` pair so that "bien pueda" never reads as a cancellation.
_NEGATED_VERBS = frozenset(
    {
        "puedo",
        "podre",
        "voy",
        "ire",
        "asistire",
        "asistir",
        "llegare",
        "llego",
        "alcanzare",
        "alcanzo",
        "estare",
    }
)

#: Short typo forms that no edit distance reaches; documented, and nothing else maps here.
_TYPO_ALIASES = {"pdo": "puedo", "peudo": "puedo"}

#: Vocabulary for typo tolerance on confirmation.
_CONFIRM_TYPO_TARGETS = frozenset(
    {
        "confirmo",
        "confirmar",
        "confirm",
        "confirmado",
        "confirmada",
        "asistire",
        "asistir",
        "asistencia",
    }
)

#: Vocabulary for typo tolerance on cancellation (the verb may be misspelled past recognition
#: of a prefix, e.g. "cnacelar").
_CANCEL_TYPO_TARGETS = frozenset(
    {
        "cancelar",
        "cancelacion",
        "reprogramar",
        "reprogramacion",
        "reagendar",
        "posponer",
        "aplazar",
        "postergar",
        "anular",
    }
)

#: Emoji that answer the reminder on their own.
_CONFIRM_EMOJI = frozenset(
    {
        "\U0001f44d",  # thumbs up
        "\u2705",  # white heavy check mark
        "\U0001f44c",  # OK hand
        "\U0001f64c",  # raising hands
        "\U0001f919",  # call me hand
        "\u2714",  # heavy check mark
        "\U0001f91d",  # handshake
        "\U0001f44f",  # clapping hands
    }
)
_CANCEL_EMOJI = frozenset(
    {
        "\u274c",  # cross mark
        "\U0001f6ab",  # no entry
        "\U0001f645",  # person gesturing NO
        "\U0001f44e",  # thumbs down
        "\u26d4",  # no entry sign
        "\U0001f6d1",  # octagonal sign
    }
)
_SKIN_TONE_RE = re.compile("[\U0001f3fb-\U0001f3ff\ufe0e\ufe0f\u200d]")


def _words(normalized: str) -> tuple[str, ...]:
    """The plain word/number tokens of an already-normalized message."""
    return tuple(_WORD_RE.findall(normalized))


def _collapse_doubles(token: str) -> str:
    """``siii`` -> ``si``; ``canceloo`` -> ``cancelo``. Applied only after an exact miss."""
    return re.sub(r"(.)\1+", r"\1", token)


def _distance_within(source: str, target: str, limit: int) -> bool:
    """Bounded optimal-string-alignment distance (a swap costs one edit, as a typist does).

    Optimal string alignment, not full Damerau-Levenshtein: it is enough for single-word typos
    and it has no transposition cycle to reason about.
    """
    if abs(len(source) - len(target)) > limit:
        return False
    previous_previous: list[int] = []
    previous = list(range(len(target) + 1))
    for i, source_char in enumerate(source, start=1):
        current = [i] + [0] * len(target)
        for j, target_char in enumerate(target, start=1):
            cost = 0 if source_char == target_char else 1
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
            if i > 1 and j > 1 and source_char == target[j - 2] and source[i - 2] == target_char:
                current[j] = min(current[j], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current
    return previous[len(target)] <= limit


def _is_typo_of(token: str, vocabulary: frozenset[str], *, minimum_length: int = 4) -> bool:
    """Is ``token`` one or two edits away from a known word of the vocabulary?"""
    if len(token) < minimum_length:
        return False
    limit = 1 if len(token) < 7 else 2
    return any(_distance_within(token, word, limit) for word in vocabulary)


def _is_opt_out(normalized: str, tokens: tuple[str, ...]) -> bool:
    if any(token in _OPT_OUT_WORDS for token in tokens):
        return True
    if any(token.startswith(_OPT_OUT_PREFIXES) and len(token) >= 5 for token in tokens):
        return True
    return any(phrase in normalized for phrase in _OPT_OUT_PHRASES)


def _selection_action(normalized: str) -> WhatsAppReplyAction | None:
    match = _SELECTION_RE.match(normalized)
    if match is None:
        return None
    return (
        WhatsAppReplyAction.CONFIRM_ATTENDANCE
        if match.group(1) in _SELECTION_CONFIRM
        else WhatsAppReplyAction.CANCEL
    )


def _emoji_action(text: str) -> WhatsAppReplyAction | None:
    stripped = _SKIN_TONE_RE.sub("", text)
    if any(character in _CONFIRM_EMOJI for character in stripped):
        return WhatsAppReplyAction.CONFIRM_ATTENDANCE
    if any(character in _CANCEL_EMOJI for character in stripped):
        return WhatsAppReplyAction.CANCEL
    return None


def _names_a_reschedule(normalized: str, tokens: tuple[str, ...]) -> bool:
    """A request to move the appointment ("cambiar de fecha", "otra hora")."""
    if any(token in _RESCHEDULE_VERBS for token in tokens) and any(
        token in _RESCHEDULE_OBJECTS for token in tokens
    ):
        return True
    return any(phrase in normalized for phrase in _RESCHEDULE_PHRASES)


def _names_a_cancellation(tokens: tuple[str, ...]) -> bool:
    """A cancellation verb, a negated intent verb, or a typo of either."""
    for index, token in enumerate(tokens):
        if token.startswith(("cancel", "anul")):
            return True
        if index > 0 and tokens[index - 1] == "no":
            if _TYPO_ALIASES.get(token, token) in _NEGATED_VERBS:
                return True
            if _is_typo_of(token, _NEGATED_VERBS, minimum_length=3):
                return True
        if _is_typo_of(token, _CANCEL_TYPO_TARGETS):
            return True
    return False


def _is_cancel(normalized: str, tokens: tuple[str, ...]) -> bool:
    if tokens and tokens[0] in _CANCEL_LEADING_WORDS:
        return True
    if _names_a_reschedule(normalized, tokens):
        return True
    if _names_a_cancellation(tokens):
        return True
    return any(phrase in normalized for phrase in _CANCEL_PHRASES)


def _is_confirm(normalized: str, tokens: tuple[str, ...]) -> bool:
    if tokens and _collapse_doubles(tokens[0]) in _CONFIRM_LEADING_WORDS:
        return True
    for token in tokens:
        if token in _CONFIRM_WORDS:
            return True
        if _collapse_doubles(token) in _CONFIRM_WORDS:
            return True
        if _is_typo_of(token, _CONFIRM_TYPO_TARGETS):
            return True
    return any(phrase in normalized for phrase in _CONFIRM_PHRASES)


def parse_reply_action(text: str) -> WhatsAppReplyAction:
    """Parse raw incoming message text into a canonical `WhatsAppReplyAction`.

    The rule order is the safety property: see the module docstring. Opt-out is settled first
    and only fires on an explicit opt-out request, so no confirm/cancel message can ever
    suppress a guest by accident.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return WhatsAppReplyAction.UNKNOWN

    tokens = _words(normalized)
    selection = _selection_action(normalized)
    emoji = _emoji_action(text)

    action: WhatsAppReplyAction | None = None
    if _is_opt_out(normalized, tokens):
        action = WhatsAppReplyAction.OPT_OUT
    elif selection is not None:
        action = selection
    elif emoji is not None:
        action = emoji
    elif _is_cancel(normalized, tokens):
        action = WhatsAppReplyAction.CANCEL
    elif _is_confirm(normalized, tokens):
        action = WhatsAppReplyAction.CONFIRM_ATTENDANCE

    return action if action is not None else WhatsAppReplyAction.UNKNOWN


async def find_target_booking(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone_e164: str,
    now: datetime,
) -> Booking | None:
    """Find the closest UPCOMING confirmed booking for this guest phone and tenant.

    ==Only bookings that have not started are eligible, and that is a safety rule.== This used to
    fall back to the most recent PAST confirmed booking, so a late (or replayed) "2" could cancel an
    appointment that already happened — firing the whole cancellation chain, including the guest
    email, for a visit that is over — and a stale "1" could "confirm" one. The reminder that invites
    the reply goes out BEFORE the appointment, so a reply that arrives after the start has no live
    appointment to act on: the guest is told exactly that (``no_booking_found``), and nothing is
    written. An appointment already under way is excluded for the same reason: a cancellation must
    never fire on a visit that is happening.
    """
    return (
        await session.scalars(
            select(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.guest_phone == phone_e164,
                Booking.status == BookingStatus.CONFIRMED,
                Booking.start_at >= now,
            )
            .order_by(Booking.start_at.asc())
        )
    ).first()


async def process_inbound_whatsapp(  # noqa: PLR0913
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    sender_phone: str,
    message_text: str,
    suppression_key: str,
    now: datetime,
    effects: BookingEffects | None = None,
) -> WhatsAppProcessResult:
    """Process an inbound WhatsApp text interaction for a specific tenant business."""
    norm_phone = normalize_e164(sender_phone)
    action = parse_reply_action(message_text)

    if action == WhatsAppReplyAction.OPT_OUT:
        await suppress_phone(
            session,
            norm_phone,
            reason="Opt-out requested via inbound WhatsApp",
            suppression_key=suppression_key,
        )
        return WhatsAppProcessResult(
            action=action,
            status="suppressed",
            booking_id=None,
            reply_message=(
                "Has sido dado de baja de nuestros mensajes. Para reactivar, contáctanos."
            ),
        )

    if action == WhatsAppReplyAction.CONFIRM_ATTENDANCE:
        booking = await find_target_booking(
            session, tenant_id=tenant_id, phone_e164=norm_phone, now=now
        )
        if booking is None:
            return WhatsAppProcessResult(
                action=action,
                status="no_booking_found",
                booking_id=None,
                reply_message="No encontramos una cita activa asociada a este número.",
            )

        # Mark attendance confirmation — and PERSIST it. Returning "attendance_confirmed" while
        # writing nothing was the no-op this stamp closes: an operator managing no-shows had no way
        # to tell a guest who answered "1" from one who never replied.
        if booking.attendance_confirmed_at is None:
            booking.attendance_confirmed_at = now
            await session.flush()
        _logger.info(
            "Attendance confirmed via WhatsApp for booking %s (tenant %s) at %s",
            booking.id,
            tenant_id,
            booking.attendance_confirmed_at,
        )
        return WhatsAppProcessResult(
            action=action,
            status="attendance_confirmed",
            booking_id=booking.id,
            reply_message="¡Gracias! Hemos confirmado tu asistencia a la cita.",
        )

    if action == WhatsAppReplyAction.CANCEL:
        booking = await find_target_booking(
            session, tenant_id=tenant_id, phone_e164=norm_phone, now=now
        )
        if booking is None:
            return WhatsAppProcessResult(
                action=action,
                status="no_booking_found",
                booking_id=None,
                reply_message="No encontramos una cita activa asociada a este número.",
            )

        # Cancel the booking under lock & run full cancellation effect chain
        await cancel_booking(
            session,
            tenant_id=tenant_id,
            booking_id=booking.id,
            effects=effects,
            now=now,
        )
        _logger.info(
            "Booking %s cancelled via interactive WhatsApp (tenant %s)",
            booking.id,
            tenant_id,
        )
        return WhatsAppProcessResult(
            action=action,
            status="cancelled",
            booking_id=booking.id,
            reply_message="Tu cita ha sido cancelada con éxito.",
        )

    return WhatsAppProcessResult(
        action=action,
        status="ignored",
        booking_id=None,
        reply_message="Responde 1 para confirmar tu asistencia o 2 para cancelar tu cita.",
    )


def extract_evolution_payload(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Extract `(sender_phone, message_text)` from an Evolution API webhook payload.

    Returns `None` if the payload is not an inbound message or is sent by the host (`fromMe`).
    """
    event_type = payload.get("event")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    if event_type and event_type not in ("messages.upsert", "messages.update", "SEND_MESSAGE"):
        return None

    key = data.get("key", {})
    remote_jid = str(key.get("remoteJid", ""))
    if key.get("fromMe") is True or "@" not in remote_jid or "g.us" in remote_jid:
        return None

    raw_phone = remote_jid.split("@", 1)[0]

    msg = data.get("message", {})
    text = ""
    if isinstance(msg, dict):
        if "conversation" in msg:
            text = str(msg["conversation"])
        elif "extendedTextMessage" in msg and isinstance(msg["extendedTextMessage"], dict):
            text = str(msg["extendedTextMessage"].get("text", ""))
        elif "buttonsResponseMessage" in msg and isinstance(msg["buttonsResponseMessage"], dict):
            text = str(
                msg["buttonsResponseMessage"].get("selectedButtonId")
                or msg["buttonsResponseMessage"].get("selectedDisplayText", "")
            )
        elif "listResponseMessage" in msg and isinstance(msg["listResponseMessage"], dict):
            text = str(
                msg["listResponseMessage"].get("singleSelectReply", {}).get("selectedRowId", "")
            )

    text = text.strip()
    if not text:
        return None

    return raw_phone, text
