"""Links back to what triggered a run, for the PR body's References section.

A PR opened by Open SWE is the end of a trail that started in Slack, Linear, a
GitHub issue, or a dashboard plan. These helpers turn the run's ``configurable``
back into that trail. Source links are only appended for private repositories:
a Slack permalink or Linear ticket in a public PR body leaks internal context to
anyone who can read the repo.
"""

import logging
from typing import Any

import httpx
from langgraph.config import get_config
from langgraph_sdk import get_client

from ..settings.plan_store import get_plan_content
from .dashboard_links import dashboard_plan_url
from .github_pr import is_private_repo
from .slack_api import get_slack_permalink
from .slack_threads import get_active_slack_thread

logger = logging.getLogger(__name__)

REFERENCES_HEADING = "## References"


def run_configurable() -> dict[str, Any]:
    """This run's ``configurable``, or ``{}`` outside a LangGraph run."""
    try:
        config = get_config()
    except Exception:
        return {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, dict) else {}


async def plan_reference_line(configurable: dict[str, Any]) -> str | None:
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str):
        return None
    try:
        plan = await get_plan_content(thread_id)
    except Exception:
        logger.debug("Failed to look up plan content for %s", thread_id, exc_info=True)
        return None
    if not plan or not str(plan.get("html") or plan.get("markdown") or "").strip():
        return None
    plan_url = dashboard_plan_url(thread_id)
    if not plan_url:
        return None
    return f"- Plan: {plan_url}"


async def source_reference_lines(configurable: dict[str, Any]) -> list[str]:
    """Links to the Slack thread / Linear ticket / GitHub issue behind this run."""
    source = configurable.get("source")
    lines: list[str] = []

    if source == "slack":
        slack_thread = configurable.get("slack_thread") or {}
        thread_id = configurable.get("thread_id")
        active = await get_active_slack_thread(
            get_client(),
            thread_id if isinstance(thread_id, str) else None,
            slack_thread if isinstance(slack_thread, dict) else None,
        )
        slack_thread = active or {}
        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        permalink = slack_thread.get("permalink")
        if not isinstance(permalink, str) or not permalink.strip():
            permalink = None
            if channel_id and thread_ts:
                permalink = await get_slack_permalink(channel_id, thread_ts)
        if isinstance(permalink, str) and permalink.strip():
            lines.append(f"- Slack thread: {permalink.strip()}")
    elif source == "linear":
        linear_issue = configurable.get("linear_issue") or {}
        url = linear_issue.get("url")
        identifier = linear_issue.get("identifier")
        if url:
            lines.append(f"- Linear ticket: [{identifier or url}]({url})")
        elif identifier:
            lines.append(f"- Linear ticket: {identifier}")
    elif source in ("github", "github_issue"):
        github_issue = configurable.get("github_issue") or {}
        url = github_issue.get("url")
        number = github_issue.get("number")
        if url:
            label = f"#{number}" if number else url
            lines.append(f"- GitHub issue: [{label}]({url})")
        elif number:
            lines.append(f"- GitHub issue: #{number}")

    return lines


async def append_references(
    client: httpx.AsyncClient,
    configurable: dict[str, Any],
    *,
    owner: str,
    repo: str,
    body: str,
) -> str:
    """Append a References section to a PR body, best-effort.

    Never raises: a missing plan store or Slack outage must not stop the PR
    from being opened.
    """
    try:
        if REFERENCES_HEADING in body:
            return body
        lines: list[str] = []
        plan_line = await plan_reference_line(configurable)
        if plan_line:
            lines.append(plan_line)
        try:
            source_lines = await source_reference_lines(configurable)
            if source_lines and await is_private_repo(client, owner, repo):
                lines.extend(source_lines)
        except Exception:
            logger.debug("Failed to append source references to PR body", exc_info=True)
        if not lines:
            return body
        return f"{body.rstrip()}\n\n{REFERENCES_HEADING}\n" + "\n".join(lines)
    except Exception:
        logger.debug("Failed to append references to PR body", exc_info=True)
        return body
