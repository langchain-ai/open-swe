"""Shared implementation for the openswe-wave operator scripts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WAKE_NODES = (
    "plan_posted",
    "review_findings_posted",
    "terminal_merged",
    "terminal_closed",
    "terminal_run_error",
    "unhandled_condition",
)
AGENT_BOT_LOGIN = "mobilyze-open-swe-studio2"
ACTION_MARKER_PREFIX = "openswe-wave-action"
LINEAR_URL = "https://api.linear.app/graphql"
HTTP_TIMEOUT_SECONDS = 30.0
COMMAND_TIMEOUT_SECONDS = 60.0
MAX_PAGINATION_PAGES = 100


class WaveOpsError(RuntimeError):
    """A named, actionable operator-tool failure."""


class _PollDeadlineError(WaveOpsError):
    """A poll iteration exceeded its deadline."""


@dataclass(frozen=True)
class RecoveryDecision:
    """A recovery decision and the evidence supporting it."""

    reason: str
    eligible: bool
    commands: tuple[tuple[str, ...], ...]
    blockers: tuple[str, ...]
    marker: str
    evidence: dict[str, Any]


def emit(payload: Any, *, pretty: bool = True) -> None:
    """Print stable JSON output."""
    print(json.dumps(payload, indent=2 if pretty else None, sort_keys=True, default=str))


def require_env(*names: str) -> dict[str, str]:
    """Return required environment values or name every missing variable."""
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        exports = " ".join(f"export {name}=..." for name in missing)
        raise WaveOpsError(
            f"Missing required environment variable(s): {joined}. Set them with: {exports}"
        )
    return {name: os.environ[name] for name in names}


def read_json(path: str | Path) -> Any:
    """Read a JSON fixture."""
    return json.loads(Path(path).read_text())


def derive_linear_thread_id(issue_id: str) -> str:
    """Derive the production Linear thread ID from an issue UUID."""
    value = hashlib.sha256(f"linear-issue:{issue_id}".encode()).hexdigest()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"


def _bounded_timeout(ceiling: float, deadline: float | None, operation: str) -> float:
    """Return the timeout remaining before an optional monotonic deadline."""
    if deadline is None:
        return ceiling
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _PollDeadlineError(f"{operation} exceeded the poll deadline")
    return min(ceiling, remaining)


@contextmanager
def _wall_clock_deadline(deadline: float, operation: str) -> Iterator[None]:
    """Interrupt an iteration when its monotonic deadline expires."""
    remaining = _bounded_timeout(float("inf"), deadline, operation)
    previous_handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()

    def raise_deadline(_signum: int, _frame: Any) -> None:
        raise _PollDeadlineError(f"{operation} exceeded the poll deadline")

    signal.signal(signal.SIGALRM, raise_deadline)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        elapsed = max(0.0, time.monotonic() - started)
        previous_delay, previous_interval = previous_timer
        restored_delay = previous_delay
        if previous_delay > 0:
            if elapsed < previous_delay:
                restored_delay = previous_delay - elapsed
            elif previous_interval > 0:
                phase = (elapsed - previous_delay) % previous_interval
                restored_delay = previous_interval - phase
            else:
                restored_delay = 1e-6
        signal.setitimer(signal.ITIMER_REAL, restored_delay, previous_interval)


def _run(
    command: Sequence[str],
    *,
    cwd: str | None = None,
    deadline: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and surface a concise failure."""
    timeout = _bounded_timeout(COMMAND_TIMEOUT_SECONDS, deadline, "Command")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise WaveOpsError(f"Command timed out after {timeout:.1f}s ({' '.join(command)})") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WaveOpsError(f"Command failed ({' '.join(command)}): {detail}")
    return result


def gh_graphql(
    query: str, variables: dict[str, Any], *, deadline: float | None = None
) -> dict[str, Any]:
    """Execute a GitHub GraphQL query through gh."""
    require_env("GH_TOKEN")
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        flag = "-F" if isinstance(value, (int, bool)) else "-f"
        command.extend([flag, f"{key}={value}"])
    payload = json.loads(_run(command, deadline=deadline).stdout)
    if payload.get("errors"):
        raise WaveOpsError(f"GitHub GraphQL returned errors: {payload['errors']}")
    return payload.get("data", {})


