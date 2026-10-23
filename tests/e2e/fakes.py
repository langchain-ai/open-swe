"""In-memory state + git plumbing behind the fake GitHub and fake Slack.

These stores are the single source of truth that both the real agent code
(via the faked HTTP endpoints) and the mock UIs read from — so what Playwright
sees in the UI is exactly what the agent produced.
"""

import copy
import os
import shutil
import subprocess
import time
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from e2e_env import (
    BARE_REMOTE,
    BASE_BRANCH,
    BASE_URL,
    OWNER,
    REPO,
    SECOND_BARE_REMOTE,
    SECOND_OWNER,
    SECOND_REPO,
    TEST_USERS,
    TMP,
)

# --- Slack -----------------------------------------------------------------
# (channel, thread_ts) -> list of {user, text, ts, blocks, is_bot}
SLACK_MESSAGES: dict[tuple[str, str], list[dict[str, Any]]] = {}
EPHEMERALS: list[dict[str, Any]] = []
VIEWS: list[dict[str, Any]] = []
CODE_CHANNELS: dict[str, dict[str, Any]] = {}
_slack_seq = [1]
_slack_epoch = int(time.time())
_code_channel_seq = [0]


def next_slack_ts() -> str:
    """A globally-unique Slack timestamp, for a message or a thread.

    Slack event dedupe keys a delivery on ``channel:ts`` in the LangGraph store,
    and thread ids are derived from the thread's ts — both outlive the process,
    so a counter restarting at the same value would make a rerun's messages look
    like redeliveries and its threads carry the previous run's state. Seeding
    the second from the clock keeps every process in its own range, and reset()
    leaves the counter alone so back-to-back tests never collide either."""
    _slack_seq[0] += 1
    return f"{_slack_epoch}.{_slack_seq[0]:06d}"


def add_slack_message(
    channel: str,
    thread_ts: str,
    *,
    user: str,
    text: str,
    blocks: Any = None,
    is_bot: bool = False,
    reply_broadcast: bool = False,
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
            **({"subtype": "bot_message", "bot_id": "B_OPEN_SWE"} if is_bot else {}),
            "reply_broadcast": reply_broadcast,
        }
    )
    return ts


def delete_slack_message(channel: str, message_ts: str) -> bool:
    for (message_channel, _thread_ts), thread_messages in SLACK_MESSAGES.items():
        if message_channel != channel:
            continue
        for index, message in enumerate(thread_messages):
            if message["ts"] == message_ts:
                del thread_messages[index]
                return True
    return False


def add_ephemeral(channel: str, user: str, text: str) -> str:
    """Record an ephemeral reply. Open SWE tells a single clicker things this way
    (why a vote was refused, for one), so a test has to be able to read them."""
    ts = next_slack_ts()
    EPHEMERALS.append({"channel": channel, "user": user, "text": text, "ts": ts})
    return ts


def slack_thread(channel: str, thread_ts: str) -> list[dict[str, Any]]:
    return SLACK_MESSAGES.get((channel, thread_ts), [])


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
    _code_channel_seq[0] += 1
    channel_id = f"C_CODE_{_code_channel_seq[0]}"
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
MERGE_METHOD_FLAGS = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge")
REPO_MERGE_METHODS: dict[tuple[str, str], dict[str, bool]] = {}
_pr_seq = [0]
# GitHub's record ids are global and only grow; the mirror keys rows on them.
_github_id_seq = [0]
_REMOTES = {
    (OWNER, REPO): BARE_REMOTE,
    (SECOND_OWNER, SECOND_REPO): SECOND_BARE_REMOTE,
}
# The harness's own pushes run inside its event loop, so the hook calling back
# into it would wait on itself; it publishes those pushes directly instead.
_HARNESS_PUSH_ENV = "E2E_HARNESS_PUSH"
_POST_RECEIVE_HOOK = f"""#!/bin/sh
[ -n "${_HARNESS_PUSH_ENV}" ] && exit 0
curl -s -m 10 -X POST "{BASE_URL}/control/pushed" >/dev/null 2>&1 || true
"""


def next_github_id() -> int:
    _github_id_seq[0] += 1
    return _github_id_seq[0]


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, _HARNESS_PUSH_ENV: "1"},
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
        hook = remote / "hooks" / "post-receive"
        hook.write_text(_POST_RECEIVE_HOOK)
        hook.chmod(0o755)
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


