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
    """Leave the shared sandbox root holding exactly one checkout, named `repo`.

    The local sandbox root is shared by every thread in a run, and the turn
    checkpoint resolves the repo by globbing that root — so a checkout another
    spec cloned alongside `repo` would silently win the glob.
    """
    work = TMP / "work"
    for path in sorted(work.glob("*")):
        if path.is_dir():
            shutil.rmtree(path)
    _git("clone", str(BARE_REMOTE), str(work / "repo"))


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


def branch_sha(owner: str, repo: str, ref: str) -> str:
    """Resolve a ref to a real commit sha in the bare remote."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return ""
    try:
        return _git("--git-dir", str(remote), "rev-parse", ref).strip()
    except subprocess.CalledProcessError:
        return ""


def pull_diff(owner: str, repo: str, base: str, head: str) -> str:
    """The unified merge-base diff GitHub would serve for a PR."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return ""
    try:
        return _git("--git-dir", str(remote), "diff", f"{base}...{head}")
    except subprocess.CalledProcessError:
        return ""


def file_at_ref(owner: str, repo: str, ref: str, path: str) -> bytes | None:
    """A file's bytes at ``ref`` in the bare remote, or None when absent."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return None
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"{ref}:{path}"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


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
        # Real shas: the reviewer fetches and checks these out with real git,
        # and its diff range is computed from them.
        "head_sha": branch_sha(owner, repo, head) or f"{number:040x}",
        "base_sha": branch_sha(owner, repo, base),
        "base": base,
        "title": title,
        "body": body,
        "draft": draft,
        "state": "open",
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "check_runs": [],
        "statuses": [],
        "review_threads": [],
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


def find_pull(
    number: int, owner: str | None = None, repo: str | None = None
) -> dict[str, Any] | None:
    return next(
        (
            pull
            for pull in PULLS
            if pull["number"] == number
            and (owner is None or pull["owner"] == owner)
            and (repo is None or pull["repo"] == repo)
        ),
        None,
    )


def find_pull_by_sha(owner: str, repo: str, sha: str) -> dict[str, Any] | None:
    return next(
        (
            pull
            for pull in PULLS
            if pull["owner"] == owner and pull["repo"] == repo and pull["head_sha"] == sha
        ),
        None,
    )


def update_pull_health(number: int, values: dict[str, Any]) -> dict[str, Any] | None:
    pull = find_pull(number)
    if pull is None:
        return None
    allowed = {
        "draft",
        "state",
        "merged",
        "mergeable",
        "mergeable_state",
        "head_sha",
        "check_runs",
        "statuses",
        "review_threads",
    }
    pull.update({key: value for key, value in values.items() if key in allowed})
    return pull


def review_thread_graphql(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": thread.get("node_id", "PRRT_fixture"),
        "isResolved": bool(thread.get("is_resolved", False)),
        "path": thread.get("path", ""),
        "line": thread.get("line"),
        "originalLine": thread.get("original_line"),
        "comments": {
            "nodes": [
                {
                    "author": {"login": thread.get("author")},
                    "body": thread.get("body", ""),
                    "url": thread.get("url"),
                }
            ]
        },
    }


def published_threads_graphql(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    """The reviewer's own inline comments, as the review threads they become."""
    threads: dict[str, dict[str, Any]] = {}
    for comment in review_comments_for(owner, repo, number):
        root_id = comment["in_reply_to_id"] or comment["id"]
        thread = threads.get(str(root_id))
        node = {
            "databaseId": comment["id"],
            "author": {"login": comment["author"]},
            "authorAssociation": "MEMBER",
            "body": comment["body"],
            "createdAt": "2026-01-01T00:00:00Z",
        }
        if thread is None:
            threads[str(root_id)] = {
                "id": comment["node_id"],
                "isResolved": comment["resolved"],
                "isOutdated": False,
                "path": comment["path"],
                "line": comment["line"],
                "originalLine": comment["line"],
                "comments": {"nodes": [node]},
            }
            continue
        thread["comments"]["nodes"].append(node)
        thread["isResolved"] = thread["isResolved"] or comment["resolved"]
    return list(threads.values())


def pull_health_json(pull: dict[str, Any]) -> dict[str, Any]:
    return {
        key: pull[key]
        for key in (
            "number",
            "draft",
            "state",
            "merged",
            "mergeable",
            "mergeable_state",
            "head_sha",
            "check_runs",
            "statuses",
            "review_threads",
        )
    }


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


# --- GitHub review state (what the reviewer agent publishes) ---------------
# One entry per submitted review; ``comments`` are its inline comments.
REVIEWS: list[dict[str, Any]] = []
# Inline review comments across every review, in post order.
REVIEW_COMMENTS: list[dict[str, Any]] = []
# Issue-level PR comments (the reviewer's status comment lives here).
ISSUE_COMMENTS: list[dict[str, Any]] = []
# Check runs created/updated by the review check (`Open SWE Review`).
CHECK_RUNS: list[dict[str, Any]] = []
_review_seq = [0]
_comment_seq = [0]
_check_run_seq = [0]


