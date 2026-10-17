"""The agent tool that attaches a pull request slacklet to its Slack thread."""

from typing import Any

import httpx2
from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.github.http import GitHubAppUnavailable
from openswe.run_config import RunConfig
from openswe.slack.cards import run_slack_location
from openswe.slack.client import parse_github_pr_url
from openswe.slack.http import SlackRequestError
from openswe.slacklets.store import PullRequestSlacklet, observe
from openswe.tools.request_human_review import repository_refusal
from openswe.users import User


@audit_tool()
async def start_pr_slacklet(pr_url: str) -> dict[str, Any]:
    """Implement the `start_pr_slacklet` tool."""
    ref = parse_github_pr_url(pr_url)
    if ref is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    cfg = RunConfig.from_config(get_config())
    if refusal := await repository_refusal(ref, cfg):
        return {"success": False, "error": refusal}
    channel_id, thread_ts = await run_slack_location(cfg, cfg.thread_id or "")
    if not (channel_id and thread_ts):
        return {"success": False, "error": "This run has no Slack thread to post the slacklet in"}
    try:
        view = await observe(ref)
    except GitHubAppUnavailable, httpx2.HTTPError:
        return {"success": False, "error": f"Open SWE cannot read {ref.owner}/{ref.repo}"}
    user = await User.for_login("github", cfg.github_login) if cfg.github_login else None
    try:
        slacklet, entries = await PullRequestSlacklet.attach(
            channel_id, thread_ts, view, created_by=user.id if user else None
        )
    except SlackRequestError as exc:
        return {"success": False, "error": f"Slack refused the slacklet message: {exc.code}"}
    return {
        "success": True,
        "slacklet_id": str(slacklet.id),
        "message_ts": slacklet.message_ts,
        "commands": PullRequestSlacklet.transcript(entries),
    }
