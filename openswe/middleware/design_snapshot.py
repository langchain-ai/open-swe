"""Collect bounded frontend fingerprints in repositories with design guidance."""

import hashlib
import json
import subprocess
from pathlib import Path


def snapshot(workspace: Path) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    roots = [workspace, *(path for path in workspace.iterdir() if path.is_dir())]
    for root in roots:
        if not (root / ".git").exists():
            continue
        if not any(
            (root / directory / skill / "SKILL.md").is_file()
            for directory in (".agents/skills", ".claude/skills")
            for skill in ("design-system", "macaw", "macaw-design")
        ):
            continue
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        )
        for name in set(result.stdout.decode().split("\0")):
            if Path(name).suffix not in {".tsx", ".jsx", ".css", ".scss", ".vue", ".svelte"}:
                continue
            path = root / name
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file()
                or path.stat().st_size > 2_000_000
            ):
                continue
            fingerprints[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


if __name__ == "__main__":
    print(json.dumps(snapshot(Path.cwd())))
