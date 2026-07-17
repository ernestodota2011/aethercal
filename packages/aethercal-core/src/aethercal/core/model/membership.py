"""Membership: what a person IS to a business — the vocabulary shared by the DB and the services."""

from __future__ import annotations

from enum import StrEnum


class MemberRole(StrEnum):
    """A person's role inside ONE business. Three, and deliberately only three.

    * ``owner`` — everything, including the two things that hand the business itself over: who else
      is in it (``memberships``) and where its money goes (credentials, billing).
    * ``admin`` — runs the business day to day: hosts, event types, schedules, workflows, anybody's
      bookings. Not the members, not the billing.
    * ``member`` — reads the business, and runs the bookings they host. Nothing else.

    ==This vocabulary is NOT what isolates one business from another.== That is row-level security
    (migration 0008), and it compares ``tenant_id`` — no notion of a role at all. A ``member`` of
    Acme carries Acme's ``tenant_id`` on every row they touch, so every policy in the database
    says yes to them deleting Acme's hosts. ==RLS isolates BUSINESSES; this enum authorises
    PEOPLE.== Two different questions, answered in two different places (``server.services.rbac``).

    Stored as its string value — ``VARCHAR(16)`` plus a ``CHECK``, exactly like ``BookingStatus`` —
    so the database and the domain can never disagree about the set of roles that exists.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
