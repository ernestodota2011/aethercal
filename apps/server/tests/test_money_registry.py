"""The money registry's lock: ==every MONEY provider has a real adapter and a real gateway== (B-06).

The registry this guards replaced a hand-written dict that listed ``mercado_pago`` and handed it a
generic HMAC adapter which could never have verified a real Mercado Pago notification. The point of
these tests is that the same class of lie cannot be told again.

==The provider set is DERIVED, never typed out here.== Every test below re-computes the money
providers from :func:`~aethercal.server.services.tenant_credentials.credential_class` — the same
function the BYOK fallback rule reads off. A new payment processor added to ``CredentialProvider``
lands in these tests automatically and fails them until it has an adapter and a gateway. A test that
listed the providers by hand would just be a second copy of the photograph, going stale beside the
first.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

from aethercal.server.integrations import money, stripe
from aethercal.server.integrations.money import (
    NotAMoneyProviderError,
    build_webhook_adapters,
    gateway_for,
    gateway_method_for,
    implementation_fingerprint,
    module_fingerprint,
    money_providers,
    webhook_adapter_for,
)
from aethercal.server.integrations.stripe import StripeGateway
from aethercal.server.services.payment_webhooks import GenericHmacAdapter
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    GatewayOperation,
    credential_class,
)


def test_the_money_providers_are_derived_from_the_credential_class() -> None:
    """The registry and the BYOK fallback rule read the same source, by construction."""
    assert set(money_providers()) == {
        provider
        for provider in CredentialProvider
        if credential_class(provider) is CredentialClass.MONEY
    }
    # If this ever empties, every assertion below passes vacuously — so it is pinned.
    assert money_providers(), "a product that charges must have at least one money provider"


@pytest.mark.parametrize("provider", money_providers())
def test_every_money_provider_has_a_webhook_adapter(provider: CredentialProvider) -> None:
    """==The exhaustiveness lock.== A money provider without an adapter cannot be shipped: its
    webhooks would have no way to be verified, so a guest could pay and never be confirmed."""
    adapter = webhook_adapter_for(provider)
    assert adapter is not None
    assert hasattr(adapter, "verify_signature")
    assert hasattr(adapter, "parse")


@pytest.mark.parametrize("provider", money_providers())
def test_no_money_provider_is_served_by_the_generic_fake(provider: CredentialProvider) -> None:
    """==The regression that names the original defect.==

    ``GenericHmacAdapter`` is the test fake. It once sat in the registry under ``mercado_pago``,
    which advertised support for a provider whose real signature scheme (an ``x-signature`` manifest
    over ``data.id``/``x-request-id``/``ts``) it does not implement and whose notifications it would
    therefore have rejected outright. A fake standing in for a provider is not support; it is a
    registry entry that looks like support.
    """
    assert not isinstance(webhook_adapter_for(provider), GenericHmacAdapter)


@pytest.mark.parametrize("provider", money_providers())
def test_every_money_provider_has_a_gateway(provider: CredentialProvider) -> None:
    """A provider that can receive webhooks but cannot open a checkout or refund is half a
    provider — and the half that is missing is the one that gives a guest's money back."""
    gateway = gateway_for(provider)
    assert gateway is not None
    assert hasattr(gateway, "create_checkout_session")
    assert hasattr(gateway, "refund")


@pytest.mark.parametrize(
    "provider",
    [p for p in CredentialProvider if credential_class(p) is CredentialClass.INFRA],
)
def test_an_infra_provider_has_no_payment_adapter_or_gateway(provider: CredentialProvider) -> None:
    """==Raised, not returned as ``None``.== Asking for SMTP's payment gateway is a category error,
    and a ``None`` on the money path is read by the first hurried caller as "not configured, carry
    on" — the sentence the whole BYOK module exists to make unsayable."""
    with pytest.raises(NotAMoneyProviderError):
        webhook_adapter_for(provider)
    with pytest.raises(NotAMoneyProviderError):
        gateway_for(provider)


def test_the_router_map_is_keyed_by_the_stored_provider_value() -> None:
    """The ``POST /webhooks/{provider}/{tenant_slug}`` route carries the credential provider's
    stored string, so the map the router looks up must be keyed by exactly that — and by every money
    provider, so none is unreachable."""
    adapters = build_webhook_adapters()
    assert set(adapters) == {provider.value for provider in money_providers()}
    assert "mercado_pago" in adapters, "the route the docs publish must resolve to an adapter"


# ======================================================================================
# The fingerprint: WHAT a verification is about.
# ======================================================================================


REFUND_METHOD = gateway_method_for(GatewayOperation.REFUND)


