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
CODE_CHANNELS: dict[str, dict[str, Any]] = {}
_slack_seq = [1]
_code_channel_seq = [0]


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


# --- Slack streaming messages ----------------------------------------------
# A streaming message is an ordinary message that keeps being appended to:
# `chat.startStream` posts it, `chat.appendStream` adds chunks, `chat.stopStream`
# closes it. `markdown_text` chunks accumulate into its text; `task_update`
# chunks are cards the client renders beside that text, replaced in place by id.
# Streaming into a channel without `thread_ts` posts at channel level, which
# Slack allows only where the whole channel is one session.
STREAMS: dict[str, dict[str, Any]] = {}


def start_stream(
    channel: str,
    *,
    thread_ts: str = "",
    chunks: list[dict[str, Any]] | None = None,
    task_display_mode: str = "timeline",
    recipient_user_id: str = "",
) -> dict[str, Any]:
    # A channel-level stream (no `thread_ts`) posts its own top-level message.
    ts = add_slack_message(channel, thread_ts, user="BOT", text="", is_bot=True)
    stream = {
        "ts": ts,
        "channel": channel,
        "thread_ts": thread_ts,
        "task_display_mode": task_display_mode,
        "recipient_user_id": recipient_user_id,
        "text": "",
        "tasks": {},
        "task_order": [],
        # What arrived, in order, so tests can assert that the agent's words
        # reach the channel before the cards that explain them.
        "timeline": [],
        "chunk_count": 0,
        "state": "streaming",
        "session_status": "",
    }
    STREAMS[ts] = stream
    apply_stream_chunks(ts, chunks or [])
    return stream


def apply_stream_chunks(ts: str, chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    stream = STREAMS.get(ts)
    if stream is None:
        return None
    for chunk in chunks:
        stream["chunk_count"] += 1
        kind = chunk.get("type")
        if kind == "markdown_text":
            text = str(chunk.get("text") or "")
            stream["text"] += text
            stream["timeline"].append({"kind": "text", "text": text})
        elif kind == "task_update":
            task_id = str(chunk.get("id") or "")
            if task_id and task_id not in stream["tasks"]:
                stream["task_order"].append(task_id)
            if task_id:
                stream["tasks"][task_id] = chunk
                stream["timeline"].append(
                    {
                        "kind": "task",
                        "id": task_id,
                        "title": chunk.get("title"),
                        "status": chunk.get("status"),
                    }
                )
    _render_stream(stream)
    return stream


def stop_stream(
    ts: str, chunks: list[dict[str, Any]] | None = None, *, session_status: str = ""
) -> dict[str, Any] | None:
    stream = STREAMS.get(ts)
    if stream is None:
        return None
    if stream["state"] != "streaming":
        return {"error": "message_not_in_streaming_state"}
    apply_stream_chunks(ts, chunks or [])
    stream["state"] = "stopped"
    stream["session_status"] = session_status
    # Slack takes the session out of its loading state when the stream that put
    # it there ends, without waiting for a separate status call.
    if session_status:
        update_code_channel(stream["channel"], status=session_status)
    return stream


def _render_stream(stream: dict[str, Any]) -> None:
    """Write the stream's current content onto the message it is streaming into."""
    cards = "\n".join(
        f"[{stream['tasks'][task_id].get('status')}] {stream['tasks'][task_id].get('title')}"
        for task_id in stream["task_order"]
        if task_id in stream["tasks"]
    )
    update_slack_message(
        stream["channel"],
        str(stream["ts"]),
        text="\n".join(part for part in (stream["text"], cards) if part),
    )


def streams(channel: str = "") -> list[dict[str, Any]]:
    return [stream for stream in STREAMS.values() if not channel or stream["channel"] == channel]


def slack_messages(channel: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for (message_channel, _thread_ts), thread_messages in SLACK_MESSAGES.items():
        if message_channel == channel:
            messages.extend(thread_messages)
    return sorted(messages, key=lambda message: message["ts"])


def slack_channels() -> list[str]:
    return list(dict.fromkeys(channel for channel, _thread_ts in SLACK_MESSAGES))


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


def create_code_channel(payload: dict[str, Any]) -> dict[str, Any]:
    # Globally unique, like `new_thread_ts`: a code channel's agent thread id is
    # derived from its channel id, and the store outlives the server, so a reused
    # id would hand a fresh channel someone else's session.
    _code_channel_seq[0] += 1
    channel_id = f"C_CODE_{int(time.time())}_{_code_channel_seq[0]}"
    channel = {
        "id": channel_id,
        "name": str(payload.get("name") or "Open SWE task"),
        "session_id": str(payload.get("session_id") or ""),
        "origin_channel_id": str(payload.get("origin_channel_id") or ""),
        "origin_message_ts": str(payload.get("origin_message_ts") or ""),
        "status": "active",
        "context_bar_items": [],
        "commands": [],
        "views": [],
        "archived": False,
    }
    CODE_CHANNELS[channel_id] = channel
    return channel


def update_code_channel(channel_id: str, **values: Any) -> dict[str, Any] | None:
    channel = CODE_CHANNELS.get(channel_id)
    if channel is not None:
        channel.update(values)
    return channel


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
        "head_sha": f"{number:040x}",
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
        "reviews": [],
        "review_decision": "REVIEW_REQUIRED",
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
        "reviews",
        "review_decision",
    }
    pull.update({key: value for key, value in values.items() if key in allowed})
    return pull


def review_thread_graphql(thread: dict[str, Any]) -> dict[str, Any]:
    comments = thread.get("comments")
    if not isinstance(comments, list):
        comments = [
            {
                "author": thread.get("author"),
                "body": thread.get("body", ""),
                "url": thread.get("url"),
            }
        ]
    return {
        "isResolved": bool(thread.get("is_resolved", False)),
        "isOutdated": bool(thread.get("is_outdated", False)),
        "path": thread.get("path", ""),
        "line": thread.get("line"),
        "originalLine": thread.get("original_line"),
        "comments": {
            "nodes": [
                {
                    "author": {"login": comment.get("author")},
                    "body": comment.get("body", ""),
                    "url": comment.get("url"),
                }
                for comment in comments
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def check_graphql(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "__typename": "CheckRun",
        "name": check.get("name", ""),
        "status": str(check.get("status", "")).upper(),
        "conclusion": str(check.get("conclusion", "")).upper() or None,
        "detailsUrl": check.get("details_url"),
        "isRequired": check.get("required", False),
    }


def status_graphql(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "__typename": "StatusContext",
        "context": status.get("context", ""),
        "state": str(status.get("state", "")).upper(),
        "targetUrl": status.get("target_url"),
        "isRequired": status.get("required", False),
    }


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
            "reviews",
            "review_decision",
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


def reset() -> None:
    SLACK_MESSAGES.clear()
    CODE_CHANNELS.clear()
    STREAMS.clear()
    PULLS.clear()
    SNAPSHOTS.clear()
    DELETED_SNAPSHOTS.clear()
    REPO_PRIVATE[0] = False
    _pr_seq[0] = 0
    seed_bare_remotes()
