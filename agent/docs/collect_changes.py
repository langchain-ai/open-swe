"""Standalone sandbox utility: export docs changes without following symlinks."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024
EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
}


def validate_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Invalid docs path")
    if any(
        part.startswith(".") or part in {"AGENTS.md", "CLAUDE.md", "SKILL.md"}
        for part in path.parts
    ):
        raise ValueError(
            "Docs publication cannot change automation, credentials, or agent instructions"
        )
    if path.suffix.lower() not in EXTENSIONS or any(
        part.lower() in {"secrets", "credentials", "node_modules"} for part in path.parts
    ):
        raise ValueError("Only documentation, docs configuration and images can be published")


def collect(root: Path, base: str) -> list[dict[str, object]]:
    root = root.resolve()

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args])

    tracked = git("diff", "--name-only", "-z", base, "--").split(b"\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    names = sorted({name.decode("utf-8") for name in tracked + untracked if name})
    if len(names) > 100:
        raise ValueError("Docs PR exceeds 100 changed files")
    total = 0
    changes: list[dict[str, object]] = []
    for name in names:
        validate_path(name)
        path = root / name
        for candidate in (path, *path.parents):
            if candidate == root:
                break
            if candidate.is_symlink():
                raise ValueError("Docs publication cannot follow symlinks")
        if os.path.commonpath([root, path.resolve()]) != str(root):
            raise ValueError("Docs path escaped checkout")
        if not path.exists():
            changes.append({"path": name, "content": None})
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError("Docs file is not a regular file or exceeds 2 MiB")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError("Docs PR exceeds 10 MiB")
        changes.append({"path": name, "content": base64.b64encode(data).decode("ascii")})
    return changes


if __name__ == "__main__":
    root, base, destination = sys.argv[1:]
    Path(destination).write_text(json.dumps(collect(Path(root), base)), encoding="utf-8")
