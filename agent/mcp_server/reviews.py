"""PR review dispatch for the MCP server.

Everything that touches the reviewer graph lives here so the MCP layer stays
protocol-only. Two functions carry assumptions about the rest of the codebase
and are marked ``INTEGRATION``: ``start_review`` (should mirror what the Slack
``review`` trigger sends) and ``extract_findings`` (should read wherever the
reviewer graph records its structured findings).
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from langgraph_sdk import get_client

from agent.mcp_server.auth import Caller

REVIEWER_GRAPH = "reviewer"
POLL_SECONDS = 3.0
TERMINAL_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})

_THREAD_NAMESPACE = uuid.UUID("6f0f3c1e-5a0b-4a52-9d0e-2b8a5e1c7c11")
_PR_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[0-9]{1,9})"
    r"(?:/(?:files|commits|checks))?/?$"
)


class ReviewError(Exception):
    """A failure that is safe to show to the calling agent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}/pull/{self.number}"


@dataclass(frozen=True)
class ReviewHandle:
    thread_id: str
    run_id: str
    web_url: str | None
    joined_existing_run: bool


def _csv_env(name: str) -> set[str]:
    return {v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()}


def parse_pr_url(raw: str) -> PullRequestRef:
    """Strictly parse a GitHub PR URL. The input is untrusted agent output."""
    parsed = urlparse(raw.strip())
    hosts = {"github.com"} | _csv_env("MCP_GITHUB_HOSTS")
    match = _PR_PATH.match(parsed.path)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in hosts or not match:
        raise ReviewError("invalid_pr_url", "Expected https://github.com/<owner>/<repo>/pull/<number>")
    return PullRequestRef(match["owner"], match["repo"], int(match["number"]))


def assert_repo_allowed(ref: PullRequestRef) -> None:
    """Apply the same allowlists the GitHub and Slack triggers use."""
    orgs, repos = _csv_env("ALLOWED_GITHUB_ORGS"), _csv_env("ALLOWED_GITHUB_REPOS")
    if not orgs and not repos:
        return
    if ref.owner.lower() in orgs or ref.full_name.lower() in repos:
        return
    raise ReviewError("repo_not_allowed", f"{ref.full_name} is not enabled for Open SWE")


def get_langgraph_client() -> Any:
    # url=None talks to the in-process server when running inside the deployment.
    return get_client(url=os.environ.get("LANGGRAPH_URL") or None)


def review_thread_id(ref: PullRequestRef, caller: Caller) -> str:
    """Deterministic per (PR, user), so repeat requests reuse one thread."""
    key = f"mcp-review:{ref.full_name.lower()}#{ref.number}:{caller.user_id}"
    return str(uuid.uuid5(_THREAD_NAMESPACE, key))


def web_url_for(thread_id: str) -> str | None:
    base = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    return f"{base}/agents/{thread_id}" if base else None


def _status_code(exc: Exception) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


async def start_review(client: Any, caller: Caller, ref: PullRequestRef) -> ReviewHandle:
    """INTEGRATION: keep in step with the Slack ``review`` trigger.

    Prefer calling the shared helper in agent/dispatch.py if it exposes one;
    the inline version below shows the shape of the run it needs to create.
    """
    thread_id = review_thread_id(ref, caller)
    await client.threads.create(
        thread_id=thread_id,
        if_exists="do_nothing",
        metadata={
            "source": "mcp",
            "graph_id": REVIEWER_GRAPH,
            "mcp_user_id": caller.user_id,
            "repo": ref.full_name,
            "pr_number": ref.number,
        },
    )
    try:
        run = await client.runs.create(
            thread_id,
            REVIEWER_GRAPH,
            input={"messages": [{"role": "user", "content": f"Review {ref.url}"}]},
            config={
                "configurable": {
                    "repo": {"owner": ref.owner, "name": ref.repo},
                    "pr_number": ref.number,
                    "pr_url": ref.url,
                    "source": "mcp",
                    "user_email": caller.email,
                }
            },
            metadata={"source": "mcp", "mcp_user_id": caller.user_id},
            multitask_strategy="reject",
        )
        joined = False
    except Exception as exc:
        if _status_code(exc) != 409:
            raise
        # A review of this PR is already running for this user: attach to it.
        run = await latest_run(client, thread_id)
        joined = True
    return ReviewHandle(thread_id, run["run_id"], web_url_for(thread_id), joined)


async def latest_run(client: Any, thread_id: str) -> dict[str, Any]:
    runs = await client.runs.list(thread_id, limit=1)
    if not runs:
        raise ReviewError("not_found", "No review run exists for this thread")
    return runs[0]


async def wait_for_run(client: Any, thread_id: str, run_id: str, *, timeout: float) -> str:
    """Poll until the run finishes or ``timeout`` elapses; return its last status."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        status = (await client.runs.get(thread_id, run_id))["status"]
        if status in TERMINAL_STATUSES or loop.time() >= deadline:
            return status
        await asyncio.sleep(POLL_SECONDS)


def public_status(run_status: str) -> str:
    if run_status == "success":
        return "completed"
    if run_status in TERMINAL_STATUSES:
        return "failed"
    return "running"


def normalize_finding(raw: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or index),
        "path": raw.get("path") or raw.get("file"),
        "line": raw.get("line") or raw.get("start_line"),
        "severity": raw.get("severity") or raw.get("priority"),
        "title": raw.get("title") or raw.get("summary"),
        "body": raw.get("body") or raw.get("description") or raw.get("comment"),
    }


async def extract_findings(client: Any, thread_id: str) -> list[dict[str, Any]]:
    """INTEGRATION: read the reviewer's structured findings.

    This assumes the reviewer leaves them in thread state under ``findings``.
    If they are only persisted through ``publish_review`` / the
    ``pull_request_review`` rows, read them from that store here instead.
    """
    state = await client.threads.get_state(thread_id)
    values = state.get("values") or {}
    raw = values.get("findings") or (values.get("review") or {}).get("findings") or []
    return [normalize_finding(f, i) for i, f in enumerate(raw, start=1) if isinstance(f, dict)]


async def assert_owns_thread(client: Any, caller: Caller, thread_id: str) -> None:
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:
        if _status_code(exc) == 404:
            raise ReviewError("not_found", "Unknown thread") from exc
        raise
    if (thread.get("metadata") or {}).get("mcp_user_id") != caller.user_id:
        # Same message as unknown, so thread ids can't be probed.
        raise ReviewError("not_found", "Unknown thread")


async def build_result(
    client: Any, thread_id: str, run_id: str, web_url: str | None, run_status: str
) -> dict[str, Any]:
    status = public_status(run_status)
    result: dict[str, Any] = {
        "thread_id": thread_id,
        "run_id": run_id,
        "web_url": web_url,
        "status": status,
    }
    if status == "completed":
        result["findings"] = await extract_findings(client, thread_id)
    elif status == "failed":
        result["error"] = f"Review run ended with status '{run_status}'. See web_url for details."
    return result
