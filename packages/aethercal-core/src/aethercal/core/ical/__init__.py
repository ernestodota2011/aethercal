"""iCalendar (.ics) import/export for AetherCal (RF-05): structural round-trip.

Pure (zero network/disk I/O) conversion between an :class:`aethercal.core.model.Event`
and its RFC 5545 iCalendar representation, built on the ``icalendar`` library.

Round-trip invariant
---------------------
``event_from_ics(event_to_ics(ev)) == ev`` holds for every supported ``Event``
(structural, field-for-field Pydantic value equality). Recurrence *expansion*
equivalence is out of scope (that is the recurrence engine's concern).

Precision / normalization boundaries (inherent to RFC 5545, not workarounds):

* DATE-TIME has whole-second precision, so a sub-second ``microsecond`` on
  ``dtstart`` / ``exdates`` / ``rdates`` and a sub-second ``duration`` are not
  representable and do not round-trip. The model permits sub-second values;
  callers needing exact .ics equality must use whole-second values.
* ``RRULE`` is round-tripped through icalendar, which emits the rule parts in
  RFC 5545 canonical order. An rrule already in canonical order round-trips
  byte-for-byte; a semantically identical rrule written in a different part
  order is re-emitted in canonical order (same meaning, different text).
* ``timezone`` round-trips as its IANA key. ``"UTC"`` is emitted in the ``Z``
  form; aliases such as ``"Etc/UTC"`` canonicalize to ``"UTC"`` on the way back.
"""

from aethercal.core.ical.serde import PRODID, event_from_ics, event_to_ics

__all__ = ["PRODID", "event_from_ics", "event_to_ics"]
