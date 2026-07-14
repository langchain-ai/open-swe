"""In-memory state + git plumbing behind the fake GitHub and fake Slack.

These stores are the single source of truth that both the real agent code
(via the faked HTTP endpoints) and the mock UIs read from — so what Playwright
sees in the UI is exactly what the agent produced.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from e2e_env import BARE_REMOTE, BASE_BRANCH, OWNER, REPO

# --- Slack -----------------------------------------------------------------
# (channel, thread_ts) -> list of {user, text, ts, blocks, is_bot}
SLACK_MESSAGES: dict[tuple[str, str], list[dict[str, Any]]] = {}
_slack_seq = [1]


def next_slack_ts() -> str:
    _slack_seq[0] += 1
    return f"1700000000.{_slack_seq[0]:06d}"


_thread_seq = [0]


def new_thread_ts() -> str:
    """A globally-unique thread ts so every send maps to a fresh LangGraph thread
    (the in-mem store persists across restarts, so reused ids would carry state).
    Not reset by reset(), so back-to-back tests never collide."""
    _thread_seq[0] += 1
    return f"{int(time.time())}.{_thread_seq[0]:06d}"


def add_slack_message(
    channel: str, thread_ts: str, *, user: str, text: str, blocks: Any = None, is_bot: bool = False
) -> str:
    ts = next_slack_ts()
    actual_thread_ts = thread_ts or ts
    SLACK_MESSAGES.setdefault((channel, actual_thread_ts), []).append(
        {
            "user": user,
            "text": text,
            "ts": ts,
            "thread_ts": actual_thread_ts,
            "blocks": blocks,
            "is_bot": is_bot,
        }
    )
    return ts


def slack_thread(channel: str, thread_ts: str) -> list[dict[str, Any]]:
    return SLACK_MESSAGES.get((channel, thread_ts), [])


def slack_messages(channel: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for (message_channel, _thread_ts), thread_messages in SLACK_MESSAGES.items():
        if message_channel == channel:
            messages.extend(thread_messages)
    return sorted(messages, key=lambda message: message["ts"])


# --- GitHub ----------------------------------------------------------------
PULLS: list[dict[str, Any]] = []
_pr_seq = [0]


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def seed_bare_remote() -> None:
    """Create a fresh bare repo (the fake GitHub remote) with one commit on main."""
    if BARE_REMOTE.exists():
        shutil.rmtree(BARE_REMOTE)
    seed_work = BARE_REMOTE.parent / f"seed-{OWNER}-{REPO}"
    if seed_work.exists():
        shutil.rmtree(seed_work)

    seed_work.mkdir(parents=True)
    ident = ["-c", "user.email=seed@example.com", "-c", "user.name=Seed"]
    _git("init", "-b", BASE_BRANCH, str(seed_work))
    (seed_work / "README.md").write_text("# demo\n\nA tiny demo repo.\n")
    _git("add", "-A", cwd=seed_work)
    _git(*ident, "commit", "-m", "Initial commit", cwd=seed_work)
    _git("init", "--bare", "-b", BASE_BRANCH, str(BARE_REMOTE))
    _git("remote", "add", "origin", str(BARE_REMOTE), cwd=seed_work)
    _git("push", "origin", BASE_BRANCH, cwd=seed_work)
    shutil.rmtree(seed_work)


def _diff_files(base: str, head: str) -> list[dict[str, Any]]:
    """Compute changed files for a PR from the pushed branch in the bare remote."""
    try:
        out = _git("--git-dir", str(BARE_REMOTE), "diff", "--numstat", base, head)
    except subprocess.CalledProcessError:
        return []
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            adds, dels, name = parts
            files.append(
                {
                    "filename": name,
                    "additions": int(adds) if adds.isdigit() else 0,
                    "deletions": int(dels) if dels.isdigit() else 0,
                }
            )
    return files


def branch_exists(branch: str) -> bool:
    """Check whether a branch exists in the bare remote (the fake GitHub)."""
    try:
        _git("--git-dir", str(BARE_REMOTE), "rev-parse", "--verify", f"refs/heads/{branch}")
        return True
    except subprocess.CalledProcessError:
        return False


def create_pull(
    owner: str, repo: str, *, head: str, base: str, title: str, body: str, draft: bool
) -> dict[str, Any]:
    _pr_seq[0] += 1
    number = _pr_seq[0]
    files = _diff_files(base, head)
    pr = {
        "number": number,
        "owner": owner,
        "repo": repo,
        "head": head,
        "base": base,
        "title": title,
        "body": body,
        "draft": draft,
        "state": "open",
        "merged": False,
        "author": "jarvis-aeteq[bot]",
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }
    PULLS.append(pr)
    return pr


def find_pull(number: int) -> dict[str, Any] | None:
    return next((p for p in PULLS if p["number"] == number), None)


def reset() -> None:
    SLACK_MESSAGES.clear()
    PULLS.clear()
    _pr_seq[0] = 0
    seed_bare_remote()
