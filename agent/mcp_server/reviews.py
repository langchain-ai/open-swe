"""PR review dispatch for the MCP server.

Reviews are started through ``trigger_pr_review_from_ref``, the same function
the dashboard and Slack use, so an MCP-requested review behaves identically:
canonical per-PR reviewer thread, "review started" comment, review published
to GitHub, visible in the dashboard. This module only adds the parts an MCP
caller needs: URL parsing, allowlists, waiting for the run, and reading the
findings back.

"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from starlette.exceptions import HTTPException

from agent.mcp_server.auth import Caller
from agent.review.findings import REVIEWER_THREAD_KIND, list_findings
from agent.thread_ids import reviewer_thread_id

logger = logging.getLogger(__name__)

# The dashboard's own re-review button dispatches with source="dashboard" and a
# github_login, which is a proven path. Other values are not interchangeable:
# agent/github/token.py, agent/utils/thread_participants.py and
# agent/completion.py all branch on the source. A dedicated "mcp" source needs
# handling added in those places, so it is a follow-up rather than a setting.
RUN_SOURCE = "dashboard"

POLL_SECONDS = 3.0
ACTIVE_STATUSES = frozenset({"pending", "running"})
TERMINAL_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})

_PR_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[0-9]{1,9})"
    r"(?:/(?:files|commits|checks))?/?$"
)
_FINDING_FIELDS = (
    "id",
    "severity",
    "confidence",
    "category",
    "title",
    "file",
    "start_line",
    "end_line",
    "description",
    "suggestion",
    "status",
    "in_diff",
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
        raise ReviewError(
            "invalid_pr_url", "Expected https://github.com/<owner>/<repo>/pull/<number>"
        )
    return PullRequestRef(match["owner"], match["repo"], int(match["number"]))


def assert_repo_allowed(ref: PullRequestRef) -> None:
    """Apply the same org/repo allowlists the GitHub and Slack triggers use."""
    orgs, repos = _csv_env("ALLOWED_GITHUB_ORGS"), _csv_env("ALLOWED_GITHUB_REPOS")
    if not orgs and not repos:
        return
    if ref.owner.lower() in orgs or ref.full_name.lower() in repos:
        return
    raise ReviewError("repo_not_allowed", f"{ref.full_name} is not enabled for Open SWE")


async def _check_dashboard_access(login: str, full_name: str) -> None:
    from agent.dashboard.repo_access import require_repo_access_for_user

    await require_repo_access_for_user(login, full_name)


async def assert_user_access(caller: Caller, ref: PullRequestRef) -> None:
    """Enforce the same per-user repo access the dashboard's review routes do.

    trigger_pr_review_from_ref runs with the GitHub App token and never checks
    who is asking, so this must run before any dispatch or read. It reuses the
    dashboard's ``require_repo_access_for_user``, which verifies the login's
    repo access with that user's own stored GitHub token. Any failure to
    verify is treated as denial.
    """
    try:
        await _check_dashboard_access(caller.github_login, ref.full_name)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise ReviewError(
                "login_required",
                "Sign in to the Open SWE dashboard with GitHub once so your repository "
                "access can be verified, then retry.",
            ) from exc
        if exc.status_code in (403, 404):
            raise ReviewError("forbidden", f"You don't have access to {ref.full_name}") from exc
        logger.warning("repo access check returned HTTP %s for %s", exc.status_code, ref.full_name)
        raise ReviewError(
            "access_check_failed", "Could not verify repository access. Try again shortly."
        ) from exc
    except Exception as exc:
        logger.warning("repo access check errored for %s", ref.full_name, exc_info=True)
        raise ReviewError(
            "access_check_failed", "Could not verify repository access. Try again shortly."
        ) from exc


def get_langgraph_client() -> Any:
    from agent.webhooks import common

    return common.get_client(url=common.LANGGRAPH_URL)


def web_url_for(ref: PullRequestRef) -> str | None:
    """Dashboard page for a PR review (ui/src/routes/agents/reviews/$owner.$repo.$number)."""
    base = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    return f"{base}/agents/reviews/{ref.owner}/{ref.repo}/{ref.number}" if base else None


def _status_code(exc: Exception) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


async def _trigger(ref: PullRequestRef, caller: Caller) -> dict[str, Any]:
    from agent.github.webhook import trigger_pr_review_from_ref
    from agent.slack.client import GitHubPrRef

    return await trigger_pr_review_from_ref(
        GitHubPrRef(owner=ref.owner, repo=ref.repo, number=ref.number, url=ref.url),
        source=RUN_SOURCE,
        github_login=caller.github_login,
        github_user_id=caller.github_user_id,
    )


async def latest_run(client: Any, thread_id: str) -> dict[str, Any] | None:
    try:
        runs = await client.runs.list(thread_id, limit=1)
    except Exception as exc:
        if _status_code(exc) == 404:
            return None
        raise
    return runs[0] if runs else None


async def start_review(client: Any, caller: Caller, ref: PullRequestRef) -> ReviewHandle:
    thread_id = reviewer_thread_id(ref.owner, ref.repo, ref.number)

    # dispatch_agent_run defaults to multitask_strategy="interrupt", so triggering
    # while a review is running would kill it. Attach to the running one instead.
    active = await latest_run(client, thread_id)
    if active and active["status"] in ACTIVE_STATUSES:
        return ReviewHandle(thread_id, active["run_id"], web_url_for(ref), True)

    result = await _trigger(ref, caller)
    if not result.get("success"):
        raise ReviewError(
            "dispatch_failed", str(result.get("error") or "Could not start the review")
        )
    thread_id = result.get("thread_id") or thread_id
    run = await latest_run(client, thread_id)
    if run is None:
        raise ReviewError("dispatch_failed", "Review was dispatched but no run was found")
    return ReviewHandle(thread_id, run["run_id"], web_url_for(ref), False)


async def load_review(client: Any, ref: PullRequestRef) -> tuple[str, dict[str, Any]]:
    """Find the reviewer thread and its latest run for a PR."""
    thread_id = reviewer_thread_id(ref.owner, ref.repo, ref.number)
    missing = ReviewError("not_found", "No Open SWE review exists for this PR")
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:
        if _status_code(exc) == 404:
            raise missing from exc
        raise
    if (thread.get("metadata") or {}).get("kind") != REVIEWER_THREAD_KIND:
        raise missing
    run = await latest_run(client, thread_id)
    if run is None:
        raise missing
    return thread_id, run


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


def normalize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Expose only the fields an external agent needs, not GitHub bookkeeping."""
    return {key: finding.get(key) for key in _FINDING_FIELDS}


async def build_result(
    thread_id: str, run_id: str, web_url: str | None, run_status: str
) -> dict[str, Any]:
    status = public_status(run_status)
    result: dict[str, Any] = {
        "thread_id": thread_id,
        "run_id": run_id,
        "web_url": web_url,
        "status": status,
    }
    if status == "completed":
        result["findings"] = [normalize_finding(f) for f in await list_findings(thread_id)]
    elif status == "failed":
        result["error"] = f"Review run ended with status '{run_status}'. See web_url for details."
    return result
