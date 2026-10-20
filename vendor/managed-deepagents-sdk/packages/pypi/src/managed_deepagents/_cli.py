"""Console-script launcher for the bundled ``mda`` binary."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def binary_path() -> Path:
    """Return the path to the bundled ``mda`` binary for this platform."""
    name = "mda.exe" if os.name == "nt" else "mda"
    return Path(__file__).parent / "_binaries" / name


def _runtime_version() -> str | None:
    """Return this package's installed version, or ``None`` if unavailable."""
    try:
        return version("managed-deepagents")
    except PackageNotFoundError:
        return None


def main() -> int:
    """Forward all arguments to the bundled binary and return its exit code."""
    binary = binary_path()
    if not binary.exists():
        sys.stderr.write(
            "managed-deepagents: the prebuilt mda binary was not found in this "
            "installation. Reinstall the platform-specific wheel.\n"
        )
        return 1

    # Advertise the runtime version to the binary so `mda --version` reports it
    # instead of the CLI crate version. execv/subprocess inherit os.environ.
    runtime_version = _runtime_version()
    if runtime_version:
        os.environ["MDA_RUNTIME_VERSION"] = runtime_version
    os.environ["MDA_INSTALL_CHANNEL"] = "pypi"
    os.environ["MDA_BUNDLED_RUNTIME_PATH"] = str(Path(__file__).parent)

    argv = [str(binary), *sys.argv[1:]]

    # On POSIX, replace the current process so signals propagate cleanly.
    if os.name == "posix":
        os.execv(str(binary), argv)
        return 0  # unreachable

    return subprocess.run(argv, check=False).returncode
