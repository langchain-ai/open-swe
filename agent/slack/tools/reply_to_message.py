from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.run_config import RunConfig
from agent.slack.client import get_active_slack_thread
from agent.slack.session import current_run_id, post_session_message, triggering_user_id
from agent.slack.surfaces import slack_surface
from agent.source_context import SlackThreadRef
from agent.utils.run_usage import summarize_run_usage
from agent.utils.thread_ops import langgraph_client as get_langgraph_client


async def slack_reply_to_message(
    message_ts: str,
    message: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Reply in the Slack thread hanging off one specific message.

    Everything you say already reaches this channel as you say it, so this is
    only for answering *under* a particular message: a question a user asked in
    a thread they started, or a point far enough back that answering at channel
    level would read as a non-sequitur. `message_ts` is that message's
    timestamp, as shown in the conversation context.

    Prefer answering at channel level — just say it — so the session reads as
    one conversation. Never repeat here what you have already said.

    Format messages using Slack's mrkdwn format, NOT standard Markdown.
    Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
    bullet lists with "• ", ```code blocks```, > blockquotes.
    To mention a user, use <@USER_ID>."""
    config = get_config()
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    client = get_langgraph_client()
    active = await get_active_slack_thread(
        client,
        thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    if not active:
        return {"success": False, "error": "Current Slack location is unavailable"}
    location = SlackThreadRef.parse(active)
    surface = slack_surface(location)
    if location is None or surface is None or not surface.projects_transcript:
        return {
            "success": False,
            "error": "This session is not in a Slack channel; use slack_thread_reply instead",
        }
    if not message_ts.strip():
        return {
            "success": False,
            "error": "message_ts is required: it identifies the message to reply under",
        }
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    return await post_session_message(
        channel_id=location.channel_id,
        thread_ts=location.thread_ts,
        post_thread_ts=message_ts.strip(),
        message=message,
        usage=summarize_run_usage(state),
        agent_thread_id=surface.viewer_link_thread_id(str(thread_id or "")),
        run_id=current_run_id(config),
        triggering_user=triggering_user_id(cfg),
    )