_FILE_STATUSES = {"A": "added", "D": "removed", "M": "modified"}


def _diff_files(owner: str, repo: str, base: str, head: str) -> list[dict[str, Any]]:
    """Compute changed files for a PR from the pushed branch in the bare remote.

    The three ``git diff`` runs list the files in the same order, so they zip together.
    """
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return []
    diff = ["--git-dir", str(remote), "diff", "--no-renames", base, head]
    try:
        numstat = _git(*diff, "--numstat").splitlines()
        if not numstat:
            return []
        statuses = _git(*diff, "--name-status").splitlines()
        patches = _git(*diff).split("\ndiff --git ")
    except subprocess.CalledProcessError:
        return []
    files = []
    for counts, status, patch in zip(numstat, statuses, patches, strict=True):
        adds, dels, name = counts.split("\t", 2)
        files.append(
            {
                "filename": name,
                "status": _FILE_STATUSES.get(status.split("\t", 1)[0], "modified"),
                "additions": int(adds) if adds.isdigit() else 0,
                "deletions": int(dels) if dels.isdigit() else 0,
                "patch": _hunks(patch),
            }
        )
    return files


def _hunks(file_diff: str) -> str | None:
    """One file's unified diff, hunks only, as GitHub's ``patch`` field carries it.

    ``None`` for a blob git produced no textual hunks for (a binary file), which
    is how the real API reports one — and what callers key "no readable diff" on.
    """
    lines = file_diff.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("@@")), None)
    return "\n".join(lines[start:]) if start is not None else None


