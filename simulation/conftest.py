"""Put ``simulation/`` on ``sys.path`` so ``aethercal_sim`` imports without being installed.

The harness is deliberately NOT a workspace package: it has to run on a throwaway host with nothing
but a Python interpreter — no ``uv sync``, no wheel, no package index — which is what makes it
usable five minutes after a container is created and destroyed an hour later.

The cost of that choice is exactly this file. Under pytest's default import mode the directory added
to ``sys.path`` is the test file's own (``simulation/tests``), not the one holding the package, so
``import aethercal_sim`` would fail. A ``conftest.py`` here makes pytest insert THIS directory too.

.. rubric:: What this does NOT disturb

The root ``conftest.py`` owns two tree-wide gates and this file deliberately touches neither: the
``pytest_plugins`` registration of the network guard (only the rootdir conftest may declare it) and
the db-suite gate, which decides on the markers pytest actually selected. The tests collected here
are unmarked and offline — they open no socket, so the network guard has nothing to refuse — so
they join a bare ``pytest`` run and are deselected by ``-m db`` without changing either gate's
answer.

Only the pure, offline logic is tested here: percentiles, the traffic plan, the verdict. The parts
that need a running stack are not pytest tests at all — they are the harness itself, run by
``simulation/scripts/run.sh`` against a throwaway instance.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
