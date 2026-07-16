"""==The sender belt, DERIVED FROM THE CODE.==

A new sending consumer that skips the funnel breaks CI.

.. rubric:: Why an AST walk, and not a list in a document

The rule this file enforces could be written as a sentence: *"these three senders are built in
``services/tenant_senders.py``."* A sentence is a photograph. It was true of B-03's specification
too — which named SMTP, WhatsApp and SMS as the INFRA providers, wired the credential machinery for
all three, and then left every one of them being CONSTRUCTED at boot from the instance's environment
anyway. The list was right and the code still leaked, because nothing checked the code against it.

So the rule is enforced against the SOURCE:

* a live sending client is constructed ONLY inside ``services/tenant_senders.py``, whose one entry
  point (``resolve_tenant_senders``) takes a ``tenant_id`` with no default. ==Those two facts
  together are the belt==: a caller cannot obtain a sender without saying whose it is, and cannot go
  around the funnel to build one;
* the instance's sender configuration is READ only there too — otherwise a future
  ``build_email_sender`` could quietly reappear at a process edge;
* no module reaches for a process-wide sender on ``app.state``, which is exactly the door B-03bis
  closed.

.. rubric:: ==And the set of classes is DERIVED, not typed out here==

The three names are not written in this file. They are read out of ``tenant_senders._SPECS`` — the
table the funnel itself dispatches on — and a separate test proves that table covers **every** INFRA
provider in :class:`CredentialProvider`. So the day somebody adds a fourth sending provider they
must add a spec (or ``test_every_sending_provider_has_a_spec`` goes red), and the moment they do,
these locks cover their new classes **without anybody remembering this file exists**. That is the
difference between a belt and an inventory: the fourth provider is the one an enumeration always
misses.

The precedent for this shape is ``test_bypass_belt.py``, which does the same to the one privilege
that can read every business at once. ``ast``, not a regular expression, and deliberately: the
docstrings in ``tenant_senders.py`` NAME every one of these classes several times over while
explaining the rule, and a grep would count the explanation as the violation.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    credential_class,
)
from aethercal.server.services.tenant_senders import _SPECS, resolve_tenant_senders

_SRC = Path(__file__).resolve().parents[1] / "src" / "aethercal" / "server"
_FUNNEL = _SRC / "services" / "tenant_senders.py"

_PROCESS_WIDE_SENDER_ATTRS = frozenset({"email_sender", "channel_senders"})
"""The two ``app.state`` attributes B-03bis removed.

