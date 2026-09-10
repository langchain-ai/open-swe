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

_MAX_OPTIONS = 5


async def ask_user_choice(
    question: str,
    options: list[str],
    reply_to_ts: str = "",
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Ask the user to pick from a fixed set of answers.

    Use this when the run is blocked on a decision that is the user's to make
    and the answers are known in advance — up to five of them. The user gets
    them as buttons and can still answer in their own words instead.

    When a plan is ready, ask with `options=["Approve & implement",
    "Request changes"]` and a concise summary plus the review link as the
    `question`; those two answers drive plan approval.

    Ask at session level by default. `reply_to_ts` puts the question under one
    specific earlier message instead.

    Format `question` using Slack's mrkdwn format, NOT standard Markdown:
    *bold*, _italic_, <url|link text>, bullet lists with "• ".
    """
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not question.strip():
        return {"success": False, "error": "question is required"}
    choices = [option.strip() for option in options if option.strip()]
    if not choices:
        return {"success": False, "error": "options is required: give up to five answers"}
    if len(choices) > _MAX_OPTIONS:
        return {
            "success": False,
            "error": f"too many options: {len(choices)} given, at most {_MAX_OPTIONS} render",
        }

    client = get_langgraph_client()
    active = await get_active_slack_thread(
        client,
        thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    location = SlackThreadRef.parse(active) if active else None
    if location is None or not location.channel_id:
        return {
            "success": False,
            "error": "This session has no Slack location to ask in",
        }
    surface = slack_surface(location)
    if surface is None:
        return {"success": False, "error": "This session has no Slack location to ask in"}
    return await post_session_message(
        channel_id=location.channel_id,
        thread_ts=location.thread_ts,
        post_thread_ts=reply_to_ts.strip() or surface.reply_target() or location.thread_ts,
        message=question,
        blocks=option_blocks(question, choices),
        usage=summarize_run_usage(state),
        agent_thread_id=surface.viewer_link_thread_id(str(thread_id or "")),
        run_id=current_run_id(),
        triggering_user=triggering_user_id(cfg),
    )
