"""Which test modules actually reach a real provider. ==One definition, read by both gates.==

Two guards need this answer and they must not disagree:

* ``tests/test_live_suite_gate.py`` — "with no credential, did any of them PASS?" (a pass without a
  key can only mean the process really dialled out);
* ``tests/live/test_live_harness_guardrails.py`` — "does every one of their tests ask for the
  connectivity control?"

.. rubric:: ==Why this is parsed and not grepped==

Both used to answer it with ``"pytestmark = pytest.mark.live_provider" in source``. That is a
substring, and a substring cannot tell a module that CARRIES the marker from one that merely
MENTIONS it — so the moment a guard named the marker in order to look for it, it classified itself
as a provider harness. The offline guardrail file then had to "reach a provider", and the suite gate
demanded it not pass without a credential, which it does on every commit by design.

==A classifier that matches its own source is not a classifier.== So the question is asked of the
syntax tree: is there a module-level ``pytestmark`` bound to ``pytest.mark.live_provider``? A file
that quotes the marker inside a string answers no, because a string is not an assignment.
"""

from __future__ import annotations

import ast
import pathlib

LIVE_MARKER = "live_provider"
"""The pytest marker that opens the network door for a module (see ``pyproject.toml``)."""


def _is_the_live_marker(node: ast.expr) -> bool:
    """Is this expression ``pytest.mark.live_provider`` (however it is wrapped)?"""
    return any(
        isinstance(inner, ast.Attribute)
        and inner.attr == LIVE_MARKER
        and isinstance(inner.value, ast.Attribute)
        and inner.value.attr == "mark"
        for inner in ast.walk(node)
    )


def carries_the_live_marker(source: str) -> bool:
    """Does this module apply the marker to ITSELF, at module level?

    A module-level ``pytestmark`` assignment, checked structurally. A list of markers counts too,
    since ``ast.walk`` looks inside whatever the value happens to be.
    """
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            continue
        if _is_the_live_marker(node.value):
            return True
    return False


def provider_touching_modules(directory: pathlib.Path) -> list[pathlib.Path]:
    """Every ``test_*.py`` in ``directory`` that reaches a real provider. ==Found, never listed.==

    Reading the marker keeps both gates' question right without keeping a list beside them: a
    harness added tomorrow is covered the day it is written, because the thing that makes it a
    provider harness is the very thing this looks for.
    """
    return sorted(
        path
        for path in directory.glob("test_*.py")
        if carries_the_live_marker(path.read_text(encoding="utf-8"))
    )
