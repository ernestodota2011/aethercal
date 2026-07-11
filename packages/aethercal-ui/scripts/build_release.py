"""Release build for ``aethercal-ui`` with an explicit anti-"wheel-without-asset" gate.

Hatchling's ``artifacts`` mechanism will happily build a VALID-looking wheel that is silently
MISSING the JS bundle if the asset file is absent — no warning, no error (documented in
docs/spikes/f0-10-reflex-tsx.md, "Hatchling's artifacts mechanism has a silent failure mode").
A published wheel with no ``aethercal-calendar.js`` inside would import-fail for every consumer
at ``rx.asset()`` time. This script closes that hole:

1. verify the committed JS bundle exists and is non-empty **before** packaging,
2. build the wheel with ``uv build``,
3. re-open the built wheel and assert the bundle is actually inside it and non-empty.

Any failure exits non-zero, so a release can never ship (or "succeed" producing) an asset-less
wheel unnoticed. It assumes the committed bundle is fresh — CI's rebuild-and-diff drift guard
(``.github/workflows/ci.yml`` job ``calendar-js``) is what enforces freshness; run
``pnpm build`` in ``packages/aethercal-ui/js`` first if you changed the TSX.

Run from the repo root::

    uv run python packages/aethercal-ui/scripts/build_release.py
    uv run python packages/aethercal-ui/scripts/build_release.py --out-dir dist
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# packages/aethercal-ui/ (this file lives in packages/aethercal-ui/scripts/).
PKG_DIR = Path(__file__).resolve().parent.parent
# packages/aethercal-ui/../.. -> monorepo root (where `uv build --package` must run).
REPO_ROOT = PKG_DIR.parent.parent
ASSET_REL = Path("src/aethercal/ui/assets/aethercal-calendar.js")
# The bundle's path *inside* the wheel (POSIX separators — zip arcnames are always "/").
WHEEL_ARCNAME = "aethercal/ui/assets/aethercal-calendar.js"


class AssetMissingError(RuntimeError):
    """Raised when the JS bundle the wheel must ship is absent or empty."""


def verify_source_asset(pkg_dir: Path) -> Path:
    """Return the committed JS bundle path, or raise ``AssetMissingError`` if missing/empty.

    Args:
        pkg_dir: The ``aethercal-ui`` package directory (the one holding ``pyproject.toml``).

    Returns:
        The resolved path to the bundle.

    Raises:
        AssetMissingError: If the bundle file does not exist, or exists but is zero bytes.
    """
    asset = pkg_dir / ASSET_REL
    if not asset.is_file():
        msg = (
            f"JS bundle missing at {asset}. Run `pnpm build` in packages/aethercal-ui/js before "
            "building the wheel (hatchling would otherwise ship an asset-less wheel silently)."
        )
        raise AssetMissingError(msg)
    if asset.stat().st_size == 0:
        msg = f"JS bundle at {asset} is empty — a truncated build would ship a broken wheel."
        raise AssetMissingError(msg)
    return asset


def wheel_contains_asset(wheel_path: Path, arcname: str = WHEEL_ARCNAME) -> bool:
    """Return whether ``wheel_path`` contains a non-empty entry at ``arcname``."""
    with zipfile.ZipFile(wheel_path) as zf:
        try:
            info = zf.getinfo(arcname)
        except KeyError:
            return False
        return info.file_size > 0


def build_wheel(repo_root: Path, out_dir: Path) -> Path:
    """Build the ``aethercal-ui`` wheel, verify it ships the bundle, then move it into ``out_dir``.

    Builds into a fresh, empty staging dir and checks ``wheel_contains_asset`` there — the invalid
    (asset-less) wheel this gate exists to catch NEVER reaches ``out_dir``; it is dropped with the
    staging dir. Only a wheel that passes is moved to the final destination. Staging also makes the
    verified wheel unambiguously the one THIS run produced (the version is a constant ``0.0.0``, so
    a same-named wheel left by a prior build would otherwise be indistinguishable).

    Raises:
        RuntimeError: If ``uv build`` does not produce exactly one ``aethercal_ui-*.whl``.
        AssetMissingError: If the freshly built wheel does not contain a non-empty JS bundle.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aethercal-wheel-") as staging_str:
        staging = Path(staging_str)
        subprocess.run(
            ["uv", "build", "--package", "aethercal-ui", "--wheel", "--out-dir", str(staging)],
            cwd=repo_root,
            check=True,
        )
        built = list(staging.glob("aethercal_ui-*.whl"))
        if len(built) != 1:
            msg = (
                f"expected exactly one aethercal_ui wheel from uv build, got {len(built)}: {built}"
            )
            raise RuntimeError(msg)
        wheel = built[0]
        if not wheel_contains_asset(wheel):
            msg = (
                f"built wheel is MISSING a non-empty {WHEEL_ARCNAME} — hatchling produced an "
                "asset-less wheel. It is left in staging (never moved to the output dir); rebuild "
                "the bundle and re-run."
            )
            raise AssetMissingError(msg)
        final = out_dir / wheel.name
        if final.exists():
            final.unlink()
        shutil.move(str(wheel), str(final))
        return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(PKG_DIR / "dist"),
        help="Directory for the built wheel (default: packages/aethercal-ui/dist).",
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir).resolve()

    try:
        asset = verify_source_asset(PKG_DIR)
        print(f"OK: source bundle present ({asset.stat().st_size} bytes) -> {asset}")
        wheel = build_wheel(REPO_ROOT, out_dir)
    except AssetMissingError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK: wheel ships the JS bundle -> {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