def _branch_tip(owner: str, repo: str, branch: str) -> str:
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return ""
    try:
        return _git(
            "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def branch_exists(owner: str, repo: str, branch: str) -> bool:
    """Check whether a branch exists in the bare remote (the fake GitHub)."""
    return bool(_branch_tip(owner, repo, branch))


def push_branch(owner: str, repo: str, branch: str, files: dict[str, str]) -> None:
    """Push ``branch`` off the base branch with ``files`` written in one commit."""
    remote = _REMOTES[(owner, repo)]
    work = remote.parent / f"push-{owner}-{repo}-{branch.replace('/', '-')}"
    if work.exists():
        shutil.rmtree(work)
    ident = ["-c", "user.email=seed@example.com", "-c", "user.name=Seed"]
    _git("clone", "--branch", BASE_BRANCH, str(remote), str(work))
    _git("checkout", "-b", branch, cwd=work)
    for path, content in files.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git("add", "-A", cwd=work)
    _git(*ident, "commit", "-m", f"Seed {branch}", cwd=work)
    _git("push", "--force", "origin", branch, cwd=work)
    shutil.rmtree(work)


def commit_to_base(owner: str, repo: str, files: dict[str, str]) -> None:
    """Commit ``files`` straight onto the base branch, as a merged change would land."""
    remote = _REMOTES[(owner, repo)]
    work = remote.parent / f"base-{owner}-{repo}"
    if work.exists():
        shutil.rmtree(work)
    ident = ["-c", "user.email=seed@example.com", "-c", "user.name=Seed"]
    _git("clone", "--branch", BASE_BRANCH, str(remote), str(work))
    for path, content in files.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git("add", "-A", cwd=work)
    _git(*ident, "commit", "-m", "Seed base files", cwd=work)
    _git("push", "origin", BASE_BRANCH, cwd=work)
    shutil.rmtree(work)


def resolve_ref(owner: str, repo: str, ref: str) -> str:
    """A git revision for ``ref``; a pull's synthetic head SHA maps to its pushed branch."""
    pull = find_pull_by_sha(owner, repo, ref)
    if pull is not None:
        return pull["branch_tip"] or pull["head"]
    return ref


def base_sha(pull: dict[str, Any]) -> str:
    return _branch_tip(pull["owner"], pull["repo"], pull["base"])


def commit_author_emails(owner: str, repo: str, path: str, ref: str) -> list[str]:
    """Author emails of the commits touching ``path`` at ``ref``, newest first."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return []
    try:
        out = _git(
            "--git-dir",
            str(remote),
            "log",
            "--format=%ae",
            resolve_ref(owner, repo, ref),
            "--",
            path,
        )
    except subprocess.CalledProcessError:
        return []
    return [line for line in out.splitlines() if line]


def file_at_ref(owner: str, repo: str, path: str, ref: str) -> str | None:
    """The file's contents at ``ref``, or ``None`` when it does not exist there."""
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return None
    try:
        return _git("--git-dir", str(remote), "show", f"{resolve_ref(owner, repo, ref)}:{path}")
    except subprocess.CalledProcessError:
        return None


def merge_base(owner: str, repo: str, base: str, head: str) -> str | None:
    remote = _REMOTES.get((owner, repo))
    if remote is None:
        return None
    try:
        return _git(
            "--git-dir",
            str(remote),
            "merge-base",
            resolve_ref(owner, repo, base),
            resolve_ref(owner, repo, head),
        ).strip()
    except subprocess.CalledProcessError:
        return None


def compare_files(owner: str, repo: str, base: str, head: str) -> list[dict[str, Any]]:
    return _diff_files(owner, repo, resolve_ref(owner, repo, base), resolve_ref(owner, repo, head))


def pull_diff(pull: dict[str, Any]) -> str:
    """The pull request's unified diff, as ``Accept: application/vnd.github.diff`` returns it."""
    remote = _REMOTES.get((pull["owner"], pull["repo"]))
    if remote is None:
        return ""
    try:
        return _git("--git-dir", str(remote), "diff", f"{pull['base']}...{pull['head']}")
    except subprocess.CalledProcessError:
        return ""


def pull_commits(pull: dict[str, Any]) -> list[dict[str, Any]]:
    """The commits on the pull request's branch, oldest first, as ``/pulls/{n}/commits`` lists them."""
    remote = _REMOTES.get((pull["owner"], pull["repo"]))
    if remote is None or not pull["branch_tip"]:
        return []
    try:
        out = _git(
            "--git-dir",
            str(remote),
            "log",
            "--reverse",
            "--format=%H%x00%s%x00%aI%x00%ae",
            f"{pull['base']}..{pull['branch_tip']}",
        )
    except subprocess.CalledProcessError:
        return []
    logins = {user["email"]: user["login"] for user in TEST_USERS}
    commits = []
    for line in out.splitlines():
        sha, message, date, email = line.split("\x00")
        login = logins.get(email)
        commits.append(
            {
                "sha": sha,
                "html_url": f"https://github.com/{pull['owner']}/{pull['repo']}/commit/{sha}",
                "commit": {"message": message, "author": {"date": date, "email": email}},
                "author": github_user(login) if login else None,
            }
        )
    return commits


def pulls() -> list[dict[str, Any]]:
    return PULLS


def advance_heads() -> None:
    """Re-point each open pull request whose branch was pushed at the new head.

    GitHub does this on every push, and the new head starts with no checks.
    """
    for pull in PULLS:
        if pull["state"] != "open" or pull["merged"]:
            continue
        tip = _branch_tip(pull["owner"], pull["repo"], pull["head"])
        if not tip or tip == pull["branch_tip"]:
            continue
        files = _diff_files(pull["owner"], pull["repo"], pull["base"], pull["head"])
        pull.update(
            branch_tip=tip,
            head_sha=tip,
            files=files,
            additions=sum(f["additions"] for f in files),
            deletions=sum(f["deletions"] for f in files),
            check_runs=[],
            statuses=[],
            check_suite_id=next_github_id(),
        )


def snapshot() -> dict[tuple[str, str, int], dict[str, Any]]:
    """Every pull request as it stands now, to tell later what changed."""
    return {pull_key(pull): copy.deepcopy(pull) for pull in PULLS}


def pull_key(pull: dict[str, Any]) -> tuple[str, str, int]:
    return (pull["owner"], pull["repo"], pull["number"])


def touch(pull: dict[str, Any]) -> None:
    """Advance ``updated_at`` as GitHub does on every change, never backwards."""
    pull["updated_at"] = max(github_timestamp(), pull["updated_at"])


def github_timestamp(offset_seconds: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds))