They held one SMTP client and one WhatsApp/SMS registry, built at boot from the instance's
environment, and the drain sent EVERY business's messages through them. Nothing may reach for them
again — under these names the object could only ever be the instance's, never a business's."""


def _modules() -> list[Path]:
    """Every module of the shipped server source. The Alembic versions are generated: excluded."""
    return [
        path
        for path in sorted(_SRC.rglob("*.py"))
        if "migrations" not in path.parts and "__pycache__" not in path.parts
    ]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path: Path) -> str:
    return path.relative_to(_SRC).as_posix()


def _constructors_of(names: frozenset[str], *, excluding: Path) -> dict[str, list[int]]:
    """Every module outside ``excluding`` that CONSTRUCTS one of ``names``, with line numbers."""
    offenders: dict[str, list[int]] = {}
    for path in _modules():
        if path == excluding:
            continue
        hits = [
            node.lineno
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in names
        ]
        if hits:
            offenders[_rel(path)] = hits
    return offenders


def _sender_types() -> frozenset[str]:
    return frozenset(spec.sender_type.__name__ for spec in _SPECS.values())


def _config_types() -> frozenset[str]:
    return frozenset(spec.config_type.__name__ for spec in _SPECS.values())


class TestTheFunnelCoversEverySendingProvider:
    def test_every_sending_provider_has_a_spec(self) -> None:
        """==The derivation the locks below stand on.==

        If a provider could be INFRA without a spec, it would be a sending provider whose sender is
        built somewhere else — and the AST locks, which read their class names out of this table,
        would not know to look for it. The belt would stay green and the hole would be open.

        ``resolve_tenant_senders`` would also simply never build it: a channel silently absent for
        every business on the instance. That is this codebase's signature failure, so it is a test.
        """
        infra = {
            provider
            for provider in CredentialProvider
            if credential_class(provider) is CredentialClass.INFRA
        }
        assert infra, "the walk found no INFRA provider at all — the guard is vacuous"
        assert set(_SPECS) == infra, (
            f"the sender funnel covers {sorted(p.value for p in _SPECS)} but the INFRA providers "
            f"are {sorted(p.value for p in infra)}. A sending provider with no spec is a channel "
            "that is silently off for every business — and one the belt below cannot see."
        )

    def test_the_spec_table_names_real_classes(self) -> None:
        """Anti-vacuity: the locks below read their names from here, so an empty table would make
        every one of them pass while enforcing nothing at all."""
        assert _sender_types(), "no sender types derived — the belt would be vacuous"
        assert _config_types(), "no config types derived — the belt would be vacuous"


class TestALiveSenderIsBuiltInExactlyOnePlace:
    def test_nothing_outside_the_funnel_constructs_a_sender(self) -> None:
        """==The whole belt, in one assertion.==

        A live sending client is the object that decides WHOSE account a message leaves on. Built
        anywhere but the funnel, it is built without a ``tenant_id`` in scope to build it FOR —
        which is not hypothetical: it is precisely what ``app.build_email_sender`` and
        ``app.build_channel_senders`` did, at boot, for every business at once.

        The funnel is the only file allowed to do it, because it is the only one that cannot do it
        without naming a business.
        """
        offenders = _constructors_of(_sender_types(), excluding=_FUNNEL)
        assert offenders == {}, (
            "these modules construct a live sender outside `services/tenant_senders.py`: "
            f"{offenders}. A sender built there is a sender built without a business — which is "
            "how a business's message goes out on somebody else's account. Resolve it through "
            "`resolve_tenant_senders(session, tenant_id=...)` instead."
        )

    def test_the_funnel_really_does_construct_them(self) -> None:
        """Anti-vacuity, the other way round.

        If the funnel stopped constructing senders — an accidental deletion, a refactor that moved
        the construction into a helper module — the lock above would go green over a product that
        builds its senders somewhere this file no longer inspects.
        """
        built = {
            node.func.id
            for node in ast.walk(_tree(_FUNNEL))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _sender_types()
        }
        assert built == _sender_types(), (
            f"the funnel constructs {sorted(built)} but the spec table declares "
            f"{sorted(_sender_types())}. Every declared sender must be built here, or it is built "
            "somewhere the belt above does not look."
        )


class TestATenantsUrlCannotBeDialedWithoutTheEgressGuard:
    """==The structural price of B-03bis, locked the same way as everything else here.==

    Moving ``base_url`` out of the environment and into a per-business credential turned it from
    operator configuration into ==third-party input this server obeys==. A guard closes that — and a
    guard somebody must remember to call is not a guard, so the type does the remembering:
    ``_build_phone_sender`` requires an ``_EgressTarget``, and only ``_assert_target_reachable``
    constructs one.

    Pyright enforces that a phone sender *has* a witness. These tests are the belt-and-braces: they
    catch the day somebody mints one somewhere else — which would compile, read as a tidy refactor,
    and silently re-open a hole onto the cloud metadata service.
    """

    def test_the_witness_is_minted_in_exactly_one_place(self) -> None:
        """``_EgressTarget(...)`` appears in ONE function. ==Anywhere else forges the proof.==

        The type-checker enforces that a phone sender *has* a witness. Only this enforces that the
        witness ever meant anything: a second constructor is a second way to say "validated" without
        having validated.
        """
        minted: dict[str, list[int]] = {}
        for path in _modules():
            hits = [
                node.lineno
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_EgressTarget"
            ]
            if hits:
                minted[_rel(path)] = hits

        assert list(minted) == ["services/tenant_senders.py"], (
            f"`_EgressTarget` is constructed in {minted}. It is a WITNESS that a tenant's base_url "
            "passed the egress guard — minting one anywhere but `_assert_target_reachable` forges "
            "that proof, and `_build_phone_sender` dials the URL believing it."
        )
        guard = next(
            node
            for node in ast.walk(_tree(_FUNNEL))
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name == "_assert_target_reachable"
        )
        inside = {
            node.lineno
            for node in ast.walk(guard)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_EgressTarget"
        }
        assert inside, "the guard mints no witness at all — this lock would be vacuous"
        assert set(minted["services/tenant_senders.py"]) == inside, (
            "a witness is minted in `tenant_senders.py` OUTSIDE `_assert_target_reachable`. That "
            "is the one function allowed to say a URL was validated, because it is the one that "
            "validates it."
        )

    def test_the_smtp_witness_is_minted_in_exactly_one_place(self) -> None:
        """``_SmtpTarget(...)`` too. ==The relay has no URL and no HTTP client, and the same rule.==

        It attests that a business's relay host passed the guard AND carries the connector that pins
        it at connect. Minting one anywhere else claims both without having done either — and SMTP
        is the path with no certificate to catch the mistake.
        """
        minted = {
            _rel(path)
            for path in _modules()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_SmtpTarget"
        }
        assert minted == {"services/tenant_senders.py"}, (
            f"`_SmtpTarget` is constructed in {sorted(minted)}. Only `_assert_smtp_host_reachable` "
            "may say a relay host was validated, because it is the one that validates it."
        )
        guard = next(
            node
            for node in ast.walk(_tree(_FUNNEL))
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name == "_assert_smtp_host_reachable"
        )
        inside = [
            node
            for node in ast.walk(guard)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_SmtpTarget"
        ]
        assert inside, "the SMTP guard mints no witness at all — this lock would be vacuous"

    def test_the_email_sender_is_wired_with_the_witnesss_connector(self) -> None:
        """==The connector must actually reach the sender, or the witness is decoration.==

        ``_SmtpTarget`` can carry a perfectly good connector that nobody passes on — the witness
        would be true, and ``aiosmtplib`` would resolve the host itself and land wherever DNS said.
        So every ``SmtpEmailSender`` the funnel builds must be given ``connect=`` off the witness.
        """
        calls = [
            node
            for node in ast.walk(_tree(_FUNNEL))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SmtpEmailSender"
        ]
        assert calls, "the funnel builds no SmtpEmailSender — this lock would be vacuous"
        for call in calls:
            wired = [keyword.value for keyword in call.keywords if keyword.arg == "connect"]
            assert wired, (
                "an SmtpEmailSender is built without `connect=`. aiosmtplib would then resolve the "
                "host itself at connect time, and a business's relay could rebind onto the "
                "operator's own MTA — the open relay this batch closed."
            )
            assert all(
                isinstance(value, ast.Attribute)
                and value.attr == "connect"
                and isinstance(value.value, ast.Name)
                and value.value.id.endswith("target")
                for value in wired
            ), "the connector must come off the witness, not from somewhere the guard never saw"

    def test_the_phone_sender_reads_the_url_off_the_witness_and_not_the_credential(self) -> None:
        """==Validate one string and dial another, and the guard was theatre.==

        ``_build_phone_sender`` must take its ``base_url`` from ``target.url`` — the value that went
        through the guard — never back out of ``secrets``. The two are equal today, and a future
        normalisation inside the guard (a punycode fold, a redirect chase) would silently make them
        differ while every SSRF test stayed green.
        """
        builder = next(
            node
            for node in ast.walk(_tree(_FUNNEL))
            if isinstance(node, ast.FunctionDef) and node.name == "_build_phone_sender"
        )
        base_urls = [
            keyword.value
            for node in ast.walk(builder)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "base_url"
        ]
        assert base_urls, "no base_url is set in the builder at all — this lock would be vacuous"
        for value in base_urls:
            assert (
                isinstance(value, ast.Attribute)
                and value.attr == "url"
                and isinstance(value.value, ast.Name)
                and value.value.id == "target"
            ), (
                "`_build_phone_sender` sets base_url from something other than `target.url`. The "
                "witness carries the URL that was validated; reading `secrets['base_url']` again "
                "dials a string nobody checked."
            )


class TestTheOfflineEscapeHatchIsNailedShut:
    def test_the_product_never_uses_the_offline_resolver(self) -> None:
        """``TenantSenders.for_offline_tests`` hands the SAME senders to every business.

        In the offline harness that is harmless and useful — SQLite holds no credentials, there is
        no environment, and what those tests assert is the drain's bookkeeping, not whose account a
        fake recorded on.

        ==In the product it is the B-03bis bug restored, in one line==, and a line that would read
        as entirely reasonable in a diff: "the same senders for every business" is the exact
        description of the ``app.state.email_sender`` this cut deleted. The hatch is real, so it is
        nailed shut on the side that matters — precisely as ``test_bypass_belt`` does for
        ``WorkerPools.for_offline_tests``.
        """
        users = {
            _rel(path)
            for path in _modules()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Attribute) and node.attr == "for_offline_tests"
        }
        assert users == set(), (
            f"{sorted(users)} call `for_offline_tests` inside the shipped source. It returns one "
            "set of senders for every business — which is the leak this batch closed. Use "
            "`resolve_tenant_senders`."
        )


class TestTheInstanceConfigurationIsReadInOnePlace:
    def test_nothing_outside_the_funnel_reads_the_instance_sender_config(self) -> None:
        """==The lock that stops ``build_email_sender`` growing back.==

        Constructing the sender is not the only way to send as the operator; READING the operator's
        credentials is the step before it, and a process edge that reads them has already decided to
        use them for somebody. The funnel is where that decision is made, per provider
        (``instance_fallback``), so it is where the configuration is read.
        """
        offenders = _constructors_of(_config_types(), excluding=_FUNNEL)
        assert offenders == {}, (
            "these modules build the INSTANCE's sender configuration outside the funnel: "
            f"{offenders}. Whether a business may use the operator's configuration at all is "
            "`instance_fallback`'s decision — a phone account is an identity, not a lent pipe. "
            "Read it through `InstanceSenderDefaults.from_env` and resolve per business."
        )


class TestTheProcessWideSenderIsGone:
    def test_no_module_reaches_for_a_sender_on_app_state(self) -> None:
        """==The door B-03bis closed, nailed shut.==

        ``app.state.email_sender`` / ``app.state.channel_senders`` were process-wide by
        construction: one object, on a state shared by every request and every drain item. Reading
        either name back would re-open the leak in a single line, and it would look entirely
        ordinary in a diff.
        """
        offenders: dict[str, list[int]] = {}
        for path in _modules():
            hits = [
                node.lineno
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Attribute) and node.attr in _PROCESS_WIDE_SENDER_ATTRS
            ]
            if hits:
                offenders[_rel(path)] = hits

        assert offenders == {}, (
            f"these modules hold a process-wide sender: {offenders}. One sender on `app.state` is "
            "one sender for every business the drain works through — the B-03bis bug exactly. The "
            "worker keeps `app.state.instance_sender_defaults` (inert configuration) and resolves "
            "each business's senders per item."
        )


class TestTheFunnelCannotBeCalledWithoutABusiness:
    def test_resolve_tenant_senders_takes_a_tenant_id_with_no_default(self) -> None:
        """==The signature IS the belt, so the signature is what is asserted.==

        Every lock above says a sender can only be built in one place. This says that place cannot
        be used without naming a business. Give ``tenant_id`` a default — ``None``, "the instance",
        anything — and the funnel becomes something a hurried caller can invoke with no business at
        all, which is the shape this whole cut removed.

        The money path pins its own signature the same way, and for the same reason:
        ``resolve_money_credential`` has no ``instance_default`` parameter, and a test asserts that
        by name.
        """
        signature = inspect.signature(resolve_tenant_senders)
        tenant = signature.parameters["tenant_id"]
        assert tenant.kind is inspect.Parameter.KEYWORD_ONLY, (
            "tenant_id must be keyword-only: whose message this is has to be legible at the call "
            "site, not the first positional argument somebody miscounts."
        )
        assert tenant.default is inspect.Parameter.empty, (
            "tenant_id has acquired a default. There is no such thing as resolving senders for "
            "'no business in particular' — that is the instance-wide sender this cut deleted."
        )