def add_review(
    owner: str,
    repo: str,
    number: int,
    *,
    body: str,
    event: str,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record a submitted review and its inline comments."""
    _review_seq[0] += 1
    review_id = _review_seq[0]
    recorded: list[dict[str, Any]] = []
    for comment in comments:
        _comment_seq[0] += 1
        entry = {
            "id": _comment_seq[0],
            "owner": owner,
            "repo": repo,
            "pull_number": number,
            "review_id": review_id,
            "path": comment.get("path", ""),
            "line": comment.get("line"),
            "start_line": comment.get("start_line"),
            "side": comment.get("side", "RIGHT"),
            "body": comment.get("body", ""),
            "author": "open-swe[bot]",
            "in_reply_to_id": None,
            "node_id": f"PRRT_{_comment_seq[0]}",
            "resolved": False,
        }
        REVIEW_COMMENTS.append(entry)
        recorded.append(entry)
    review = {
        "id": review_id,
        "owner": owner,
        "repo": repo,
        "pull_number": number,
        "body": body,
        "event": event,
        "state": "COMMENTED" if event == "COMMENT" else event,
        "author": "open-swe[bot]",
        "comments": recorded,
    }
    REVIEWS.append(review)
    return review


def reviews_for(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    return [
        review
        for review in REVIEWS
        if review["owner"] == owner and review["repo"] == repo and review["pull_number"] == number
    ]


def review_comments_for(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    return [
        comment
        for comment in REVIEW_COMMENTS
        if comment["owner"] == owner
        and comment["repo"] == repo
        and comment["pull_number"] == number
    ]


def find_review_comment(comment_id: int) -> dict[str, Any] | None:
    return next((c for c in REVIEW_COMMENTS if c["id"] == comment_id), None)


def reply_to_review_comment(comment_id: int, body: str) -> dict[str, Any] | None:
    parent = find_review_comment(comment_id)
    if parent is None:
        return None
    _comment_seq[0] += 1
    reply = {
        **parent,
        "id": _comment_seq[0],
        "body": body,
        "in_reply_to_id": comment_id,
        "node_id": f"PRRT_{_comment_seq[0]}",
    }
    REVIEW_COMMENTS.append(reply)
    return reply


def resolve_review_thread(node_id: str) -> bool:
    """Mark every comment sharing a thread node id resolved."""
    matched = [c for c in REVIEW_COMMENTS if c["node_id"] == node_id]
    for comment in matched:
        comment["resolved"] = True
    return bool(matched)


def add_issue_comment(owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
    _comment_seq[0] += 1
    comment = {
        "id": _comment_seq[0],
        "owner": owner,
        "repo": repo,
        "issue_number": number,
        "body": body,
        "author": "open-swe[bot]",
    }
    ISSUE_COMMENTS.append(comment)
    return comment


def issue_comments_for(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    return [
        comment
        for comment in ISSUE_COMMENTS
        if comment["owner"] == owner
        and comment["repo"] == repo
        and comment["issue_number"] == number
    ]


def update_issue_comment(comment_id: int, body: str) -> dict[str, Any] | None:
    comment = next((c for c in ISSUE_COMMENTS if c["id"] == comment_id), None)
    if comment is not None:
        comment["body"] = body
    return comment


def delete_issue_comment(comment_id: int) -> bool:
    for index, comment in enumerate(ISSUE_COMMENTS):
        if comment["id"] == comment_id:
            del ISSUE_COMMENTS[index]
            return True
    return False


def create_check_run(owner: str, repo: str, payload: dict[str, Any]) -> dict[str, Any]:
    _check_run_seq[0] += 1
    check_run = {
        "id": _check_run_seq[0],
        "owner": owner,
        "repo": repo,
        "name": payload.get("name", ""),
        "head_sha": payload.get("head_sha", ""),
        "status": payload.get("status", "queued"),
        "conclusion": payload.get("conclusion"),
        "details_url": payload.get("details_url", ""),
        "output": payload.get("output", {}),
    }
    CHECK_RUNS.append(check_run)
    return check_run


def update_check_run(check_run_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    check_run = next((run for run in CHECK_RUNS if run["id"] == check_run_id), None)
    if check_run is None:
        return None
    for key in ("status", "conclusion", "output", "details_url"):
        if key in payload:
            check_run[key] = payload[key]
    return check_run


def review_state(owner: str, repo: str, number: int) -> dict[str, Any]:
    """Everything the reviewer published for one PR, for spec assertions."""
    return {
        "reviews": reviews_for(owner, repo, number),
        "review_comments": review_comments_for(owner, repo, number),
        "issue_comments": issue_comments_for(owner, repo, number),
        "check_runs": [run for run in CHECK_RUNS if run["owner"] == owner and run["repo"] == repo],
    }


def reset() -> None:
    SLACK_MESSAGES.clear()
    PULLS.clear()
    SNAPSHOTS.clear()
    DELETED_SNAPSHOTS.clear()
    REVIEWS.clear()
    REVIEW_COMMENTS.clear()
    ISSUE_COMMENTS.clear()
    CHECK_RUNS.clear()
    REPO_PRIVATE[0] = False
    _pr_seq[0] = 0
    _review_seq[0] = 0
    _comment_seq[0] = 0
    _check_run_seq[0] = 0
    seed_bare_remotes()
