import logging
from typing import Any

from openswe.audit_logs.tools import audit_tool
from openswe.expedited_review.reviews import settings_hint
from openswe.github.pull_request_context import parse_pull_request_url
from openswe.review_guide.launch import GuideOrigin, GuideStart, GuideStartError, start_review_guide
from openswe.run_config import RunConfig
from openswe.slack.client import get_active_slack_thread
from openswe.source_context import SlackThreadRef
from openswe.threads.summary import thread_is_private
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


@expose_mcp()
@audit_tool()
async def slack_start_review_channel(
    pr_url: str, invite: list[str] | None = None
) -> dict[str, Any]:
    """Implement the `slack_start_review_channel` tool."""
    parsed = parse_pull_request_url(pr_url)
    if parsed is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    owner, repo, number = parsed
    cfg = RunConfig.from_runtime()
    try:
        origin = (
            await _mcp_caller_origin(caller)
            if (caller := cfg.mcp_caller)
            else await _slack_origin(cfg)
        )
        started = await start_review_guide(
            GuideStart(owner=owner, repo=repo, number=number, origin=origin, invite=invite or [])
        )
    except GuideStartError as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "channel_id": started.channel_id,
        "channel_link": f"<#{started.channel_id}>",
        "channel_url": f"https://slack.com/archives/{started.channel_id}",
    }


async def _slack_origin(cfg: RunConfig) -> GuideOrigin:
    """The Slack conversation asking, which the walkthrough is forked from."""
    requester = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if not cfg.thread_id or not requester:
        raise GuideStartError("only a person's Slack message can open a review channel")
    client = langgraph_client()
    try:
        metadata = thread_metadata(await client.threads.get(cfg.thread_id))
    except Exception as exc:
        logger.exception(
            "Could not read the thread opening a review channel",
            extra={"agent_thread_id": cfg.thread_id},
        )
        raise GuideStartError("Cannot verify thread credential scope") from exc
    active = await get_active_slack_thread(
        client, cfg.thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    if not active:
        raise GuideStartError("Current Slack location is unavailable")
    location = SlackThreadRef.model_validate(active)
    return GuideOrigin(
        requester_slack_id=requester,
        team_id=cfg.slack_thread.team_id if cfg.slack_thread else "",
        channel_id=location.channel_id,
        message_ts=location.triggering_event_ts or location.thread_ts,
        source_thread_id=cfg.thread_id,
        workspace_slug=cfg.workspace_slug,
        is_private=thread_is_private(metadata) or location.channel_id.startswith("D"),
    )


async def _mcp_caller_origin(login: str) -> GuideOrigin:
    """A person calling over MCP: no conversation to fork, and as private as their DM."""
    user = await User.for_login("github", login)
    if user is None or not user.slack_user_id:
        raise GuideStartError(
            "Your Slack account is not linked to Open SWE. "
            + settings_hint("Connect Slack under Connections", "/my-settings/connections")
        )
    return GuideOrigin(
        requester_slack_id=user.slack_user_id, team_id=user.slack_team_id, is_private=True
    )
