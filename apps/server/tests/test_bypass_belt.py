"""==The bypass belt, DERIVED FROM THE CODE.== A new consumer that does not declare its reason
breaks CI.

.. rubric:: Why an AST walk, and not a list in a document

An earlier draft of this design closed the question with a sentence: *"exactly these five queries
need the bypass."* A list is a photograph, not a belt. Three consumers of that very same
specification did not fit in it — the ``/metrics`` scrape, the parked-payment tick, and
``/health/ready`` — and an enumeration of instances rots the moment somebody adds the sixth.

So the rule is enforced against the SOURCE, not against anybody's memory:

* the ``BYPASSRLS`` engine is private to :class:`~aethercal.server.db.pools.WorkerPools`, and
  ``scan_session(reason)`` is its only door;
* every call to it passes a **literal** ``BypassReason`` member, so the reason is visible at the call
  site and greppable — never a variable that could be anything at run time;
* :func:`~aethercal.server.db.pools.why_bypass` is exhaustive over the enum (``assert_never``), so a
  new member with no branch fails the type check;
* and the escape hatch that exists for the offline harness (``WorkerPools.for_offline_tests``) is
  nailed shut on the side that matters: it is never called from the product's own source.

The precedent for this shape is already in the repository — ``test_users_service`` and
``test_workflow_rules_service`` both use ``ast`` to assert "this is constructed in exactly one
place". Same idea, applied to the one privilege in the system that can read every business at once.

``ast``, not a regular expression, and deliberately: the docstrings in ``pools.py`` NAME
``_scan_maker`` and ``for_offline_tests`` several times over, and a grep would count the explanation
as the violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aethercal.server.db.pools import BypassReason, WorkerPools, why_bypass

_SRC = Path(__file__).resolve().parents[1] / "src" / "aethercal" / "server"
_POOLS = _SRC / "db" / "pools.py"

_PRIVATE_SCAN_MAKER = "_scan_maker"
_OFFLINE_HATCH = "for_offline_tests"


def _modules() -> list[Path]:
    """Every module of the shipped server source. The Alembic versions are generated, and excluded."""
    return [
        path
        for path in sorted(_SRC.rglob("*.py"))
        if "migrations" not in path.parts and "__pycache__" not in path.parts
    ]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path: Path) -> str:
    return path.relative_to(_SRC).as_posix()


class TestTheBypassEngineHasExactlyOneDoor:
    def test_nothing_outside_pools_touches_the_private_scan_maker(self) -> None:
        """==The whole belt, in one assertion.==

        ``_scan_maker`` is the ``BYPASSRLS`` session factory. If any module could reach it, then the
        enum, the exhaustiveness check and the marker would all be decoration: a consumer could open
        a cross-business session, read every guest of every business, and declare nothing at all.

        "Touches" means any mention as an attribute or a keyword — construction included. The only
        file allowed to do either is ``pools.py`` itself, plus the worker, which builds the object
        exactly once (and is checked separately, below).
        """
        offenders: dict[str, list[int]] = {}
        for path in _modules():
            if path == _POOLS or path.name == "worker.py":
                continue
            hits = [
                node.lineno
                for node in ast.walk(_tree(path))
                if (isinstance(node, ast.Attribute) and node.attr == _PRIVATE_SCAN_MAKER)
                or (isinstance(node, ast.keyword) and node.arg == _PRIVATE_SCAN_MAKER)
            ]
            if hits:
                offenders[_rel(path)] = hits

        assert offenders == {}, (
            "these modules reach the BYPASSRLS session factory directly, going around "
            f"`scan_session(BypassReason...)`: {offenders}. That is a cross-business read which "
            "declares nothing — precisely what this belt exists to make impossible."
        )

    def test_only_the_worker_constructs_the_pools(self) -> None:
        """``WorkerPools(...)`` is built in ONE place: the worker's factory.

        Anywhere else would mean a second process — or, far worse, the WEB process — holding an
        engine with ``BYPASSRLS``. Criterion 2 says the web has none, and this is what makes that
        statement structural rather than aspirational.
        """
        builders = {
            _rel(path)
            for path in _modules()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == WorkerPools.__name__
        }
        assert builders == {"worker.py"}, (
            f"WorkerPools is constructed in {sorted(builders)}. Only the worker may build it: "
            "anywhere else is another process holding a BYPASSRLS engine."
        )

    def test_the_offline_escape_hatch_is_never_used_by_the_product(self) -> None:
        """``for_offline_tests`` puts both pools on ONE sessionmaker.

        In the offline harness that is harmless — SQLite has no RLS to bypass. In the product it
        would be a catastrophe: a session MARKED as bypassed on a pool that is not, and
        ``collect_metrics`` — which trusts that marker — would go straight back to reporting zeros
        over a burning queue. The hatch is real, so it is nailed shut on the side that matters.
        """
        users = {
            _rel(path)
            for path in _modules()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Attribute) and node.attr == _OFFLINE_HATCH
        }
        assert users == set(), (
            f"{sorted(users)} call `WorkerPools.for_offline_tests` inside the shipped source. It is "
            "for the offline SQLite harness, and for nothing else."
        )


class TestEveryConsumerDeclaresItsReason:
    def test_every_scan_session_call_passes_a_literal_bypass_reason(self) -> None:
        """==Add a consumer without declaring WHY, and this goes red.==

        A variable would defeat the point: the reason has to be readable at the call site, by a
        person, in a diff — not resolved at run time to whatever happened to be in scope.
        """
        bad: list[str] = []
        found = 0
        for path in _modules():
            for node in ast.walk(_tree(path)):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "scan_session"
                ):
                    continue
                found += 1
                argument = node.args[0] if node.args else None
                literal = (
                    isinstance(argument, ast.Attribute)
                    and isinstance(argument.value, ast.Name)
                    and argument.value.id == BypassReason.__name__
                )
                if not literal:
                    bad.append(f"{_rel(path)}:{node.lineno}")

        assert found > 0, "the AST walk found no scan_session call at all — the guard is vacuous"
        assert bad == [], (
            f"these calls take the BYPASSRLS pool without a literal BypassReason: {bad}. The reason "
            "must be visible at the call site."
        )

    def test_every_declared_reason_is_handled(self) -> None:
        """``why_bypass`` is exhaustive at run time too, not only under pyright's ``assert_never``.

        The type check is the real gate — a new member with no ``case`` fails ``pyright`` — but this
        costs one loop and catches the day somebody silences the type error rather than thinking
        about the branch.
        """
        for reason in BypassReason:
            assert why_bypass(reason), f"{reason} has no declared justification"

    def test_the_claim_is_in_the_enum(self) -> None:
        """==``CLAIM_OUTBOX`` — the one that was nearly missed.==

        ``claim_one`` is an ``UPDATE ... WHERE id = :id AND status = 'pending'`` over a row whose
        ``tenant_id`` can only be learned by READING it. Under RLS with no GUC bound, that UPDATE
        matches **zero rows** → ``work is None`` → "unclaimed" → ``continue`` → the drainer reclaims
        NOTHING, ever, and never raises. An earlier draft of the design left it out entirely.
        """
        assert BypassReason.CLAIM_OUTBOX in BypassReason

    def test_the_parked_payment_scan_is_reserved_in_the_enum(self) -> None:
        """``PLAN_PARKED_PAYMENTS`` is declared BEFORE the payments batch needs it, on purpose.

        A parked payment event cannot travel through the outbox — ``outbox.booking_id`` is NOT NULL,
        and a parked event is by definition the one whose booking does not exist yet. Without a
        cross-business scan of its own, "it is never discarded" quietly becomes "it is never
        retried", and the dead-letter alarm never fires because nobody is looking.
        """
        assert BypassReason.PLAN_PARKED_PAYMENTS in BypassReason


class TestTheMarkerIsSetInOneAndOnlyOnePlace:
    def test_mark_bypass_is_called_only_by_scan_session(self) -> None:
        """``collect_metrics`` REFUSES a session without the marker — and that refusal is only as
        strong as the marker being impossible to forge.

        Test fixtures may set it. That is the entire reason the detection is a marker rather than
        ``SELECT current_user``, which would have forced eleven offline ``collect_metrics`` tests off
        SQLite and cost real coverage. But nothing in the SHIPPED source may.
        """
        setters: dict[str, list[int]] = {}
        for path in _modules():
            if path == _POOLS:
                continue
            hits = [
                node.lineno
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "mark_bypass"
            ]
            if hits:
                setters[_rel(path)] = hits

        assert setters == {}, (
            f"{setters} stamp the bypass marker outside pools.py. A forged marker makes "
            "`collect_metrics` believe it is reading every business when it is reading none — the "
            "zero-filled, permanently-green dead-man switch this batch exists to prevent."
        )


@pytest.mark.parametrize("reason", list(BypassReason))
def test_the_reason_enum_has_no_orphan(reason: BypassReason) -> None:
    """Every member carries a real ``str`` value — it is what the marker carries around."""
    assert isinstance(reason.value, str)
    assert reason.value
