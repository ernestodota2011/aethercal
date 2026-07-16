"""==No test may reach the real network.== A repo-wide, fail-closed pytest plugin.

The sibling of the db gate in ``conftest.py``, aimed at the other way a suite can pass while proving
nothing — or worse, while spending real money. Registered from the root ``conftest.py`` via
``pytest_plugins``, so it applies to every test in the tree and a test can import
:class:`RealNetworkForbiddenError` by a name that does not collide with the several ``conftest``
modules this repo has.
"""

from __future__ import annotations

from typing import NoReturn

import httpx
import pytest
import respx.mocks

respx.mocks.DEFAULT_MOCKER = "httpx"
"""==Tell ``respx`` to mock one storey UP, so it and the guard are not standing on the same step.==

``respx`` mocks by default at ``httpcore`` — *below* ``httpx``'s transport, which is where the guard
must live. Two libraries patching adjacent rungs of the same ladder is a coin-flip decided by import
order: the first attempt at this guard answered before respx's mocks and turned 58 green tests red.

``"httpx"`` is a first-class, documented respx mode: it patches ``AsyncClient._transport_for_url``
and hands back its own mock transport. That is strictly ABOVE this guard, so a respx test never
reaches the door at all — while anything respx is NOT mocking still walks straight into it. Set
once, at import, before any test builds a client.
"""


class RealNetworkForbiddenError(RuntimeError):
    """A test tried to open a REAL outbound HTTP connection. ==It never gets to.==

    Raised in place of the socket, so the failure is a red test naming this module rather than a
    request leaving the machine.
    """


def _forbidden(*_args: object, **_kwargs: object) -> NoReturn:
    raise RealNetworkForbiddenError(
        "a test tried to make a REAL outbound HTTP request.\n"
        "\n"
        "Nothing under test is allowed to leave this machine. If you are seeing this, a fake did "
        "not take effect and the REAL provider adapter was reached — check that the test injected "
        "its stub where the code actually READS it (`httpx.MockTransport`, `respx`, or the "
        "`app.state` key the router looks up), not merely somewhere adjacent.\n"
        "\n"
        "This is not a lint. A payment adapter that reaches the real API during a test run charges "
        "whatever account the environment happens to hold credentials for.\n"
        "\n"
        "See pytest_network_guard.py."
    )


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """==Make reaching the real network IMPOSSIBLE, rather than remembering not to.==

    .. rubric:: The incident this exists for

    B-06 changed how the payment gateway is wired, and ``test_payments_checkout_pg`` kept setting
    its fake on the OLD ``app.state`` key. The fake therefore did nothing, the REAL
    ``StripeGateway`` stayed wired, and the suite **opened a TLS connection to api.stripe.com**. It
    came back 401 — because that machine happened to hold no Stripe key — and the test failed on the
    status code, so it read like an ordinary wiring break.

    ==The 401 was luck, and luck is not a control.== Run the same suite on a machine with LIVE keys
    exported (to debug something else, on a laptop, at any point in the next year) and the identical
    mistake bills a real person. The rule this product runs on is that a business's money moves only
    on that business's own account, by its own decision — and a test suite is not a decision.

    .. rubric:: Why it closes the door instead of asking callers to behave

    Every provider adapter in this codebase speaks HTTP through ``httpx`` — Stripe, Mercado Pago,
    Twilio, Evolution/WhatsApp, Turnstile, cal.com, the outbound webhook delivery. And whatever an
    ``httpx`` client is asked to fetch, it reaches the wire through exactly one of two transport
    classes. ==So the door is shut, rather than the callers enumerated.== There is no allow-list of
    hosts to keep current and no adapter that can be forgotten: a provider added tomorrow is covered
    on the day it is written, because it cannot get out either.

    ``raising=True`` is part of the guarantee. If ``httpx`` ever renames these methods this fixture
    fails LOUDLY at setup, instead of silently patching nothing and quietly re-opening the door —
    the same failure mode the db gate exists to prevent, one layer down.

    What still passes: ``httpx.MockTransport`` (a different class entirely — the stub answers and
    the real transport is never built), ``ASGITransport`` (in-process, no socket), and ``respx``,
    which this module reconfigures to mock above the transport rather than below it (see
    :data:`respx.mocks.DEFAULT_MOCKER` at the top). And the database, which does not go through
    ``httpx`` at all — so the ``-m db`` suite reaches its PostgreSQL untouched, wherever it lives.
    That asymmetry is deliberate: this closes the door that SPENDS, not the one that stores.
    """
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _forbidden, raising=True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _forbidden, raising=True)
