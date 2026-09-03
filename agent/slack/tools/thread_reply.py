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
) -> dict[str, Any]:
    """Post a message to the current Slack thread and the Web UI.

    Use this for clarifying questions, essential progress updates, and the final
    answer or outcome. For Slack-triggered information-only requests, put the
    complete answer in `message`, not merely a summary, and do not repeat it in
    the final assistant response. Make `message` as concise as possible: default
    to one sentence with only the outcome/status and link, or one blocking
    question. Omit greetings, preambles, headings, recaps, implementation
    details, and redundant context; use bullets only when multiple items are
    essential. End the run by posting a concise final outcome here.

    Format messages using Slack's mrkdwn format, NOT standard Markdown.
    Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
    bullet lists with "• ", ```code blocks```, > blockquotes.
    Do NOT use **bold**, [link](url), or other standard Markdown syntax.

    To ask a user to choose from predefined options, pass `options`. Slack will
    render interactive buttons and the web UI will render the same choices.
    The user can still reply manually in the Slack thread.

    When a plan is ready, post a concise summary with the dashboard review link and
    pass `options=["Approve & implement", "Request changes"]`. The user can still
    reply manually with feedback.

    To mention/tag a user, use Slack's mention format: <@USER_ID>.
    You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
    Example: <@U06KD8BFY95> will tag that user in the message."""
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
    )
    return {"success": True} if result.get("success") else result
