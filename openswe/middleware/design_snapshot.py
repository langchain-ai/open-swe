"""Collect bounded frontend fingerprints in repositories with design guidance."""

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def snapshot(workspace: Path, previous: dict[str, str] | None = None) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    previous = previous or {}
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
        marker = str(root / ".git")
        try:
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                capture_output=True,
            )
            fingerprints[marker] = head.stdout.decode().strip()
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
        except OSError, subprocess.CalledProcessError:
            logger.warning("Design repository snapshot unavailable: %s", root, exc_info=True)
            fingerprints.update(
                {key: value for key, value in previous.items() if Path(key).is_relative_to(root)}
            )
            continue
        for name in set(os.fsdecode(result.stdout).split("\0")):
            if Path(name).suffix not in {".tsx", ".jsx", ".css", ".scss", ".vue", ".svelte"}:
                continue
            path = root / name
            try:
                if (
                    path.is_symlink()
                    or not path.resolve().is_relative_to(root.resolve())
                    or not path.is_file()
                ):
                    continue
                stat = path.stat()
                if stat.st_size > 2_000_000:
                    continue
                signature = f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:"
                cached = previous.get(str(path), "")
                fingerprints[str(path)] = (
                    cached
                    if cached.startswith(signature)
                    else signature + hashlib.sha256(path.read_bytes()).hexdigest()
                )
            except OSError:
                logger.warning("Design file snapshot unavailable: %s", path, exc_info=True)
                if str(path) in previous:
                    fingerprints[str(path)] = previous[str(path)]
    return fingerprints


if __name__ == "__main__":
    print(json.dumps(snapshot(Path.cwd(), json.loads(sys.argv[1]) if len(sys.argv) > 1 else None)))
