#!/usr/bin/env python3
"""Build per-platform PyPI wheels from cross-compiled ``mda`` binaries.

For each Rust target with a built binary under ``target/<triple>/release/``,
this bundles the binary into ``_binaries/`` and produces a platform-tagged
wheel under ``dist/pypi/``.

Usage:
    python packages/pypi/scripts/build_wheels.py
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYPI_DIR = HERE.parent
REPO_ROOT = PYPI_DIR.parent.parent
BINARIES_DIR = PYPI_DIR / "src" / "managed_deepagents" / "_binaries"
BUILD_CACHE_DIR = PYPI_DIR / "build"
DIST_DIR = REPO_ROOT / "dist" / "pypi"
PYPROJECT = PYPI_DIR / "pyproject.toml"


def load_targets() -> list[dict]:
    data = json.loads((REPO_ROOT / "packages" / "targets.json").read_text())
    return data["targets"]


@contextlib.contextmanager
def overridden_version() -> Iterator[None]:
    """Temporarily stamp ``MDA_VERSION`` into ``pyproject.toml``.

    PyPI has no dist-tags, so a "not latest" release must itself be a PEP 440
    pre-release (e.g. ``0.0.1.dev42``); pip then skips it unless ``--pre`` is
    used. The release workflow passes that version via ``MDA_VERSION``. When the
    variable is unset (local ``make package``), the manifest version is used and
    the file is left untouched.
    """
    version = os.environ.get("MDA_VERSION")
    if not version:
        yield
        return

    original = PYPROJECT.read_text()
    patched, count = re.subn(
        r'(?m)^version\s*=\s*".*"$', f'version = "{version}"', original, count=1
    )
    if count != 1:
        raise RuntimeError("could not find a single version field in pyproject.toml")
    PYPROJECT.write_text(patched)
    try:
        yield
    finally:
        PYPROJECT.write_text(original)


def binary_source(target: dict) -> Path:
    return REPO_ROOT / "target" / target["rustTarget"] / "release" / target["binary"]


def clean_staging() -> None:
    """Drop both places a previously staged binary can survive.

    setuptools keeps package data in ``build/lib`` across invocations and never
    prunes files that disappeared from the source tree, so the cache has to go
    as well. Otherwise a Windows wheel also ships the ``mda`` copied there by
    the preceding Linux target.
    """
    shutil.rmtree(BINARIES_DIR, ignore_errors=True)
    shutil.rmtree(BUILD_CACHE_DIR, ignore_errors=True)


def reset_binaries(binary: Path) -> None:
    clean_staging()
    BINARIES_DIR.mkdir(parents=True)
    dst = BINARIES_DIR / binary.name
    shutil.copy2(binary, dst)
    dst.chmod(0o755)


@contextlib.contextmanager
def staged_binaries() -> Iterator[None]:
    """Keep the staged binary out of the source tree once the build finishes.

    ``_binaries/`` is untracked scratch space. Leaving the last target's binary
    behind would make a subsequent plain ``uv build`` emit a ``py3-none-any``
    wheel carrying it.
    """
    try:
        yield
    finally:
        clean_staging()


def build_wheel(platform_tag: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # Build a pure wheel, then retag it for the target platform. The binary
        # makes the wheel platform-specific even though setuptools cannot infer
        # the tag on a cross-build host.
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", tmp],
            cwd=PYPI_DIR,
            check=True,
        )
        wheels = list(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one wheel, found: {wheels}")
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "wheel",
                "tags",
                "--platform-tag",
                platform_tag,
                "--remove",
                str(wheels[0]),
            ],
            check=True,
        )
        for retagged in Path(tmp).glob("*.whl"):
            shutil.move(str(retagged), str(DIST_DIR / retagged.name))


def main() -> int:
    built = 0
    with staged_binaries(), overridden_version():
        for target in load_targets():
            binary = binary_source(target)
            if not binary.exists():
                print(f"! skipping {target['pypi']['platformTag']}: missing {binary}")
                continue
            reset_binaries(binary)
            build_wheel(target["pypi"]["platformTag"])
            print(f"\u2713 built wheel for {target['pypi']['platformTag']}")
            built += 1

    if built == 0:
        print("No platform binaries found. Run the cross-compilation build first.")
        return 1
    print(f"\nBuilt {built} wheel(s) in {DIST_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
