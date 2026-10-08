import logging
from typing import Any

from openswe.github.pull_request_context import parse_pull_request_url
from openswe.review_guide.launch import GuideStart, GuideStartError, start_review_guide
from openswe.run_config import RunConfig
from openswe.slack.client import get_active_slack_thread
from openswe.source_context import SlackThreadRef
from openswe.threads.summary import thread_is_private
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


async def slack_start_review_channel(
    pr_url: str, invite: list[str] | None = None
) -> dict[str, Any]:
    """Implement the `slack_start_review_channel` tool."""
    parsed = parse_pull_request_url(pr_url)
    if parsed is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    cfg = RunConfig.from_runtime()
    requester = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if not cfg.thread_id or not requester:
        return {
            "success": False,
            "error": "only a person's Slack message can open a review channel",
        }
    client = langgraph_client()
    try:
        metadata = thread_metadata(await client.threads.get(cfg.thread_id))
    except Exception:
        logger.exception(
            "Could not read the thread opening a review channel",
            extra={"agent_thread_id": cfg.thread_id},
        )
        return {"success": False, "error": "Cannot verify thread credential scope"}
    if thread_is_private(metadata):
        return {"success": False, "error": "Private threads cannot open review channels"}
    active = await get_active_slack_thread(
        client, cfg.thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    if not active:
        return {"success": False, "error": "Current Slack location is unavailable"}
    origin = SlackThreadRef.model_validate(active)
    owner, repo, number = parsed
    try:
        started = await start_review_guide(
            GuideStart(
                owner=owner,
                repo=repo,
                number=number,
                source_thread_id=cfg.thread_id,
                requester_slack_id=requester,
                origin_channel_id=origin.channel_id,
                origin_message_ts=origin.triggering_event_ts or origin.thread_ts,
                team_id=cfg.slack_thread.team_id if cfg.slack_thread else "",
                workspace_slug=cfg.workspace_slug,
                invite=invite or [],
            )
        )
    except GuideStartError as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "channel_id": started.channel_id,
        "channel_link": f"<#{started.channel_id}>",
    }
