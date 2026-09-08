"""Build snapshot-persisted tgrep indexes for Git repositories."""

import argparse
import hashlib
import os
import subprocess
from pathlib import Path


def repositories(workspace: Path) -> list[Path]:
    """Return immediate Git checkouts under a workspace."""
    return sorted(path for path in workspace.iterdir() if (path / ".git").exists())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--index-root", type=Path, default=Path("/opt/open-swe/tgrep-indexes"))
    parser.add_argument("--tgrep", default="/usr/local/bin/tgrep")
    args = parser.parse_args()
    args.index_root.mkdir(parents=True, exist_ok=True)
    for repository in repositories(args.workspace):
        root = Path(os.path.realpath(repository))
        key = hashlib.sha256(str(root).encode()).hexdigest()
        subprocess.run(
            [args.tgrep, "index", str(root), "--index-path", str(args.index_root / key)],
            check=True,
        )


if __name__ == "__main__":
    main()