def create_pull(
    owner: str,
    repo: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool,
    author: str = "open-swe[bot]",
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    _pr_seq[0] += 1
    number = _pr_seq[0]
    files = _diff_files(owner, repo, base, head)
    pr = {
        "id": next_github_id(),
        "number": number,
        "owner": owner,
        "repo": repo,
        "head": head,
        "head_sha": f"{number:040x}",
        "branch_tip": _branch_tip(owner, repo, head),
        "base": base,
        "title": title,
        "body": body,
        "draft": draft,
        "state": "open",
        "merged": False,
        "merged_at": None,
        "closed_at": None,
        "mergeable": True,
        "mergeable_state": "clean",
        "check_suite_id": next_github_id(),
        "check_runs": [],
        "statuses": [],
        "resolved_threads": [],
        "seeded_comment_ids": [],
        "reviews": [],
        "review_comments": [],
        "standalone_comment_posts": [],
        "issue_comments": [],
        # GitHub's verdict under branch protection; the fake repos have none.
        "review_decision": None,
        "requested_reviewers": [],
        "author": author,
        "merge_method": None,
        "created_at": created_at or github_timestamp(-5 * 24 * 60 * 60),
        "updated_at": updated_at or github_timestamp(),
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
            for pull in pulls()
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
            for pull in pulls()
            if pull["owner"] == owner and pull["repo"] == repo and pull["head_sha"] == sha
        ),
        None,
    )


def pull_node_id(pull: dict[str, Any]) -> str:
    return f"PR_node_{pull['owner']}_{pull['repo']}_{pull['number']}"


def mark_pull_ready(node_id: str) -> dict[str, Any] | None:
    pull = next((pull for pull in PULLS if pull_node_id(pull) == node_id), None)
    if pull is None:
        return None
    pull["draft"] = False
    return pull


def set_pull_state(pull: dict[str, Any], state: str) -> None:
    pull["state"] = state
    pull["closed_at"] = github_timestamp() if state == "closed" else None


def update_pull_health(number: int, values: dict[str, Any]) -> dict[str, Any] | None:
    """Set what a spec says GitHub holds for the pull request.

    Reviews, threads, checks and statuses become records with ids, as GitHub
    lists them; one left as it was keeps its id, so setting it again is no change.
    """
    pull = find_pull(number)
    if pull is None:
        return None
    pull.update(
        {
            key: value
            for key, value in values.items()
            if key
            in {"draft", "merged", "mergeable", "mergeable_state", "head_sha", "review_decision"}
        }
    )
    if "state" in values:
        set_pull_state(pull, str(values["state"]))
    if values.get("merged") and not pull["merged_at"]:
        pull["merged_at"] = github_timestamp()
    if isinstance(values.get("check_runs"), list):
        pull["check_runs"] = [_seeded_check_run(pull, check) for check in values["check_runs"]]
    if isinstance(values.get("statuses"), list):
        pull["statuses"] = [_seeded_status(pull, status) for status in values["statuses"]]
    if isinstance(values.get("reviews"), list):
        pull["reviews"] = [_seeded_review(pull, review) for review in values["reviews"]]
    if isinstance(values.get("review_threads"), list):
        _seed_review_threads(pull, values["review_threads"])
    return pull


def _seeded_check_run(pull: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "name": str(check.get("name") or ""),
        "head_sha": str(check.get("head_sha") or pull["head_sha"]),
        "status": str(check.get("status") or "completed"),
        "conclusion": check.get("conclusion"),
        "details_url": check.get("details_url"),
    }
    same = next(
        (run for run in pull["check_runs"] if all(run.get(k) == v for k, v in fields.items())),
        None,
    )
    # A changed check is a rerun: a new id in the same suite, which GitHub shows instead.
    return {
        **fields,
        "id": same["id"] if same else next_github_id(),
        "required": bool(check.get("required", False)),
    }


