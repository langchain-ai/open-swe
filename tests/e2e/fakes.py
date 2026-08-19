"""In-memory state + git plumbing behind the fake GitHub and fake Slack.

These stores are the single source of truth that both the real agent code
(via the faked HTTP endpoints) and the mock UIs read from — so what Playwright
sees in the UI is exactly what the agent produced.
"""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from e2e_env import (
    BARE_REMOTE,
    BASE_BRANCH,
    OWNER,
    REPO,
    SECOND_BARE_REMOTE,
    SECOND_OWNER,
    SECOND_REPO,
    TMP,
)

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


def slack_message(channel: str, thread_ts: str, message_ts: str) -> dict[str, Any] | None:
    return next(
        (message for message in slack_thread(channel, thread_ts) if message["ts"] == message_ts),
        None,
    )


def update_slack_message(
    channel: str, message_ts: str, *, text: str, blocks: Any = None
) -> dict[str, Any] | None:
    for (message_channel, _thread_ts), thread_messages in SLACK_MESSAGES.items():
        if message_channel != channel:
            continue
        for message in thread_messages:
            if message["ts"] != message_ts:
                continue
            message["text"] = text
            if blocks is not None:
                message["blocks"] = blocks
            return message
    return None


# --- GitHub ----------------------------------------------------------------
PULLS: list[dict[str, Any]] = []
REPO_PRIVATE = [False]
_pr_seq = [0]
_REMOTES = {
    (OWNER, REPO): BARE_REMOTE,
    (SECOND_OWNER, SECOND_REPO): SECOND_BARE_REMOTE,
}


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def seed_bare_remotes() -> None:
    """Create fresh fake GitHub remotes with one commit on main."""
    for (owner, repo), remote in _REMOTES.items():
        if remote.exists():
            shutil.rmtree(remote)
        seed_work = remote.parent / f"seed-{owner}-{repo}"
        if seed_work.exists():
            shutil.rmtree(seed_work)

        seed_work.mkdir(parents=True)
        ident = ["-c", "user.email=seed@example.com", "-c", "user.name=Seed"]
        _git("init", "-b", BASE_BRANCH, str(seed_work))
        (seed_work / "README.md").write_text(f"# {repo}\n\nA tiny demo repo.\n")
        _git("add", "-A", cwd=seed_work)
        _git(*ident, "commit", "-m", "Initial commit", cwd=seed_work)
        _git("init", "--bare", "-b", BASE_BRANCH, str(remote))
        _git("remote", "add", "origin", str(remote), cwd=seed_work)
        _git("push", "origin", BASE_BRANCH, cwd=seed_work)
        shutil.rmtree(seed_work)


def seed_sandbox_repo() -> None:
    repo = TMP / "work" / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    _git("clone", str(BARE_REMOTE), str(repo))


def _diff_files(owner: str, repo: str, base: str, head: str) -> list[dict[str, Any]]:
    """Compute changed files for a PR from the pushed branch in the bare remote."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return []
    try:
        out = _git("--git-dir", str(remote), "diff", "--numstat", base, head)
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


def branch_exists(owner: str, repo: str, branch: str) -> bool:
    """Check whether a branch exists in the bare remote (the fake GitHub)."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return False
    try:
        _git("--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}")
        return True
    except subprocess.CalledProcessError:
        return False


def create_pull(
    owner: str, repo: str, *, head: str, base: str, title: str, body: str, draft: bool
) -> dict[str, Any]:
    _pr_seq[0] += 1
    number = _pr_seq[0]
    files = _diff_files(owner, repo, base, head)
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
        "author": "open-swe[bot]",
        "created_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5 * 24 * 60 * 60)
        ),
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }
    PULLS.append(pr)
    return pr


def find_pull(number: int) -> dict[str, Any] | None:
    return next((p for p in PULLS if p["number"] == number), None)


def set_repo_private(value: bool) -> None:
    REPO_PRIVATE[0] = value


def repo_private() -> bool:
    return REPO_PRIVATE[0]


# --- LangSmith snapshots ---------------------------------------------------
# Captures the environment tools asked for: {"snapshot_id", "name", "sandbox_id"}.
# The E2E sandbox is the local provider, so there is no real snapshot service —
# this store stands in for it and is what the specs assert on.
SNAPSHOTS: list[dict[str, Any]] = []
DELETED_SNAPSHOTS: list[str] = []
_snapshot_seq = [0]


def record_snapshot_capture(sandbox_id: str, name: str) -> str:
    _snapshot_seq[0] += 1
    snapshot_id = f"snap-{_snapshot_seq[0]}"
    SNAPSHOTS.append({"snapshot_id": snapshot_id, "name": name, "sandbox_id": sandbox_id})
    return snapshot_id


def record_snapshot_delete(snapshot_id: str) -> None:
    DELETED_SNAPSHOTS.append(snapshot_id)


def reset() -> None:
    SLACK_MESSAGES.clear()
    PULLS.clear()
    SNAPSHOTS.clear()
    DELETED_SNAPSHOTS.clear()
    REPO_PRIVATE[0] = False
    _pr_seq[0] = 0
    seed_bare_remotes()
