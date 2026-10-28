"""Fingerprint workspace checkouts without returning file contents or remote credentials."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The scan is about 120 git calls waiting on the disk; 8, 16 and 128 workers measured the same.
WORKERS = 16
# A cold box's first scan has taken 25s, and the caller gives the script 30.
DEADLINE_SECONDS = 20


class Skipped(Exception):
    """A checkout could not be fingerprinted; the rest of the scan still can."""


class Git:
    def __init__(self, pool: ThreadPoolExecutor) -> None:
        self.pool = pool
        self.deadline = time.monotonic() + DEADLINE_SECONDS

    def start(self, path: Path, *args: str) -> "Future[subprocess.CompletedProcess[bytes]]":
        return self.pool.submit(self.run, path, args)

    def run(self, path: Path, args: Tuple[str, ...]) -> "subprocess.CompletedProcess[bytes]":
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Skipped(f"git {args[0]} did not start before the scan deadline")
        try:
            return subprocess.run(
                ["git", "--no-optional-locks", "-C", str(path), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise Skipped(f"git {args[0]} timed out") from None


def output(call: "Future[subprocess.CompletedProcess[bytes]]", optional: bool = False) -> bytes:
    result = call.result()
    if result.returncode:
        if optional:
            return b""
        raise Skipped("git failed")
    return result.stdout


class Checkout:
    """One checkout, with every git call it needs already running."""

    def __init__(self, git: Git, path: Path) -> None:
        self.path = path
        self.remote = git.start(path, "remote", "get-url", "origin")
        self.head = git.start(path, "rev-parse", "--verify", "HEAD")
        self.branch = git.start(path, "branch", "--show-current")
        self.diff = git.start(path, "diff", "--no-ext-diff", "--no-textconv", "HEAD")
        self.untracked = git.start(path, "ls-files", "--others", "--exclude-standard", "-z")

    def fingerprint(self) -> Optional[Dict[str, str]]:
        """None for a checkout that is not a GitHub repository with a commit."""
        remote = output(self.remote, optional=True).decode().strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",
            remote,
        )
        if not match:
            return None
        head = output(self.head, optional=True).decode().strip()
        if not head:
            return None
        digest = hashlib.sha256(output(self.diff))
        for name in output(self.untracked).split(b"\0"):
            if name:
                try:
                    stat = (self.path / os.fsdecode(name)).lstat()
                except OSError:
                    raise Skipped("an untracked file changed during the scan") from None
                digest.update(name)
                digest.update(str((stat.st_size, stat.st_mtime_ns)).encode())
        return {
            "repo": match.group(1),
            "branch": output(self.branch).decode().strip(),
            "head": head,
            "dirty": digest.hexdigest(),
        }


def snapshot(directory: str) -> Dict[str, object]:
    root = Path(directory)
    paths = [root] if (root / ".git").exists() else sorted(root.iterdir())
    repos: Dict[str, Dict[str, str]] = {}
    skipped: Dict[str, str] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        git = Git(pool)
        checkouts: List[Checkout] = [
            Checkout(git, path)
            for path in paths
            if not path.is_symlink() and (path / ".git").exists()
        ]
        for checkout in checkouts:
            try:
                fingerprint = checkout.fingerprint()
            except Skipped as reason:
                skipped[str(checkout.path)] = str(reason)
                continue
            if fingerprint:
                repos[str(checkout.path)] = fingerprint
    return {"repos": repos, **({"skipped": skipped} if skipped else {})}


if __name__ == "__main__":
    try:
        request = json.loads(base64.b64decode(sys.argv[1]))
        print(json.dumps(snapshot(request["root"])))
    except (OSError, ValueError):
        print(json.dumps({"error": "Could not inspect workspace git state"}))