def check_run_json(pull: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    url = f"https://github.com/{pull['owner']}/{pull['repo']}/runs/{run['id']}"
    return {
        "id": run["id"],
        "name": run["name"],
        "head_sha": run["head_sha"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "details_url": run["details_url"] or url,
        "html_url": url,
        "app": {"id": 1, "slug": "fake-ci"},
        "check_suite": {"id": pull["check_suite_id"], "head_branch": pull["head"]},
        "started_at": pull["created_at"],
        "completed_at": github_timestamp() if run["status"] == "completed" else None,
    }


def _seeded_status(pull: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "context": str(status.get("context") or ""),
        "state": str(status.get("state") or "pending"),
        "target_url": status.get("target_url"),
        "description": status.get("description"),
    }
    same = next(
        (old for old in pull["statuses"] if all(old.get(k) == v for k, v in fields.items())),
        None,
    )
    return {
        **fields,
        "updated_at": same["updated_at"] if same else github_timestamp(),
        "required": bool(status.get("required", False)),
    }


def _seeded_review(pull: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    user = review.get("user")
    author = str(user.get("login") if isinstance(user, dict) else review.get("author") or "")
    state = str(review.get("state") or "").upper()
    body = str(review.get("body") or "")
    same = next(
        (
            old
            for old in pull["reviews"]
            if (old["author"], old["state"], old["body"]) == (author, state, body)
        ),
        None,
    )
    return same or _review_record(
        pull,
        author=author,
        state=state,
        commit_id=pull["head_sha"],
        body=body,
        url=review.get("url") if isinstance(review.get("url"), str) else None,
    )


def _seed_review_threads(pull: dict[str, Any], threads: list[dict[str, Any]]) -> None:
    """Replace the spec's earlier inline threads with ``threads``, as review comments."""
    seeded = set(pull["seeded_comment_ids"])
    pull["review_comments"] = [c for c in pull["review_comments"] if c["id"] not in seeded]
    pull["resolved_threads"] = [r for r in pull["resolved_threads"] if r not in seeded]
    pull["seeded_comment_ids"] = []
    for thread in threads:
        comments = thread.get("comments")
        if not isinstance(comments, list):
            comments = [thread]
        line = None if thread.get("is_outdated") else thread.get("line")
        root: dict[str, Any] | None = None
        for comment in comments:
            added = _add_review_comment(
                pull,
                review_id=None,
                commit_id=pull["head_sha"],
                author=str(comment.get("author") or ""),
                payload={
                    "body": comment.get("body"),
                    "path": thread.get("path"),
                    "line": line,
                    "original_line": thread.get("original_line") or thread.get("line"),
                },
                url=comment.get("url") if isinstance(comment.get("url"), str) else None,
                in_reply_to=root["id"] if root else None,
            )
            pull["seeded_comment_ids"].append(added["id"])
            root = root or added
        if root is not None and thread.get("is_resolved"):
            pull["resolved_threads"].append(root["id"])


def thread_node_id(root: dict[str, Any]) -> str:
    return f"PRRT_node_{root['id']}"


def set_thread_resolved(node_id: str, resolved: bool) -> dict[str, Any] | None:
    """GraphQL ``resolveReviewThread``/``unresolveReviewThread``."""
    found = _comment_by(lambda comment: thread_node_id(comment) == node_id)
    if found is None or found[1].get("in_reply_to_id") is not None:
        return None
    pull, root = found
    others = [r for r in pull["resolved_threads"] if r != root["id"]]
    pull["resolved_threads"] = [*others, root["id"]] if resolved else others
    return pull


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
        **{
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
                "reviews",
                "review_decision",
            )
        },
        "review_threads": review_threads_graphql(pull, ""),
    }


# Accounts the backend already knows under another id than the hash below.
_GITHUB_IDS = {"octocat": 1003, **{user["login"]: int(user["github_id"]) for user in TEST_USERS}}


def github_user_id(login: str) -> int:
    """The account id GitHub sends with every user; stable per login across runs."""
    return _GITHUB_IDS.get(login) or zlib.crc32(login.encode())


def github_user(login: str) -> dict[str, Any]:
    return {
        "login": login,
        "id": github_user_id(login),
        "avatar_url": "",
        "type": "Bot" if login.endswith("[bot]") else "User",
    }


def review_rest_json(review: dict[str, Any]) -> dict[str, Any]:
    """A review as the REST ``/pulls/{n}/reviews`` list and webhooks describe it."""
    return {
        "id": review["id"],
        "node_id": review["node_id"],
        "user": github_user(review["author"]),
        "state": review["state"],
        "body": review["body"],
        "html_url": review["html_url"],
        "commit_id": review["commit_id"],
        "submitted_at": review["submitted_at"],
    }


def set_repo_merge_methods(owner: str, repo: str, flags: dict[str, bool]) -> dict[str, bool]:
    resolved = {flag: bool(flags.get(flag, True)) for flag in MERGE_METHOD_FLAGS}
    REPO_MERGE_METHODS[(owner, repo)] = resolved
    return resolved


def repo_merge_methods(owner: str, repo: str) -> dict[str, bool]:
    return REPO_MERGE_METHODS.get((owner, repo)) or dict.fromkeys(MERGE_METHOD_FLAGS, True)


def set_repo_private(value: bool) -> None:
    REPO_PRIVATE[0] = value


def repo_private() -> bool:
    return REPO_PRIVATE[0]


# --- LangSmith snapshots ---------------------------------------------------
# Captures the workspace tools asked for: {"snapshot_id", "name", "sandbox_id"}.
# The E2E sandbox is the local provider, so there is no real snapshot service —
# this store stands in for it and is what the specs assert on.
SNAPSHOTS: list[dict[str, Any]] = []
DELETED_SNAPSHOTS: list[str] = []
_snapshot_seq = [0]


def record_snapshot_capture(sandbox_id: str, name: str, tag: str | None = None) -> str:
    _snapshot_seq[0] += 1
    snapshot_id = f"snap-{_snapshot_seq[0]}"
    SNAPSHOTS.append(
        {"snapshot_id": snapshot_id, "name": name, "tag": tag, "sandbox_id": sandbox_id}
    )
    return snapshot_id


def record_snapshot_delete(snapshot_id: str) -> None:
    DELETED_SNAPSHOTS.append(snapshot_id)


# Repository permission per GitHub login, as ``/collaborators/{u}/permission``
# reports it. Anyone absent reads as "read", which is what fails the vote gate.
COLLABORATOR_PERMISSIONS: dict[str, str] = {}


def collaborator_permission(login: str) -> str:
    return COLLABORATOR_PERMISSIONS.get(login.lower(), "read")


def set_collaborator_permission(login: str, permission: str) -> None:
    COLLABORATOR_PERMISSIONS[login.lower()] = permission


_REVIEW_STATES = {
    "APPROVE": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENTED",
}


def _review_record(
    pull: dict[str, Any],
    *,
    author: str,
    state: str,
    commit_id: str,
    body: str,
    url: str | None = None,
) -> dict[str, Any]:
    review_id = next_github_id()
    url = url or (
        f"https://github.com/{pull['owner']}/{pull['repo']}/pull/{pull['number']}"
        f"#pullrequestreview-{review_id}"
    )
    return {
        "id": review_id,
        "node_id": f"PRR_node_{review_id}",
        "author": author,
        "user": github_user(author),
        "state": state,
        "body": body,
        "commit_id": commit_id,
        "url": url,
        "html_url": url,
        "submitted_at": None if state == "PENDING" else github_timestamp(),
    }


def submit_review(
    number: int,
    owner: str,
    repo: str,
    *,
    author: str,
    state: str,
    commit_id: str,
    body: str = "",
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Record a review, as ``POST /pulls/{n}/reviews`` would.

    ``state`` is the request's ``event``; without one GitHub leaves the review
    PENDING, visible only to its author until it is submitted. GitHub rejects a
    self-approval and a second pending review, so both are refused here too.
    """
    pull = find_pull(number, owner, repo)
    if pull is None:
        return None
    if state == "APPROVE" and author == pull["author"]:
        return {"_error": "Can not approve your own pull request"}
    if state == "PENDING" and pending_review(pull, author) is not None:
        return {"_error": "User can only have one pending review per pull request"}
    review = _review_record(
        pull,
        author=author,
        state=_REVIEW_STATES.get(state, state),
        commit_id=commit_id,
        body=body,
    )
    pull["reviews"].append(review)
    for comment in comments or []:
        _add_review_comment(
            pull, review_id=review["id"], commit_id=commit_id, author=author, payload=comment
        )
    return review


def pending_review(pull: dict[str, Any], author: str) -> dict[str, Any] | None:
    return next(
        (
            review
            for review in pull["reviews"]
            if review.get("state") == "PENDING" and review.get("author") == author
        ),
        None,
    )


def submit_pending_review(
    number: int, owner: str, repo: str, review_id: int, *, author: str, event: str, body: str
) -> tuple[int, dict[str, Any]]:
    """``POST /pulls/{n}/reviews/{id}/events``: submit a pending review with its comments."""
    pull = find_pull(number, owner, repo)
    review = next((r for r in pull["reviews"] if r.get("id") == review_id), None) if pull else None
    if pull is None or review is None or review.get("author") != author:
        return 404, {"message": "Not Found"}
    if review["state"] != "PENDING":
        return 422, {"message": "Can not submit a review that is not pending"}
    if event == "APPROVE" and author == pull["author"]:
        return 422, {"message": "Can not approve your own pull request"}
    review.update(
        state=_REVIEW_STATES.get(event, event),
        body=body or review["body"],
        submitted_at=github_timestamp(),
    )
    return 200, review


def delete_pending_review(
    number: int, owner: str, repo: str, review_id: int, *, author: str
) -> tuple[int, dict[str, Any]]:
    """``DELETE /pulls/{n}/reviews/{id}``: drop a pending review and its comments."""
    pull = find_pull(number, owner, repo)
    review = next((r for r in pull["reviews"] if r.get("id") == review_id), None) if pull else None
    if pull is None or review is None or review.get("author") != author:
        return 404, {"message": "Not Found"}
    if review["state"] != "PENDING":
        return 422, {"message": "Can not delete a submitted review"}
    pull["reviews"].remove(review)
    pull["review_comments"] = [
        c for c in pull["review_comments"] if c["pull_request_review_id"] != review_id
    ]
    return 200, review


def _add_review_comment(
    pull: dict[str, Any],
    *,
    review_id: int | None,
    commit_id: str,
    author: str,
    payload: dict[str, Any],
    url: str | None = None,
    in_reply_to: int | None = None,
) -> dict[str, Any]:
    comment_id = next_github_id()
    now = github_timestamp()
    comment = {
        "id": comment_id,
        "node_id": f"PRRC_node_{comment_id}",
        "pull_request_review_id": review_id,
        "in_reply_to_id": in_reply_to,
        "user": github_user(author),
        "body": str(payload.get("body") or ""),
        "path": str(payload.get("path") or ""),
        "line": payload.get("line"),
        "original_line": payload.get("original_line") or payload.get("line"),
        "start_line": payload.get("start_line") or payload.get("startLine"),
        "side": payload.get("side") or "RIGHT",
        "start_side": payload.get("start_side") or payload.get("startSide"),
        "commit_id": commit_id,
        "position": 1,
        "html_url": url
        or (
            f"https://github.com/{pull['owner']}/{pull['repo']}/pull/{pull['number']}"
            f"#discussion_r{comment_id}"
        ),
        "created_at": now,
        "updated_at": now,
    }
    pull["review_comments"].append(comment)
    return comment


def _review_by_node(node_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for pull in PULLS:
        for review in pull["reviews"]:
            if review.get("node_id") == node_id:
                return pull, review
    return None


def add_review_thread(author: str, thread: dict[str, Any]) -> dict[str, Any] | None:
    """GraphQL ``addPullRequestReviewThread``: a comment on the viewer's pending review."""
    found = _review_by_node(str(thread.get("pullRequestReviewId") or ""))
    if found is None:
        return None
    pull, review = found
    if review.get("author") != author or review["state"] != "PENDING":
        return None
    return _add_review_comment(
        pull,
        review_id=review["id"],
        commit_id=review["commit_id"],
        author=author,
        payload=thread,
    )


def _comment_by(
    predicate: Callable[[dict[str, Any]], bool],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for pull in PULLS:
        for comment in pull["review_comments"]:
            if predicate(comment):
                return pull, comment
    return None


def update_review_comment_body(author: str, node_id: str, body: str) -> dict[str, Any] | None:
    """GraphQL ``updatePullRequestReviewComment``."""
    found = _comment_by(lambda comment: comment["node_id"] == node_id)
    if found is None or found[1]["user"]["login"] != author:
        return None
    found[1].update(body=body, updated_at=github_timestamp())
    return found[1]


def delete_review_comment(owner: str, repo: str, comment_id: int, *, author: str) -> bool:
    """``DELETE /pulls/comments/{id}``."""
    found = _comment_by(lambda comment: comment["id"] == comment_id)
    if found is None:
        return False
    pull, comment = found
    if (pull["owner"], pull["repo"]) != (owner, repo) or comment["user"]["login"] != author:
        return False
    pull["review_comments"].remove(comment)
    return True


def _review_state(pull: dict[str, Any], review_id: int | None) -> str:
    review = next((r for r in pull["reviews"] if r.get("id") == review_id), None)
    return str(review.get("state")) if review else ""


def visible_review_comments(pull: dict[str, Any], viewer: str) -> list[dict[str, Any]]:
    """Inline comments the viewer can see: submitted ones, plus their own pending ones."""
    return [
        comment
        for comment in pull["review_comments"]
        if _review_state(pull, comment["pull_request_review_id"]) != "PENDING"
        or comment["user"]["login"] == viewer
    ]


def submitted_review_comments(pull: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        comment
        for comment in pull["review_comments"]
        if _review_state(pull, comment["pull_request_review_id"]) != "PENDING"
    ]


def thread_comments(pull: dict[str, Any], root: dict[str, Any]) -> list[dict[str, Any]]:
    """The thread ``root`` opened: it, then its replies."""
    return [root] + [c for c in pull["review_comments"] if c.get("in_reply_to_id") == root["id"]]


def review_threads_graphql(pull: dict[str, Any], viewer: str) -> list[dict[str, Any]]:
    """``reviewThreads`` with every field the backend's thread queries select."""
    visible = visible_review_comments(pull, viewer)
    visible_ids = {comment["id"] for comment in visible}
    return [
        {
            "id": thread_node_id(root),
            "path": root["path"],
            "line": root["line"],
            "originalLine": root["original_line"],
            "startLine": root["start_line"],
            "diffSide": root["side"],
            "startDiffSide": root["start_side"],
            "isResolved": root["id"] in pull["resolved_threads"],
            "isOutdated": root["line"] is None,
            "comments": {
                "nodes": [
                    {
                        "id": comment["node_id"],
                        "fullDatabaseId": str(comment["id"]),
                        "body": comment["body"],
                        "author": {"login": comment["user"]["login"]},
                        "url": comment["html_url"],
                        "pullRequestReview": (
                            {"fullDatabaseId": str(comment["pull_request_review_id"])}
                            if comment["pull_request_review_id"] is not None
                            else None
                        ),
                    }
                    for comment in thread_comments(pull, root)
                    if comment["id"] in visible_ids
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
        for root in visible
        if root.get("in_reply_to_id") is None
    ]


def add_issue_comment(pull: dict[str, Any], *, author: str, body: str) -> dict[str, Any]:
    """``POST /issues/{n}/comments``: a top-level pull request comment."""
    comment_id = next_github_id()
    now = github_timestamp()
    comment = {
        "id": comment_id,
        "user": github_user(author or "open-swe[bot]"),
        "body": body,
        "created_at": now,
        "updated_at": now,
        "html_url": (
            f"https://github.com/{pull['owner']}/{pull['repo']}/pull/{pull['number']}"
            f"#issuecomment-{comment_id}"
        ),
    }
    pull["issue_comments"].append(comment)
    return comment


def merge_pull(
    number: int, owner: str, repo: str, *, sha: str, merge_method: str
) -> tuple[int, dict[str, Any]]:
    """Merge a pull request the way the REST endpoint does: ``(status, body)``.

    ``sha`` is the caller's claim about the head it reviewed. GitHub answers 409
    when that no longer matches, which is the guarantee the expedited merge
    leans on, so the fake enforces it rather than merging whatever is current.
    """
    pull = find_pull(number, owner, repo)
    if pull is None:
        return 404, {"message": "Not Found"}
    if pull["state"] != "open" or pull["merged"]:
        return 405, {"message": "Pull request is not mergeable"}
    if pull["draft"]:
        return 405, {"message": "Draft pull requests cannot be merged"}
    if sha and sha != pull["head_sha"]:
        return 409, {"message": "Head branch was modified. Review and try the merge again."}
    if not pull["mergeable"]:
        return 405, {"message": "Pull request is not mergeable"}
    pull["merged"] = True
    set_pull_state(pull, "closed")
    pull["merged_at"] = pull["closed_at"]
    pull["merge_method"] = merge_method
    return 200, {
        "sha": pull["head_sha"],
        "merged": True,
        "message": "Pull Request successfully merged",
    }


def reset() -> None:
    SLACK_MESSAGES.clear()
    EPHEMERALS.clear()
    VIEWS.clear()
    CODE_CHANNELS.clear()
    PULLS.clear()
    REPO_MERGE_METHODS.clear()
    SNAPSHOTS.clear()
    DELETED_SNAPSHOTS.clear()
    COLLABORATOR_PERMISSIONS.clear()
    REPO_PRIVATE[0] = False
    _pr_seq[0] = 0
    _github_id_seq[0] = 0
    seed_bare_remotes()
