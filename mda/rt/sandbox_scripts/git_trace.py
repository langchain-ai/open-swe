"""Fingerprint workspace checkouts without returning file contents or remote credentials."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict


def git(path: Path, *args: str, optional: bool = False) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if result.returncode:
        if optional:
            return b""
        raise ValueError("git inspection failed")
    return result.stdout


def snapshot(directory: str) -> Dict[str, Dict[str, Dict[str, str]]]:
    root = Path(directory)
    paths = [root] if (root / ".git").exists() else sorted(root.iterdir())
    repos = {}
    for path in paths:
        if path.is_symlink() or not (path / ".git").exists():
            continue
        remote = git(path, "remote", "get-url", "origin", optional=True).decode().strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",
            remote,
        )
        if not match:
            continue
        head = git(path, "rev-parse", "--verify", "HEAD", optional=True).decode().strip()
        if not head:
            continue
        branch = git(path, "branch", "--show-current").decode().strip()
        digest = hashlib.sha256(git(path, "diff", "--no-ext-diff", "--no-textconv", "HEAD"))
        untracked = git(path, "ls-files", "--others", "--exclude-standard", "-z")
        for name in untracked.split(b"\0"):
            if name:
                stat = (path / os.fsdecode(name)).lstat()
                digest.update(name)
                digest.update(str((stat.st_size, stat.st_mtime_ns)).encode())
        repos[str(path)] = {
            "repo": match.group(1),
            "branch": branch,
            "head": head,
            "dirty": digest.hexdigest(),
        }
    return {"repos": repos}


if __name__ == "__main__":
    try:
        request = json.loads(base64.b64decode(sys.argv[1]))
        print(json.dumps(snapshot(request["root"])))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print(json.dumps({"error": "Could not inspect workspace git state"}))