def _load_edited_gateway_module(
    source: str, tmp_path: pathlib.Path, name: str, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Import ``source`` as a real module ON DISK, so ``inspect.getsource`` can read it back.

    A file, not a string: the fingerprint asks the filesystem what the module says, and a module
    built from a string has no source for ``inspect`` to find — faking it would exercise something
    other than the mechanism.

    It is registered in ``sys.modules`` for the same reason, and through ``monkeypatch`` so the
    registration is undone at teardown: ``inspect.getmodule`` resolves a class through
    ``sys.modules[cls.__module__]``, exactly as it does for a normally imported one. A copy left
    behind would be a second, stale definition of the gateway visible to every later test.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_editing_a_dependency_of_refund_without_touching_refund_invalidates_it(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """==The finding, reproduced: ``refund`` delegates, and the delegate used to be invisible.==

    ``StripeGateway.refund`` never names a URL. It calls ``self._client``, which builds the request
    out of ``_STRIPE_API_BASE`` — so repointing the API base is exactly the kind of edit that
    destroys the claim "this was exercised against the real Stripe", and the old fingerprint
    (``inspect.getsource(method)``) could not see it at all.

    So the base URL is repointed and ==``refund``'s own source is asserted byte-identical==. That
    second assertion is what makes this a proof rather than a coincidence: a method-only hash would
    return the same value for both modules. The module fingerprint must not.
    """
    original = pathlib.Path(str(inspect.getsourcefile(stripe))).read_text(encoding="utf-8")
    edited_source = original.replace(
        '_STRIPE_API_BASE = "https://api.stripe.com/v1"',
        '_STRIPE_API_BASE = "https://api.stripe.com/v99"',
    )
    assert edited_source != original, (
        "the constant this sabotage repoints is gone, so the test arranges nothing. If the "
        "gateway's API base moved or was renamed, point this at the new one."
    )

    edited = _load_edited_gateway_module(
        edited_source, tmp_path, "stripe_with_a_repointed_base", monkeypatch
    )

    # ==The control: what the OLD fingerprint would have said.== It hashed exactly this, so an
    # equal value here is the defect stated in its own terms — the sabotage is invisible to it.
    # Without this line the test could pass because the sabotage touched `refund` after all.
    def method_only_hash(gateway_type: type) -> str:
        source = inspect.getsource(getattr(gateway_type, REFUND_METHOD))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    assert method_only_hash(edited.StripeGateway) == method_only_hash(StripeGateway), (
        f"the sabotage edited {REFUND_METHOD} itself, so it proves nothing about DEPENDENCIES — "
        "the whole point is that the method's own text is untouched"
    )
    assert module_fingerprint(edited.StripeGateway) != module_fingerprint(StripeGateway), (
        "the API base was repointed and the fingerprint did not move, so a verification saying "
        "'exercised against api.stripe.com' would go on authorising a live credential for code "
        "that now talks somewhere else"
    )


def test_an_untouched_module_keeps_its_fingerprint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """==The anti-vacuity half.== A fingerprint that changed on every read would pass the test above
    for the wrong reason — and would make every verification permanently stale, which is the
    "permanent by construction" guard the register was built to replace.

    Same bytes, different file: the hash is a function of the SOURCE and of nothing else.
    """
    original = pathlib.Path(str(inspect.getsourcefile(stripe))).read_text(encoding="utf-8")
    copied = _load_edited_gateway_module(original, tmp_path, "stripe_copied_verbatim", monkeypatch)

    assert module_fingerprint(copied.StripeGateway) == module_fingerprint(StripeGateway)


@pytest.mark.parametrize("provider", money_providers())
def test_both_operations_of_a_provider_share_its_module_fingerprint(
    provider: CredentialProvider,
) -> None:
    """Written down so it is a decision rather than a surprise: one module, one fingerprint.

    An edit to a gateway invalidates BOTH of its operations. The alternative is a hash able to say
    "checkout is still fine" about a file that changed underneath it — the claim the fingerprint
    exists to stop making.
    """
    fingerprints = {implementation_fingerprint(provider, op) for op in GatewayOperation}

    assert len(fingerprints) == 1, fingerprints
    assert fingerprints == {module_fingerprint(type(gateway_for(provider)))}


@pytest.mark.parametrize("provider", money_providers())
def test_the_fingerprint_is_the_same_in_a_fresh_process(provider: CredentialProvider) -> None:
    """==A fingerprint that is not reproducible is a guard that never opens.==

    Hashing source text is deterministic; hashing anything that carries a memory address is not, and
    that failure would be silent and total — every verification stale for ever, the door shut for
    good, looking exactly like a working guard. Cheap to rule out, so it is ruled out: a SECOND
    process, with its own address space, must produce the same string.
    """
    here = implementation_fingerprint(provider, GatewayOperation.CHECKOUT)
    program = (
        "from aethercal.server.integrations.money import implementation_fingerprint\n"
        "from aethercal.server.services.tenant_credentials import "
        "CredentialProvider, GatewayOperation\n"
        f"print(implementation_fingerprint(CredentialProvider({provider.value!r}), "
        "GatewayOperation.CHECKOUT))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == here, (
        f"{provider.value} fingerprints differently in a fresh process "
        f"({result.stdout.strip()!r} vs {here!r}): it is not a function of the source alone, so "
        "every verification would go stale the moment the process restarted"
    )


# ======================================================================================
# The USE gate: every gateway call site must consult it (H6).
# ======================================================================================


SERVER_SOURCE = pathlib.Path(inspect.getsourcefile(money) or "").parents[1]
"""``apps/server/src/aethercal/server`` — derived from a module that lives in it, never typed."""

USE_GATE = "authorise_live_use"


def _called_names(node: ast.AST) -> set[str]:
    """Every name this node calls, by attribute or plainly — ``a.b()`` and ``b()`` both give "b"."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        called = inner.func
        if isinstance(called, ast.Attribute):
            names.add(called.attr)
        elif isinstance(called, ast.Name):
            names.add(called.id)
    return names


def _functions_calling(tree: ast.AST, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``tree`` whose body calls ``name`` (directly, on anything)."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and name in _called_names(node)
    ]


def _reaches_the_gate(tree: ast.AST) -> set[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The functions in this module that reach :data:`USE_GATE`, directly or through a helper.

    ==One hop is not a bypass.== A call site may delegate the gate to a named helper in its own
    module — ``api/public.py`` does exactly that, so the refusal's docstring and its 402 mapping
    live in one place instead of being inlined at every checkout entry point. What must not happen
    is a call site that reaches the gate through NOTHING. So this closes over the module's call
    graph to a fixed point, and the guard asks about the closure rather than about one line.

    ==Keyed by NODE, not by name==, because a module holds more than one ``_run``:
    ``services/payments.py`` defines one closure per runner, and a name-keyed map silently kept the
    last — reporting the refund runner as ungated because the expire-hold runner shares its name.
    Edges still travel by name (a call names a function); only membership is by identity.
    """
    functions = {
        node: _called_names(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    reaching = {node for node, calls in functions.items() if USE_GATE in calls}
    while True:
        named = {node.name for node in reaching}
        grown = {node for node, calls in functions.items() if calls & named}
        if grown <= reaching:
            return reaching
        reaching |= grown


@pytest.mark.parametrize("operation", list(GatewayOperation), ids=lambda op: op.value)
def test_every_gateway_call_site_consults_the_use_gate(operation: GatewayOperation) -> None:
    """==The anti-omission lock for the gate that runs at USE.==

    The write-time door is unmissable: one funnel, ``store_credential``. The use gate is not — it
    lives beside each gateway call, and a call site that forgets it charges a real card through code
    nobody has exercised, silently. So "did anybody forget?" is asked of the tree rather than of a
    reviewer.

    For each operation, this finds every function in the server source that calls the gateway method
    it names, and requires that same function to call the gate. It walks
    :class:`GatewayOperation`, so F5's partial refund arrives already covered: add a third operation
    and its call sites must consult the gate on the day they are written.

    .. note::

       ==This proves the gate is CONSULTED, not that it is obeyed.== What the two directions do with
       the answer is decided in ``blocks_on_stale_evidence`` and asserted in
       ``test_credential_mode_guard``. What this catches is the omission — the failure that leaves
       no trace at all.
    """
    method = gateway_method_for(operation)
    unguarded: list[str] = []
    guarded = 0

    for path in SERVER_SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        gated = _reaches_the_gate(tree)
        for function in _functions_calling(tree, method):
            # The protocol's own declaration and the adapters that IMPLEMENT the method are not
            # call sites; they are the method. Only code that INVOKES a gateway is gated.
            if function.name == method:
                continue
            if function in gated:
                guarded += 1
            else:
                unguarded.append(f"{path.relative_to(SERVER_SOURCE).as_posix()}::{function.name}")

    # ==The informative assertion first.== "Nobody consulted the gate" is the finding; "nothing was
    # found at all" is the vacuity check, and reporting it first would hide the real one behind a
    # message about the guard rather than about the defect.
    assert not unguarded, (
        f"these call `{method}` on a business's credential without consulting `{USE_GATE}`: "
        f"{unguarded}. A credential stored under evidence that has since gone stale would move "
        "real money there with nothing asking whether the code was ever exercised."
    )
    assert guarded, (
        f"no call site of `{method}` was found at all, so this guard is watching nothing. If the "
        "checkout or refund path moved, move this with it."
    )
