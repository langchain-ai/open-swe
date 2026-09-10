from typing import Annotated, Any

from langgraph.prebuilt import InjectedState

from agent.run_config import RunConfig
from agent.slack.client import get_active_slack_thread
from agent.slack.session import (
    current_run_id,
    option_blocks,
    post_session_message,
    triggering_user_id,
)
from agent.slack.surfaces import slack_surface
from agent.source_context import SlackThreadRef
from agent.utils.run_usage import summarize_run_usage
from agent.utils.thread_ops import langgraph_client as get_langgraph_client


async def slack_thread_reply(
    message: str,
    options: list[str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
    should_ask_for_feedback: bool = False,
) -> dict[str, Any]:
    """Implement the `slack_thread_reply` tool."""
    cfg = RunConfig.from_runtime()
    slack_thread = cfg.slack_thread.dump() if cfg.slack_thread else {}
    thread_id = cfg.thread_id
    client = get_langgraph_client()
    active = await get_active_slack_thread(
        client,
        thread_id,
        slack_thread,
    )
    active = active or {}
    if (
        isinstance(slack_thread, dict)
        and slack_thread.get("channel_id") == active.get("channel_id")
        and slack_thread.get("thread_ts") == active.get("thread_ts")
        and isinstance(slack_thread.get("reply_thread_ts"), str)
    ):
        active["reply_thread_ts"] = slack_thread["reply_thread_ts"]

    channel_id = active.get("channel_id")
    thread_ts = active.get("thread_ts")
    if not channel_id or not thread_ts:
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    surface = slack_surface(SlackThreadRef.parse(active))
    if surface is None:
        return {"success": False, "error": "Current Slack location is unavailable"}
    result = await post_session_message(
        channel_id=str(channel_id),
        thread_ts=str(thread_ts),
        post_thread_ts=surface.reply_target() or str(thread_ts),
        message=message,
        blocks=blocks or option_blocks(message, options),
        usage=summarize_run_usage(state),
        agent_thread_id=surface.viewer_link_thread_id(str(thread_id or "")),
        run_id=current_run_id(),
        triggering_user=triggering_user_id(cfg),
        should_ask_for_feedback=should_ask_for_feedback and not options,
    )
    return {"success": True} if result.get("success") else result