PR_QUERY = """
query WavePr($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef { name }
    pullRequest(number: $number) {
      id number url state isDraft baseRefName headRefOid mergeStateStatus isInMergeQueue
      autoMergeRequest { enabledAt }
      statusCheckRollup { state }
      timelineItems(first: 100, after: $cursor, itemTypes: [CONVERT_TO_DRAFT_EVENT]) {
        nodes {
          ... on ConvertToDraftEvent {
            createdAt
            actor { __typename login }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

LABELS_QUERY = """
query WaveLabels($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid
      labels(first: 100, after: $cursor) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

REVIEW_THREADS_QUERY = """
query WaveReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid
      reviewThreads(first: 100, after: $cursor) {
        nodes { id isResolved }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def _paginate_pr_connection(
    query: str,
    connection_name: str,
    owner: str,
    repo: str,
    number: int,
    expected_head: str,
    *,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch every node from a PR GraphQL connection."""
    variables: dict[str, Any] = {"owner": owner, "repo": repo, "number": number}
    nodes: list[dict[str, Any]] = []
    for _page in range(MAX_PAGINATION_PAGES):
        data = gh_graphql(query, variables, deadline=deadline)
        pr = (data.get("repository") or {}).get("pullRequest")
        if not isinstance(pr, dict):
            raise WaveOpsError(f"Pull request {owner}/{repo}#{number} was not returned by GitHub")
        if pr.get("headRefOid") != expected_head:
            raise WaveOpsError(f"Pull request head changed while {connection_name} was paginated")
        connection = pr.get(connection_name) or {}
        nodes.extend(item for item in connection.get("nodes") or [] if isinstance(item, dict))
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
        if not cursor:
            raise WaveOpsError(f"GitHub {connection_name} pagination did not return an end cursor")
        variables = {**variables, "cursor": cursor}
    raise WaveOpsError(f"GitHub {connection_name} pagination exceeded {MAX_PAGINATION_PAGES} pages")


def github_pr_snapshot(
    repo_slug: str, number: int, *, deadline: float | None = None
) -> dict[str, Any]:
    """Fetch the PR state, including complete queue and actor evidence, through GraphQL."""
    owner, repo = repo_slug.split("/", 1)
    variables: dict[str, Any] = {"owner": owner, "repo": repo, "number": number}
    events: list[dict[str, Any]] = []
    first_pr: dict[str, Any] | None = None
    default_branch: str | None = None
    for _page in range(MAX_PAGINATION_PAGES):
        data = gh_graphql(PR_QUERY, variables, deadline=deadline)
        repository = data.get("repository") or {}
        pr = repository.get("pullRequest")
        if not isinstance(pr, dict):
            raise WaveOpsError(f"Pull request {repo_slug}#{number} was not returned by GitHub")
        if first_pr is None:
            first_pr = pr
            default_branch = (repository.get("defaultBranchRef") or {}).get("name")
        elif pr.get("headRefOid") != first_pr.get("headRefOid"):
            raise WaveOpsError("Pull request head changed while timeline evidence was paginated")
        timeline = pr.get("timelineItems") or {}
        events.extend(item for item in timeline.get("nodes") or [] if isinstance(item, dict))
        page_info = timeline.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            raise WaveOpsError("GitHub timeline pagination did not return an end cursor")
        variables = {**variables, "cursor": cursor}
    else:
        raise WaveOpsError(f"GitHub timelineItems pagination exceeded {MAX_PAGINATION_PAGES} pages")
    assert first_pr is not None
    first_pr["timelineItems"] = {"nodes": events}
    first_pr["timeline_complete"] = True
    expected_head = str(first_pr.get("headRefOid") or "")
    first_pr["labels"] = {
        "nodes": _paginate_pr_connection(
            LABELS_QUERY,
            "labels",
            owner,
            repo,
            number,
            expected_head,
            deadline=deadline,
        )
    }
    first_pr["reviewThreads"] = {
        "nodes": _paginate_pr_connection(
            REVIEW_THREADS_QUERY,
            "reviewThreads",
            owner,
            repo,
            number,
            expected_head,
            deadline=deadline,
        )
    }
    first_pr["defaultBranch"] = default_branch
    return first_pr


def _linear_graphql(
    query: str, variables: dict[str, Any], *, deadline: float | None = None
) -> dict[str, Any]:
    """Execute a Linear GraphQL request."""
    env = require_env("LINEAR_API_KEY")
    import httpx

    timeout = _bounded_timeout(HTTP_TIMEOUT_SECONDS, deadline, "Linear GraphQL request")
    try:
        response = httpx.post(
            LINEAR_URL,
            headers={"Authorization": env["LINEAR_API_KEY"], "Content-Type": "application/json"},
            json={"query": query, "variables": variables},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise WaveOpsError(f"LINEAR_API_KEY request failed: {exc}") from exc
    if payload.get("errors"):
        raise WaveOpsError(f"Linear GraphQL returned errors: {payload['errors']}")
    return payload.get("data", {})


LINEAR_SNAPSHOT_QUERY = """
query WaveIssue($id: String!, $cursor: String) {
  viewer { id name }
  issue(id: $id) {
    id identifier state { type name }
    comments(first: 100, after: $cursor) {
      nodes { id body createdAt user { id name } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def linear_snapshot(issue_id: str, *, deadline: float | None = None) -> dict[str, Any]:
    """Fetch the viewer identity and every issue comment."""
    variables: dict[str, Any] = {"id": issue_id}
    comments: list[dict[str, Any]] = []
    viewer: dict[str, Any] = {}
    first_issue: dict[str, Any] | None = None
    for _page in range(MAX_PAGINATION_PAGES):
        data = _linear_graphql(LINEAR_SNAPSHOT_QUERY, variables, deadline=deadline)
        issue = data.get("issue")
        if not isinstance(issue, dict):
            raise WaveOpsError(f"Linear issue {issue_id} was not returned")
        if first_issue is None:
            first_issue = issue
            viewer = data.get("viewer") or {}
        connection = issue.get("comments") or {}
        comments.extend(item for item in connection.get("nodes") or [] if isinstance(item, dict))
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            raise WaveOpsError("Linear comment pagination did not return an end cursor")
        variables = {**variables, "cursor": cursor}
    else:
        raise WaveOpsError(f"Linear comments pagination exceeded {MAX_PAGINATION_PAGES} pages")
    assert first_issue is not None
    first_issue["comments"] = {"nodes": comments}
    return {"viewer": viewer, "issue": first_issue}


def linear_comment(issue_id: str, body: str, *, deadline: float | None = None) -> None:
    """Post an action log to a Linear issue."""
    mutation = """
    mutation WaveComment($input: CommentCreateInput!) {
      commentCreate(input: $input) { success comment { id } }
    }
    """
    data = _linear_graphql(
        mutation, {"input": {"issueId": issue_id, "body": body}}, deadline=deadline
    )
    result = data.get("commentCreate") or {}
    if result.get("success") is not True:
        raise WaveOpsError("Linear action log was not accepted")


async def _langgraph_snapshot(thread_id: str, timeout: float) -> dict[str, Any]:
    """Fetch thread metadata and recent runs."""
    env = require_env("LANGGRAPH_URL")
    from langgraph_sdk import get_client

    client = get_client(url=env["LANGGRAPH_URL"], timeout=timeout)
    try:
        async with asyncio.timeout(timeout):
            thread = await client.threads.get(thread_id)
            runs = await client.runs.list(thread_id, limit=1000)
    except _PollDeadlineError:
        raise
    except TimeoutError as exc:
        raise WaveOpsError(f"LANGGRAPH_URL request timed out after {timeout:.1f}s") from exc
    except Exception as exc:
        raise WaveOpsError(f"LANGGRAPH_URL request failed: {exc}") from exc
    return {"thread": thread, "runs": runs}


def langgraph_snapshot(thread_id: str, *, deadline: float | None = None) -> dict[str, Any]:
    """Synchronously fetch the LangGraph thread snapshot."""
    timeout = _bounded_timeout(HTTP_TIMEOUT_SECONDS, deadline, "LangGraph snapshot")
    return asyncio.run(_langgraph_snapshot(thread_id, timeout))


def _event_node(event: dict[str, Any]) -> str | None:
    """Map a normalized observation to an approved wake node."""
    mapping = {
        "plan_posted": "plan_posted",
        "review_findings": "review_findings_posted",
        "merged": "terminal_merged",
        "closed": "terminal_closed",
        "run_error": "terminal_run_error",
        "unhandled": "unhandled_condition",
    }
    return mapping.get(str(event.get("kind")))


def assign_poll_id(events: Sequence[dict[str, Any]], poll_id: str) -> list[dict[str, Any]]:
    """Assign one poll identity to every observation collected in that poll."""
    return [{**event, "poll_id": poll_id} for event in events]


def event_fingerprint(event: dict[str, Any]) -> str:
    """Fingerprint a persistent condition independently of its observation poll."""
    payload = {key: value for key, value in event.items() if key != "poll_id"}
    return json.dumps(payload, sort_keys=True, default=str)


def replay_events(events: Sequence[dict[str, Any]], session_user_id: str) -> dict[str, Any]:
    """Replay recorded observations with self suppression and per-poll coalescing."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    suppressed = 0
    ignored = 0
    for index, event in enumerate(events):
        if event.get("author_id") == session_user_id:
            suppressed += 1
            continue
        node = _event_node(event)
        if node is None:
            ignored += 1
            continue
        poll_id = str(event.get("poll_id") or f"event-{index}")
        if poll_id not in grouped:
            grouped[poll_id] = []
            order.append(poll_id)
        grouped[poll_id].append({**event, "wake_node": node})
    priority = {node: index for index, node in enumerate(WAKE_NODES)}
    wakes = []
    for poll_id in order:
        observations = grouped[poll_id]
        chosen = max(observations, key=lambda item: priority[item["wake_node"]])
        wakes.append(
            {
                "poll_id": poll_id,
                "wake_node": chosen["wake_node"],
                "summary": " | ".join(
                    str(item.get("summary") or item.get("kind")) for item in observations
                ),
                "evidence": observations,
            }
        )
    return {
        "raw_events": len(events),
        "self_authored_suppressed": suppressed,
        "non_actionable_ignored": ignored,
        "wake_count": len(wakes),
        "wakes": wakes,
    }


def comments_to_events(
    comments: Sequence[dict[str, Any]], session_user_id: str, known_ids: set[str]
) -> list[dict[str, Any]]:
    """Normalize new Linear comments into monitor observations."""
    events = []
    for comment in comments:
        comment_id = str(comment.get("id") or "")
        if not comment_id or comment_id in known_ids:
            continue
        body = str(comment.get("body") or "")
        lower = body.lower()
        user = comment.get("user") or {}
        kind = "progress"
        if "/plan" in lower and "plan" in lower and ("ready" in lower or "review" in lower):
            kind = "plan_posted"
        elif "wasn't able to finish" in lower or "unexpected error" in lower:
            kind = "run_error"
        events.append(
            {
                "source": "linear",
                "poll_id": comment.get("createdAt") or comment_id,
                "kind": kind,
                "author_id": user.get("id"),
                "summary": " ".join(body.split())[:300],
                "session_user_id": session_user_id,
            }
        )
    return events


def terminal_pr_state_event(
    snapshot: dict[str, Any], emitted_states: set[str]
) -> dict[str, Any] | None:
    """Return a terminal PR observation unless its wake was already emitted."""
    pr = snapshot.get("pr") or {}
    state = str(pr.get("state") or "")
    events = {
        "MERGED": {"kind": "merged", "source": "github", "summary": "PR merged"},
        "CLOSED": {"kind": "closed", "source": "github", "summary": "PR closed"},
    }
    event = events.get(state)
    if event is None or state in emitted_states:
        return None
    return event


def snapshot_transition_events(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    """Normalize review-thread and LangGraph run transitions."""
    events: list[dict[str, Any]] = []
    old_threads = set(previous.get("unresolved_review_thread_ids") or [])
    new_threads = set(current.get("unresolved_review_thread_ids") or [])
    added_threads = sorted(new_threads - old_threads)
    if added_threads:
        events.append(
            {
                "kind": "review_findings",
                "source": "github",
                "summary": f"new unresolved review threads: {', '.join(added_threads)}",
            }
        )
    old_errors = set(previous.get("error_run_ids") or [])
    new_errors = set(current.get("error_run_ids") or [])
    added_errors = sorted(new_errors - old_errors)
    if added_errors:
        events.append(
            {
                "kind": "run_error",
                "source": "langgraph",
                "summary": f"new error runs: {', '.join(added_errors)}",
            }
        )
    return events


def _run_observations(snapshot: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    """Return latest status/time and recent error run IDs."""
    runs = snapshot.get("runs") or []
    if not runs:
        return None, None, []
    latest = max(runs, key=lambda run: str(run.get("updated_at") or run.get("created_at") or ""))
    status = latest.get("status")
    activity = latest.get("updated_at") or latest.get("created_at")
    errors = sorted(
        str(run.get("run_id") or run.get("id"))
        for run in runs
        if str(run.get("status") or "").lower() in {"error", "failed"}
        and (run.get("run_id") or run.get("id"))
    )
    return str(status) if status is not None else None, str(activity) if activity else None, errors


def live_snapshot(
    issue_id: str,
    thread_id: str,
    repo: str,
    pr_number: int | None,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Collect one coalesced monitor snapshot."""
    linear = linear_snapshot(issue_id, deadline=deadline)
    langgraph = langgraph_snapshot(thread_id, deadline=deadline)
    metadata = (langgraph.get("thread") or {}).get("metadata") or {}
    discovered_number = pr_number or metadata.get("pr_number")
    pr = (
        github_pr_snapshot(repo, int(discovered_number), deadline=deadline)
        if isinstance(discovered_number, int | str) and str(discovered_number).isdigit()
        else {}
    )
    threads = (pr.get("reviewThreads") or {}).get("nodes") or []
    unresolved_ids = sorted(
        str(item.get("id")) for item in threads if item.get("id") and not item.get("isResolved")
    )
    latest_status, latest_at, error_ids = _run_observations(langgraph)
    return {
        "linear": linear,
        "langgraph": langgraph,
        "pr": pr,
        "pr_number": int(discovered_number) if str(discovered_number or "").isdigit() else None,
        "unresolved_review_thread_ids": unresolved_ids,
        "latest_run_status": latest_status,
        "latest_run_at": latest_at,
        "error_run_ids": error_ids,
        "observed_at": datetime.now(UTC).isoformat(),
    }


def liveness_event(
    previous: dict[str, Any], current: dict[str, Any], stall_seconds: int
) -> dict[str, Any] | None:
    """Emit once when a busy thread crosses the configured silence bound."""
    thread = current.get("langgraph", {}).get("thread") or {}
    if thread.get("status") != "busy":
        return None
    activity = current.get("latest_run_at")
    if not activity:
        return {
            "kind": "unhandled",
            "source": "langgraph",
            "summary": "busy thread has no recent run activity timestamp",
        }
    try:
        activity_at = datetime.fromisoformat(str(activity).replace("Z", "+00:00"))
        current_at = datetime.fromisoformat(str(current["observed_at"]).replace("Z", "+00:00"))
        previous_at = datetime.fromisoformat(str(previous["observed_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return {
            "kind": "unhandled",
            "source": "langgraph",
            "summary": "run activity timestamp is unparseable",
        }
    current_age = (current_at - activity_at).total_seconds()
    previous_age = (previous_at - activity_at).total_seconds()
    if previous_age < stall_seconds <= current_age:
        return {
            "kind": "unhandled",
            "source": "langgraph",
            "summary": f"busy thread has no run activity for {int(current_age)} seconds",
        }
    return None


def _has_merge_hold(pr: dict[str, Any]) -> bool:
    """Return whether the repository merge veto is present."""
    nodes = (pr.get("labels") or {}).get("nodes") or []
    return any(item.get("name") == "hold-merge" for item in nodes if isinstance(item, dict))


def _green(pr: dict[str, Any]) -> bool:
    """Return whether the status rollup is green."""
    return (pr.get("statusCheckRollup") or {}).get("state") == "SUCCESS"


def action_marker(reason: str, repo: str, number: int, head_sha: str) -> str:
    """Build a deterministic recovery marker."""
    return f"<!-- {ACTION_MARKER_PREFIX}:{reason}:{repo}#{number}:{head_sha} -->"


def recovery_decision(snapshot: dict[str, Any]) -> RecoveryDecision:
    """Evaluate one recorded or live recovery snapshot."""
    metadata = snapshot.get("thread_metadata") or {}
    pr = snapshot.get("pr") or {}
    repo = str(snapshot.get("repo") or "")
    number = int(snapshot.get("pr_number") or pr.get("number") or 0)
    reason = str(metadata.get("auto_merge_alert_reason") or snapshot.get("reason") or "")
    head_sha = str(pr.get("headRefOid") or "")
    marker = action_marker(reason, repo, number, head_sha)
    blockers: list[str] = []
    if reason not in {"green_draft", "queue_stall"}:
        blockers.append("alert reason is not a documented recovery class")
    if pr.get("state") != "OPEN":
        blockers.append("pull request is not open")
    if pr.get("baseRefName") != pr.get("defaultBranch"):
        blockers.append("pull request does not target the default branch")
    if not head_sha:
        blockers.append("head SHA is unavailable")
    if pr.get("mergeStateStatus") != "CLEAN":
        blockers.append("merge state is not CLEAN")
    if not _green(pr):
        blockers.append("required checks are not green")
    if pr.get("isInMergeQueue") is not False:
        blockers.append("queue state is not explicitly false")
    if _has_merge_hold(pr):
        blockers.append("the merge-hold label is present")
    prior_comments = snapshot.get("linear_comments") or []
    if any(marker in str(item.get("body") or "") for item in prior_comments):
        blockers.append("this PR/head/reason already has an action log")
    commands: tuple[tuple[str, ...], ...] = ()
    if reason == "green_draft":
        if pr.get("isDraft") is not True:
            blockers.append("pull request is not draft")
        events = snapshot.get("convert_to_draft_events")
        if events is None:
            events = [
                item
                for item in ((pr.get("timelineItems") or {}).get("nodes") or [])
                if isinstance(item, dict)
            ]
        if pr.get("timeline_complete") is not True and "convert_to_draft_events" not in snapshot:
            blockers.append("convert_to_draft timeline evidence is incomplete")
        if not events:
            blockers.append("no convert_to_draft actor evidence exists")
        for event in events:
            actor = event.get("actor") or {}
            if actor.get("__typename") != "Bot" or actor.get("login") != AGENT_BOT_LOGIN:
                blockers.append("a convert_to_draft actor is not the canonical agent Bot")
                break
        commands = (
            ("gh", "pr", "ready", str(number), "--repo", repo),
            (
                "gh",
                "pr",
                "merge",
                str(number),
                "--repo",
                repo,
                "--auto",
                "--squash",
                "--match-head-commit",
                head_sha,
            ),
        )
    elif reason == "queue_stall":
        if pr.get("isDraft") is not False:
            blockers.append("pull request is draft or draft state is unknown")
        if pr.get("autoMergeRequest") is None:
            blockers.append("auto-merge is not armed")
        commands = (
            (
                "gh",
                "pr",
                "merge",
                str(number),
                "--repo",
                repo,
                "--disable-auto",
                "--match-head-commit",
                head_sha,
            ),
            (
                "gh",
                "pr",
                "merge",
                str(number),
                "--repo",
                repo,
                "--auto",
                "--squash",
                "--match-head-commit",
                head_sha,
            ),
        )
    evidence = {
        "reason": reason,
        "repo": repo,
        "pr_number": number,
        "head_sha": head_sha,
        "inferred_fields": snapshot.get("inferred_fields") or [],
    }
    return RecoveryDecision(reason, not blockers, commands, tuple(blockers), marker, evidence)


def recovery_log(decision: RecoveryDecision, status: str) -> str:
    """Render an auditable Linear recovery log."""
    command_text = " then ".join(" ".join(command) for command in decision.commands)
    return (
        f"Open SWE wave monitor recovery `{decision.reason}`: {status}.\n\n"
        f"PR: `{decision.evidence['repo']}#{decision.evidence['pr_number']}`\n"
        f"Head: `{decision.evidence['head_sha']}`\n"
        f"Procedure: `{command_text}`\n"
        "Safety gates: green, CLEAN, unqueued via GitHub GraphQL, stable head, "
        "and no merge-hold label.\n\n"
        f"{decision.marker}"
    )


def _recovery_verified(reason: str, pr: dict[str, Any], expected_head: str) -> bool:
    """Verify the post-action state without requiring immediate queue entry."""
    if pr.get("headRefOid") != expected_head or pr.get("state") != "OPEN":
        return False
    if reason == "green_draft":
        return pr.get("isDraft") is False and pr.get("autoMergeRequest") is not None
    return pr.get("isDraft") is False and pr.get("autoMergeRequest") is not None


def _recovery_stage_blockers(snapshot: dict[str, Any], decision: RecoveryDecision) -> list[str]:
    """Revalidate mutation-safe state without requiring the pre-action shape."""
    pr = snapshot.get("pr") or {}
    blockers = []
    if pr.get("headRefOid") != decision.evidence["head_sha"]:
        blockers.append("head changed during recovery")
    if pr.get("state") != "OPEN":
        blockers.append("pull request is not open")
    if pr.get("baseRefName") != pr.get("defaultBranch"):
        blockers.append("pull request does not target the default branch")
    if pr.get("mergeStateStatus") != "CLEAN" or not _green(pr):
        blockers.append("pull request is no longer green and CLEAN")
    if pr.get("isInMergeQueue") is not False:
        blockers.append("queue state is not explicitly false")
    if _has_merge_hold(pr):
        blockers.append("the merge-hold label is present")
    return blockers


def apply_recovery(
    snapshot: dict[str, Any],
    decision: RecoveryDecision,
    refresh: Callable[[], dict[str, Any]],
    before_actions: Callable[[], None] | None = None,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Apply an eligible recovery after a fresh evidence check."""
    if not decision.eligible:
        return {"status": "blocked", "blockers": list(decision.blockers)}
    fresh = refresh()
    fresh_decision = recovery_decision(fresh)
    if not fresh_decision.eligible or fresh_decision.marker != decision.marker:
        return {
            "status": "blocked_after_recheck",
            "blockers": list(fresh_decision.blockers) or ["head or evidence changed"],
        }
    if before_actions:
        before_actions()
    action_started = False
    for command in decision.commands:
        blockers = _recovery_stage_blockers(refresh(), decision)
        if blockers:
            return {
                "status": "verification_failed" if action_started else "blocked_after_recheck",
                "blockers": blockers,
                "verified": False,
            }
        _run(command, deadline=deadline)
        action_started = True
        if len(command) > 2 and command[2] == "ready":
            blockers = _recovery_stage_blockers(refresh(), decision)
            if blockers:
                return {
                    "status": "verification_failed",
                    "blockers": blockers,
                    "verified": False,
                }
    verified = False
    for _ in range(3):
        latest = refresh()
        if _recovery_verified(
            decision.reason, latest.get("pr") or {}, decision.evidence["head_sha"]
        ):
            verified = True
            break
        time.sleep(_bounded_timeout(2, deadline, "Recovery verification"))
    return {"status": "applied" if verified else "verification_failed", "verified": verified}


def monitor_recovery(
    snapshot: dict[str, Any],
    *,
    apply: bool,
    refresh: Callable[[], dict[str, Any]],
    post_log: Callable[[str], None],
    deadline: float | None = None,
) -> dict[str, Any] | None:
    """Handle one recovery observation without adding non-approved wakes."""
    decision = recovery_decision(snapshot)
    if decision.reason not in {"green_draft", "queue_stall"}:
        return None
    pr = snapshot.get("pr") or {}
    if (
        decision.reason == "green_draft"
        and pr.get("isDraft") is False
        and pr.get("autoMergeRequest") is not None
    ):
        return None
    if decision.reason == "queue_stall" and pr.get("isInMergeQueue") is True:
        return None
    if not decision.eligible:
        blockers = set(decision.blockers)
        quiet = {
            "this PR/head/reason already has an action log",
            "the merge-hold label is present",
            "queue state is not explicitly false",
            "pull request is not open",
        }
        if blockers and blockers <= quiet:
            return None
        return {
            "kind": "unhandled",
            "source": "recovery",
            "summary": "; ".join(decision.blockers),
            "evidence": decision.evidence,
        }
    if not apply:
        return {
            "kind": "recovery_dry_run",
            "source": "recovery",
            "summary": recovery_log(decision, "dry-run"),
            "evidence": decision.evidence,
        }
    try:
        result = apply_recovery(
            snapshot,
            decision,
            refresh,
            before_actions=lambda: post_log(recovery_log(decision, "starting")),
            deadline=deadline,
        )
    except _PollDeadlineError:
        raise
    except Exception as exc:
        event = {
            "kind": "unhandled",
            "source": "recovery",
            "summary": f"recovery action failed: {exc}",
            "evidence": decision.evidence,
        }
        try:
            post_log(recovery_log(decision, f"action_failed: {exc}"))
        except _PollDeadlineError:
            raise
        except Exception:
            if deadline is None or time.monotonic() < deadline:
                raise
        return event
    status = str(result.get("status"))
    if status in {"applied", "verification_failed"}:
        post_log(recovery_log(decision, status))
    if status == "applied":
        return None
    return {
        "kind": "unhandled",
        "source": "recovery",
        "summary": f"recovery did not converge: {status}",
        "evidence": {**decision.evidence, "result": result},
    }


def _anchor_candidates(text: str) -> list[tuple[str, str | None]]:
    """Extract likely repository path and symbol anchors from ticket text."""
    values = re.findall(r"`([^`]+)`", text)
    values += re.findall(r"(?<![\w./-])([\w.-]+(?:/[\w.@+-]+)+(?::[\w.-]+)?)", text)
    candidates: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for value in values:
        token = value.strip().strip(".,;()[]")
        if " " in token or token.startswith(("http://", "https://")):
            continue
        path, separator, anchor = token.partition(":")
        if re.search(r"(?:^|/)[\w.-]+\.[A-Za-z0-9]+$", path):
            item = (path, anchor or None if separator else None)
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", token):
            item = ("", token)
        else:
            continue
        if item not in seen:
            candidates.append(item)
            seen.add(item)
    return candidates


def anchor_sweep(repo_dir: str, ref: str, text: str) -> list[dict[str, Any]]:
    """Report present, moved, and missing cited paths or symbols."""
    results = []
    tree = _run(["git", "ls-tree", "-r", "--name-only", ref], cwd=repo_dir).stdout.splitlines()
    for path, anchor in _anchor_candidates(text):
        if not path and anchor:
            matches = []
            for candidate in tree:
                if not candidate.endswith((".py", ".ts", ".tsx", ".js", ".md", ".yml", ".yaml")):
                    continue
                candidate_text = _run(["git", "show", f"{ref}:{candidate}"], cwd=repo_dir).stdout
                if anchor in candidate_text:
                    matches.append(candidate)
            results.append(
                {
                    "path": None,
                    "anchor": anchor,
                    "status": "present" if matches else "missing",
                    "matches": matches,
                }
            )
            continue
        exists = path in tree
        if exists and anchor:
            content = _run(["git", "show", f"{ref}:{path}"], cwd=repo_dir).stdout
            found = (
                int(anchor) <= len(content.splitlines()) if anchor.isdigit() else anchor in content
            )
            if found:
                results.append({"path": path, "anchor": anchor, "status": "present"})
                continue
            matches = []
            for candidate in tree:
                if not candidate.endswith((".py", ".ts", ".tsx", ".js", ".md", ".yml", ".yaml")):
                    continue
                candidate_text = _run(["git", "show", f"{ref}:{candidate}"], cwd=repo_dir).stdout
                if anchor in candidate_text:
                    matches.append(candidate)
            results.append(
                {
                    "path": path,
                    "anchor": anchor,
                    "status": "moved" if matches else "missing",
                    "matches": matches,
                }
            )
        elif exists:
            results.append({"path": path, "anchor": anchor, "status": "present"})
        else:
            basename = Path(path).name
            matches = [candidate for candidate in tree if Path(candidate).name == basename]
            if anchor and not anchor.isdigit():
                for candidate in tree:
                    if candidate in matches or not candidate.endswith(
                        (".py", ".ts", ".tsx", ".js", ".md", ".yml", ".yaml")
                    ):
                        continue
                    candidate_text = _run(
                        ["git", "show", f"{ref}:{candidate}"], cwd=repo_dir
                    ).stdout
                    if anchor in candidate_text:
                        matches.append(candidate)
            results.append(
                {
                    "path": path,
                    "anchor": anchor,
                    "status": "moved" if matches else "missing",
                    "matches": matches,
                }
            )
    return results


def _get(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dict or object."""
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _run_metadata(run: Any) -> dict[str, Any]:
    """Read LangSmith metadata from either supported shape."""
    metadata = _get(run, "metadata")
    if isinstance(metadata, dict):
        return metadata
    extra = _get(run, "extra", {})
    return extra.get("metadata", {}) if isinstance(extra, dict) else {}


def _token_count(run: Any) -> int:
    """Read a best-effort total token count."""
    for key in ("total_tokens", "tokens", "token_count"):
        value = _get(run, key)
        if isinstance(value, int):
            return value
    direct_usage = _get(run, "usage_metadata") or _get(run, "usage")
    direct_total = _get(direct_usage, "total_tokens")
    if isinstance(direct_total, int):
        return direct_total
    for source in (_get(run, "extra", {}), _get(run, "outputs", {})):
        for key in ("total_tokens", "tokens", "token_count"):
            value = _get(source, key)
            if isinstance(value, int):
                return value
        usage = _get(source, "usage_metadata") or _get(source, "usage")
        value = _get(usage, "total_tokens")
        if isinstance(value, int):
            return value
    prompt = _get(run, "prompt_tokens")
    completion = _get(run, "completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int):
        return prompt + completion
    return 0


def _root_token_total(root: Any, runs: Sequence[Any]) -> int:
    """Use aggregate root usage or leaf usage within that root's trace."""
    direct = _token_count(root)
    if direct:
        return direct
    root_id = str(_get(root, "id") or "")
    trace_id = str(_get(root, "trace_id") or "")
    run_ids = {str(_get(run, "id") or "") for run in runs}
    parent_ids = {
        str(_get(run, "parent_run_id") or "") for run in runs if _get(run, "parent_run_id")
    }
    candidates = []
    for run in runs:
        run_id = str(_get(run, "id") or "")
        if run_id == root_id or run_id in parent_ids:
            continue
        same_trace = trace_id and str(_get(run, "trace_id") or "") == trace_id
        direct_child = str(_get(run, "parent_run_id") or "") == root_id
        if same_trace or direct_child:
            candidates.append(run)
    if not trace_id and root_id not in run_ids:
        return 0
    return sum(_token_count(run) for run in candidates)


def trace_digest(runs: Sequence[Any], thread_id: str) -> dict[str, Any]:
    """Build a compact LangSmith thread digest."""
    selected = [run for run in runs if _run_metadata(run).get("thread_id") == thread_id]
    roots = [run for run in selected if not _get(run, "parent_run_id")]
    roots.sort(key=lambda run: str(_get(run, "start_time") or ""))
    errors = []
    for run in selected:
        error = _get(run, "error")
        status = str(_get(run, "status") or "")
        if error or status.lower() in {"error", "failed"}:
            errors.append(
                {
                    "id": str(_get(run, "id") or ""),
                    "name": _get(run, "name"),
                    "error": error,
                    "status": status,
                }
            )
    prompt_sizes = []
    for run in roots:
        inputs = _get(run, "inputs", {})
        prompt_sizes.append(len(json.dumps(inputs, default=str)))
    trend = "flat"
    if len(prompt_sizes) >= 2:
        delta = prompt_sizes[-1] - prompt_sizes[0]
        threshold = max(1000, int(prompt_sizes[0] * 0.1))
        trend = "up" if delta > threshold else "down" if delta < -threshold else "flat"
    recent = sorted(selected, key=lambda run: str(_get(run, "start_time") or ""), reverse=True)[:10]
    root_tokens = [_root_token_total(root, selected) for root in roots]
    return {
        "thread_id": thread_id,
        "root_runs": [
            {
                "id": str(_get(run, "id") or ""),
                "status": _get(run, "status"),
                "start_time": str(_get(run, "start_time") or ""),
                "tokens": root_tokens[index],
            }
            for index, run in enumerate(roots)
        ],
        "total_tokens": sum(root_tokens),
        "errors": errors,
        "recent_activity": [
            {
                "id": str(_get(run, "id") or ""),
                "name": _get(run, "name"),
                "status": _get(run, "status"),
                "start_time": str(_get(run, "start_time") or ""),
            }
            for run in recent
        ],
        "prompt_size_trend": {"samples": prompt_sizes, "direction": trend},
    }


def fetch_langsmith_runs(thread_id: str, project: str, limit: int) -> list[Any]:
    """Fetch LangSmith runs for one thread."""
    env = require_env("LANGSMITH_API_KEY")
    from langsmith import Client

    escaped = thread_id.replace("\\", "\\\\").replace('"', '\\"')
    filter_expr = f'and(eq(metadata_key, "thread_id"), eq(metadata_value, "{escaped}"))'
    client = Client(api_key=env["LANGSMITH_API_KEY"])
    try:
        return list(client.list_runs(project_name=project, filter=filter_expr, limit=limit))
    except Exception as exc:
        raise WaveOpsError(f"LANGSMITH_API_KEY request failed: {exc}") from exc


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a recorded monitor stream."""
    fixture = read_json(args.fixture)
    session_id = args.session_user_id or fixture.get("session_user_id")
    if not session_id:
        raise WaveOpsError("Replay needs --session-user-id or fixture session_user_id")
    result = replay_events(fixture.get("events", []), session_id)
    result["fixture"] = str(args.fixture)
    emit(result)
    return 0 if not args.max_wakes or result["wake_count"] <= args.max_wakes else 2


def cmd_recover(args: argparse.Namespace) -> int:
    """Evaluate or apply a documented recovery."""
    if args.fixture:
        snapshot = read_json(args.fixture)
    else:
        if not all((args.issue_id, args.repo, args.pr_number, args.thread_id)):
            raise WaveOpsError(
                "Live recovery needs --issue-id, --repo, --pr-number, and --thread-id"
            )
        lg = langgraph_snapshot(args.thread_id)
        linear = linear_snapshot(args.issue_id)
        snapshot = {
            "issue_id": args.issue_id,
            "repo": args.repo,
            "pr_number": args.pr_number,
            "thread_metadata": (lg.get("thread") or {}).get("metadata") or {},
            "pr": github_pr_snapshot(args.repo, args.pr_number),
            "linear_comments": ((linear.get("issue") or {}).get("comments") or {}).get("nodes")
            or [],
        }
    decision = recovery_decision(snapshot)
    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "eligible": decision.eligible,
        "reason": decision.reason,
        "blockers": list(decision.blockers),
        "commands": [list(command) for command in decision.commands],
        "marker": decision.marker,
        "evidence": decision.evidence,
        "proposed_linear_log": recovery_log(decision, "dry-run") if decision.eligible else None,
    }
    if args.apply:
        if args.fixture:
            raise WaveOpsError("--apply is not available with a fixture")

        def refresh() -> dict[str, Any]:
            lg = langgraph_snapshot(args.thread_id)
            linear = linear_snapshot(args.issue_id)
            return {
                "issue_id": args.issue_id,
                "repo": args.repo,
                "pr_number": args.pr_number,
                "thread_metadata": (lg.get("thread") or {}).get("metadata") or {},
                "pr": github_pr_snapshot(args.repo, args.pr_number),
                "linear_comments": ((linear.get("issue") or {}).get("comments") or {}).get("nodes")
                or [],
            }

        try:
            result = apply_recovery(
                snapshot,
                decision,
                refresh,
                before_actions=lambda: linear_comment(
                    args.issue_id, recovery_log(decision, "starting")
                ),
            )
        except Exception as exc:
            linear_comment(args.issue_id, recovery_log(decision, f"action_failed: {exc}"))
            raise
        payload["result"] = result
        if result.get("status") in {"applied", "verification_failed"}:
            linear_comment(args.issue_id, recovery_log(decision, str(result["status"])))
    emit(payload)
    if not decision.eligible:
        return 2
    result = payload.get("result")
    return 0 if not isinstance(result, dict) or result.get("status") == "applied" else 2


def cmd_watch(args: argparse.Namespace) -> int:
    """Watch live wave state and print only approved wakes."""
    if not all((args.issue_id, args.repo)):
        raise WaveOpsError("watch needs --issue-id and --repo")
    if args.interval <= 0:
        raise WaveOpsError("watch needs --interval greater than zero")
    thread_id = args.thread_id or derive_linear_thread_id(args.issue_id)
    baseline_deadline = time.monotonic() + args.interval
    try:
        with _wall_clock_deadline(baseline_deadline, "Wave monitor baseline"):
            previous = live_snapshot(
                args.issue_id,
                thread_id,
                args.repo,
                args.pr_number,
                deadline=baseline_deadline,
            )
    except Exception as exc:
        raise WaveOpsError(f"wave monitor baseline failed: {exc}") from exc
    viewer_id = args.session_user_id or str(
        (previous["linear"].get("viewer") or {}).get("id") or ""
    )
    if not viewer_id:
        raise WaveOpsError("Could not discover the Linear viewer identity; pass --session-user-id")
    known_ids = {
        str(item.get("id"))
        for item in (
            ((previous["linear"].get("issue") or {}).get("comments") or {}).get("nodes") or []
        )
    }
    terminal_states_emitted: set[str] = set()
    terminal_event = terminal_pr_state_event(previous, terminal_states_emitted)
    if terminal_event:
        poll_id = str(previous.get("observed_at") or "baseline")
        result = replay_events(assign_poll_id([terminal_event], poll_id), viewer_id)
        for wake in result["wakes"]:
            emit(wake, pretty=False)
            if wake["wake_node"] == "terminal_merged":
                terminal_states_emitted.add("MERGED")
            elif wake["wake_node"] == "terminal_closed":
                terminal_states_emitted.add("CLOSED")
    iterations = 0
    last_recovery_fingerprint: str | None = None
    active_unhandled: set[str] = set()
    last_poll_error: str | None = None
    while args.iterations == 0 or iterations < args.iterations:
        time.sleep(args.interval)
        deadline = time.monotonic() + args.interval
        try:
            with _wall_clock_deadline(deadline, "Wave monitor poll"):
                current = live_snapshot(
                    args.issue_id,
                    thread_id,
                    args.repo,
                    args.pr_number,
                    deadline=deadline,
                )
                comments = ((current["linear"].get("issue") or {}).get("comments") or {}).get(
                    "nodes"
                ) or []
                events = comments_to_events(comments, viewer_id, known_ids)
                terminal_event = terminal_pr_state_event(current, terminal_states_emitted)
                if terminal_event:
                    events.append(terminal_event)
                events.extend(snapshot_transition_events(previous, current))
                stale = liveness_event(previous, current, args.run_stall_seconds)
                if stale:
                    events.append(stale)
                pr_number = current.get("pr_number")
                if pr_number:
                    recovery_snapshot = {
                        "issue_id": args.issue_id,
                        "repo": args.repo,
                        "pr_number": pr_number,
                        "thread_metadata": (
                            (current["langgraph"].get("thread") or {}).get("metadata") or {}
                        ),
                        "pr": current.get("pr") or {},
                        "linear_comments": comments,
                    }

                    def refresh(
                        _pr_number: int = pr_number, _deadline: float = deadline
                    ) -> dict[str, Any]:
                        fresh = live_snapshot(
                            args.issue_id,
                            thread_id,
                            args.repo,
                            _pr_number,
                            deadline=_deadline,
                        )
                        fresh_comments = (
                            (fresh["linear"].get("issue") or {}).get("comments") or {}
                        ).get("nodes") or []
                        return {
                            "issue_id": args.issue_id,
                            "repo": args.repo,
                            "pr_number": _pr_number,
                            "thread_metadata": (
                                (fresh["langgraph"].get("thread") or {}).get("metadata") or {}
                            ),
                            "pr": fresh.get("pr") or {},
                            "linear_comments": fresh_comments,
                        }

                    recovery_event = monitor_recovery(
                        recovery_snapshot,
                        apply=args.apply,
                        refresh=refresh,
                        post_log=lambda body, _deadline=deadline: linear_comment(
                            args.issue_id, body, deadline=_deadline
                        ),
                        deadline=deadline,
                    )
                    if recovery_event:
                        fingerprint = json.dumps(recovery_event, sort_keys=True, default=str)
                        if fingerprint != last_recovery_fingerprint:
                            if recovery_event["kind"] == "recovery_dry_run":
                                emit(recovery_event, pretty=False)
                            else:
                                events.append(recovery_event)
                        last_recovery_fingerprint = fingerprint
                    else:
                        last_recovery_fingerprint = None
                poll_id = str(current.get("observed_at") or f"poll-{iterations}")
                events = assign_poll_id(events, poll_id)
                current_unhandled = {
                    event_fingerprint(event) for event in events if event.get("kind") == "unhandled"
                }
                events = [
                    event
                    for event in events
                    if event.get("kind") != "unhandled"
                    or event_fingerprint(event) not in active_unhandled
                ]
                active_unhandled = current_unhandled
                result = replay_events(events, viewer_id)
                for wake in result["wakes"]:
                    emit(wake, pretty=False)
                    if wake["wake_node"] == "terminal_merged":
                        terminal_states_emitted.add("MERGED")
                    elif wake["wake_node"] == "terminal_closed":
                        terminal_states_emitted.add("CLOSED")
                known_ids.update(str(item.get("id")) for item in comments)
                previous = current
                last_poll_error = None
        except Exception as exc:
            error = f"wave monitor poll failed: {exc}"
            if error != last_poll_error:
                emit(
                    {
                        "wake_node": "unhandled_condition",
                        "summary": error,
                        "evidence": {"issue_id": args.issue_id, "thread_id": thread_id},
                    },
                    pretty=False,
                )
            last_poll_error = error
        iterations += 1
    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    """Run the anchor sweep."""
    if args.source == "-":
        source = sys.stdin.read()
    elif Path(args.source).exists():
        source = Path(args.source).read_text()
    else:
        source = args.source
    results = anchor_sweep(args.repo, args.ref, source)
    emit({"ref": args.ref, "anchors": results})
    return 2 if any(item["status"] == "missing" for item in results) else 0


def cmd_trace(args: argparse.Namespace) -> int:
    """Build a LangSmith trace digest."""
    runs = (
        read_json(args.fixture)
        if args.fixture
        else fetch_langsmith_runs(args.thread, args.project, args.limit)
    )
    emit(trace_digest(runs, args.thread))
    return 0


def parser() -> argparse.ArgumentParser:
    """Build the shared CLI parser."""
    root = argparse.ArgumentParser(prog="openswe-wave")
    sub = root.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay")
    replay.add_argument("--fixture", required=True)
    replay.add_argument("--session-user-id")
    replay.add_argument("--max-wakes", type=int, default=0)
    replay.set_defaults(func=cmd_replay)

    recover = sub.add_parser("recover")
    recover.add_argument("--fixture")
    recover.add_argument("--issue-id")
    recover.add_argument("--thread-id")
    recover.add_argument("--repo")
    recover.add_argument("--pr-number", type=int)
    recover.add_argument("--apply", action="store_true")
    recover.set_defaults(func=cmd_recover)

    watch = sub.add_parser("watch")
    watch.add_argument("--issue-id", required=True)
    watch.add_argument("--thread-id")
    watch.add_argument("--repo", required=True)
    watch.add_argument("--pr-number", type=int)
    watch.add_argument("--apply", action="store_true")
    watch.add_argument("--session-user-id")
    watch.add_argument("--interval", type=float, default=60)
    watch.add_argument("--iterations", type=int, default=0)
    watch.add_argument("--run-stall-seconds", type=int, default=1800)
    watch.set_defaults(func=cmd_watch)

    anchor = sub.add_parser("anchor-sweep")
    anchor.add_argument("ref")
    anchor.add_argument("source")
    anchor.add_argument("--repo", default=".")
    anchor.set_defaults(func=cmd_anchor)

    trace = sub.add_parser("trace-digest")
    trace.add_argument("thread")
    trace.add_argument("--project", default="open-swe-agent")
    trace.add_argument("--limit", type=int, default=250)
    trace.add_argument("--fixture")
    trace.set_defaults(func=cmd_trace)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    """Run the openswe-wave CLI."""
    try:
        args = parser().parse_args(argv)
        return int(args.func(args))
    except (WaveOpsError, OSError, ValueError) as exc:
        print(f"openswe-wave: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
