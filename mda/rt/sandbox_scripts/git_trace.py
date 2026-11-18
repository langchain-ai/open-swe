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

# The scan is about 50 git calls waiting on the disk; 8, 16 and 128 workers measured the same.
WORKERS = 16
# Path records in `git status --porcelain=v2`: how many space-separated fields precede the path.
PATH_FIELDS = {b"1": 8, b"2": 9, b"u": 10, b"?": 1}
# A cold sandbox's first scan has taken 25s, and the caller gives the script 30.
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


def status(output: bytes) -> Tuple[str, str, List[Tuple[bytes, bytes]]]:
    """The branch, HEAD commit and changed paths of `git status --porcelain=v2 --branch -z`."""
    branch, head, changes = "", "", []
    records = iter(output.split(b"\0"))
    for record in records:
        kind = record[:1]
        if kind == b"#":
            key, _, value = record[2:].partition(b" ")
            if key == b"branch.head" and value != b"(detached)":
                branch = os.fsdecode(value)
            elif key == b"branch.oid" and value != b"(initial)":
                head = value.decode()
        elif kind in PATH_FIELDS:
            path = record.split(b" ", PATH_FIELDS[kind])[-1]
            # A rename's record is followed by its original path.
            entry = record + b"\0" + next(records, b"") if kind == b"2" else record
            changes.append((entry, path))
    return branch, head, changes


class Checkout:
    """One checkout with its git status and remote lookup already running."""

    def __init__(self, git: Git, path: Path) -> None:
        self.git = git
        self.path = path
        self.remote = git.start(path, "remote", "get-url", "origin")
        # One walk of the worktree names the branch, HEAD and every changed path; a further
        # edit to a path already changed shows in its lstat, not its record.
        self.status = git.start(
            path,
            "status",
            "--porcelain=v2",
            "--branch",
            "--no-ahead-behind",
            "--untracked-files=all",
            "-z",
        )

    def fingerprint(self) -> Optional[Dict[str, str]]:
        """None for a checkout that is not a GitHub repository with a commit."""
        remote = output(self.remote, optional=True).decode().strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",
            remote,
        )
        if not match:
            return None
        branch, head, changes = status(output(self.status))
        if not head:
            return None
        digest = hashlib.sha256()
        for record, name in changes:
            path = self.path / os.fsdecode(name)
            try:
                stat = path.lstat()
                token = (stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except FileNotFoundError:
                token = None
            except OSError:
                raise Skipped("a changed file could not be read during the scan") from None
            digest.update(record + b"\0" + str(token).encode() + b"\0")
            if (
                token is not None
                and record[:1] != b"?"
                and record.split(b" ", 3)[2].startswith(b"SC")
            ):
                digest.update(output(self.git.start(path, "rev-parse", "--verify", "HEAD")))
        return {"repo": match.group(1), "branch": branch, "head": head, "dirty": digest.hexdigest()}


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
