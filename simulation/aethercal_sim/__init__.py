"""AetherCal's two-week load simulation harness.

Standalone tooling, like ``e2e/`` and ``loadtest/`` — not part of the shipped product, and nothing
in ``aethercal`` imports it. Stdlib only, so it runs on a throwaway host with nothing installed but
Python.

See ``simulation/README.md`` for what a run proves and, just as importantly, what it cannot.
"""

from __future__ import annotations
