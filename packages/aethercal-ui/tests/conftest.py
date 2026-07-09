"""Test configuration for aethercal-ui.

Importing `aethercal.ui.calendar` calls `rx.asset(path=..., shared=True)` at module scope to
resolve the packaged JS bundle's local `library` path. In a real consuming Reflex app, that call
also symlinks the asset into that app's `assets/external/` folder (relative to the process's
cwd) — correct there, but this repo's root is not a Reflex app, so we set
`REFLEX_BACKEND_ONLY=1` before the first import to skip that symlink side effect while still
resolving (and validating the existence of) the asset path itself.
"""

import os

os.environ.setdefault("REFLEX_BACKEND_ONLY", "1")
