import json
import logging
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph_sdk.client import LangGraphClient

from agent.database import postgres
from agent.run_config import RunConfig
from agent.slack import continuations, interactive
from agent.slack.client import (
    convert_mentions_to_slack_format,
    get_active_slack_thread,
    post_slack_ephemeral_reply,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
    store_slack_message_run_mapping,
)
from agent.slack.dm import is_dm_session
from agent.slack.orphan import (
    dashboard_handoff_message,
    move_thread_to_dashboard,
    slack_thread_detached,
)
from agent.slack.thinking import restore_slack_session_status, restore_slack_thinking_status
from agent.utils.json_types import thread_metadata
from agent.utils.run_usage import RunUsageSummary, summarize_run_usage
from agent.utils.thread_ops import langgraph_client as get_langgraph_client

logger = logging.getLogger(__name__)

# Replaying these would pin a resumed run to this one's identity or mode.
_TRANSIENT_CONFIG_KEYS = frozenset(
    {"invocation_id", "resolved_agent_model_id", "run_id", "stop_summary"}
)


def _unsupported_blocks(reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"Those blocks cannot be posted: {reason}",
        "retry": True,
        "hint": "Nothing was posted. Call this tool again with that element removed.",
    }


def _resume_config(cfg: RunConfig) -> dict[str, Any]:
    """The configurable a click should resume this thread with."""
    return {key: value for key, value in cfg.dump().items() if key not in _TRANSIENT_CONFIG_KEYS}


async def _register_continuations(
    blocks: list[dict[str, Any]] | None,
    cfg: RunConfig,
    *,
    channel_id: str,
    thread_ts: str,
) -> tuple[list[dict[str, Any]] | None, list[continuations.SlackContinuation]]:
    """`blocks` with its interactive elements registered against this thread."""
    if not blocks or not cfg.thread_id:
        return blocks, []
    if not postgres.configured() and interactive.has_interactive_element(blocks):
        raise interactive.UnsupportedBlocks(
            "this deployment has no database to remember an interactive element by, so a "
            "click on it could never be answered. Use a link button instead"
        )
    prepared, rows = interactive.prepare(
        blocks,
        thread_id=str(cfg.thread_id),
        channel_id=channel_id,
        thread_ts=thread_ts,
        run_config=_resume_config(cfg),
    )
    await continuations.save(rows)
    return prepared, rows


async def slack_thread_reply(
    message: str,
    options: list[str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
    should_ask_for_feedback: bool = False,
) -> dict[str, Any]:
    """Implement the `slack_thread_reply` tool."""
    config = get_config()
    cfg = RunConfig.from_config(config)
    run_id = _current_run_id(config)
    slack_thread = cfg.slack_thread.dump() if cfg.slack_thread else {}
    thread_id = cfg.thread_id
    if cfg.slack_ask is True:
        return await _ephemeral_reply(cfg, message, blocks, state)
    client = get_langgraph_client()
    active = await get_active_slack_thread(
        client,
        thread_id,
        slack_thread if isinstance(slack_thread, dict) else None,
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
        if await _already_moved_to_dashboard(client, thread_id):
            return _dashboard_handoff(thread_id)
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    from agent.slack.code_channels import is_code_channel_session

    reply_thread_ts = active.get("reply_thread_ts")
    post_thread_ts = (
        str(reply_thread_ts)
        if is_code_channel_session(str(thread_ts)) and isinstance(reply_thread_ts, str)
        else str(thread_ts)
    )

    try:
        blocks, rows = await _register_continuations(
            blocks, cfg, channel_id=str(channel_id), thread_ts=str(thread_ts)
        )
    except interactive.UnsupportedBlocks as exc:
        return _unsupported_blocks(str(exc))

    async with slack_thread_mutation_lock(client, channel_id, thread_ts):
        message = convert_mentions_to_slack_format(message)
        slack_blocks = blocks or _build_option_blocks(message, options)
        usage = summarize_run_usage(state)
        message_ts, slack_error = await _post_and_store_mapping(
            channel_id,
            thread_ts,
            message,
            blocks=slack_blocks,
            usage=usage,
            post_thread_ts=post_thread_ts,
            agent_thread_id=(
                None if is_code_channel_session(str(thread_ts)) else str(thread_id or "") or None
            ),
            langgraph_client=client,
            run_id=run_id,
            triggering_user_id=_triggering_user_id(cfg),
            # A DM session is a private back-and-forth, so it never asks for a rating.
            should_ask_for_feedback=(
                should_ask_for_feedback
                and not options
                and not is_dm_session(
                    cfg.slack_thread.channel_context if cfg.slack_thread else None,
                    str(thread_ts),
                )
            ),
        )
    if message_ts is None:
        if slack_error == "thread_not_found":
            moved = bool(thread_id) and await move_thread_to_dashboard(
                client, str(thread_id), str(channel_id), str(thread_ts)
            )
            return _dashboard_handoff(thread_id) if moved else _dashboard_handoff_failed()
        return {
            "success": False,
            "error": slack_error or "post failed",
            "slack_error": slack_error,
            "message_chars": len(message),
            "hint": _slack_reply_failure_hint(slack_error),
        }
    await continuations.attach_message(interactive.tokens(rows), str(channel_id), str(message_ts))
    if run_id:
        # Slack drops the status when the app posts; a session keeps its on
        # whichever message currently holds it rather than on the session itself.
        if is_code_channel_session(str(thread_ts)):
            await restore_slack_session_status(client, str(channel_id), str(thread_ts))
        else:
            await restore_slack_thinking_status(str(channel_id), str(thread_ts))
    return {"success": True}


async def _ephemeral_reply(
    cfg: RunConfig,
    message: str,
    blocks: list[dict[str, Any]] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    slack_thread = cfg.slack_thread
    channel_id = slack_thread.channel_id if slack_thread else ""
    user_id = slack_thread.triggering_user_id if slack_thread else ""
    if not channel_id or not user_id:
        return {"success": False, "error": "Missing the Slack channel or user to answer"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}
    # An ephemeral message has no timestamp to update or hang siblings off, but
    # the token rides in the element's own `action_id`, so a click still lands.
    try:
        blocks, _ = await _register_continuations(blocks, cfg, channel_id=channel_id, thread_ts="")
    except interactive.UnsupportedBlocks as exc:
        return _unsupported_blocks(str(exc))
    posted = await post_slack_ephemeral_reply(
        channel_id,
        user_id,
        convert_mentions_to_slack_format(message),
        blocks=blocks,
        usage=summarize_run_usage(state),
        agent_thread_id=cfg.thread_id,
    )
    if not posted:
        return {
            "success": False,
            "error": "post failed",
            "hint": "The ephemeral answer could not be delivered. Retry once, then stop.",
        }
    return {"success": True}


async def _already_moved_to_dashboard(client: LangGraphClient, thread_id: str | None) -> bool:
    if not thread_id:
        return False
    try:
        thread = await client.threads.get(thread_id)
    except Exception:
        logger.exception(
            "Could not check whether the thread left Slack", extra={"agent_thread_id": thread_id}
        )
        return False
    return slack_thread_detached(thread_metadata(thread))


def _dashboard_handoff_failed() -> dict[str, Any]:
    return {
        "success": False,
        "error": "Slack thread no longer exists and it could not be moved to the dashboard",
        "moved_to_dashboard": False,
        "retry": True,
        "hint": (
            "The Slack thread you were replying in is gone, so posting there cannot work, and "
            "moving this thread to the dashboard failed. Retry once; if it fails again, give "
            "your answer as your final response."
        ),
    }


def _dashboard_handoff(thread_id: str | None) -> dict[str, Any]:
    return {
        "success": False,
        "error": "Slack thread no longer exists",
        "moved_to_dashboard": True,
        "retry": False,
        "hint": dashboard_handoff_message(str(thread_id or "")),
    }


def _current_run_id(config: Mapping[str, Any]) -> str | None:
    candidates = [config.get("run_id"), RunConfig.from_config(config).run_id]
    return next((str(candidate) for candidate in candidates if candidate), None)


def _triggering_user_id(cfg: RunConfig) -> str | None:
    return (cfg.slack_thread.triggering_user_id or None) if cfg.slack_thread else None


def _build_option_blocks(message: str, options: list[str] | None) -> list[dict[str, Any]] | None:
    if not options:
        return None
    clean_options = [option.strip() for option in options if option.strip()]
    if not clean_options:
        return None
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option[:75], "emoji": True},
                    "value": json.dumps(
                        {
                            "type": "plan_approval",
                            "action": "approve" if option == "Approve & implement" else "revise",
                        }
                        if option in {"Approve & implement", "Request changes"}
                        else {"type": "open_swe_option", "response": option}
                    ),
                    "action_id": f"open_swe_option_select_{index}",
                }
                for index, option in enumerate(clean_options[:5])
            ],
        },
    ]


def build_workflow_approval_blocks(message: str, fingerprint: str) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve & continue push",
                        "emoji": True,
                    },
                    "style": "primary",
                    "value": json.dumps(
                        {
                            "type": "workflow_push_approval",
                            "action": "approve",
                            "fingerprint": fingerprint,
                        }
                    ),
                    "action_id": "open_swe_option_select_approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel push", "emoji": True},
                    "style": "danger",
                    "value": json.dumps(
                        {
                            "type": "workflow_push_approval",
                            "action": "reject",
                            "fingerprint": fingerprint,
                        }
                    ),
                    "action_id": "open_swe_option_select_reject",
                },
            ],
        },
    ]


def _slack_reply_failure_hint(slack_error: str | None) -> str:
    if slack_error == "msg_too_long":
        return "Slack rejected the message as too long; retry with a shorter message."
    if slack_error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the channel; do not retry. Surface the failure to the user via the trace output instead."
    if slack_error and slack_error.startswith("rate_limited"):
        retry_after = slack_error.partition(":")[2].strip()
        if retry_after:
            return f"Slack rate limited the request; wait at least {retry_after}s before retrying, or surface the failure to the user via the trace output."
        return "Slack rate limited the request; wait before retrying, or surface the failure to the user via the trace output."
    if slack_error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry. Surface the failure to the user via the trace output instead."
    if slack_error and slack_error.startswith("http_error:"):
        return "Slack posting hit an HTTP error; retry once, then surface the failure to the user via the trace output."
    return "Slack post failed; retry once with a concise message or surface the failure to the user via the trace output."


async def _post_and_store_mapping(
    channel_id: str,
    thread_ts: str,
    message: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    usage: RunUsageSummary | None = None,
    agent_thread_id: str | None = None,
    langgraph_client: Any | None = None,
    run_id: str | None = None,
    triggering_user_id: str | None = None,
    post_thread_ts: str | None = None,
    should_ask_for_feedback: bool = False,
) -> tuple[str | None, str | None]:
    message_ts, slack_error = await post_slack_thread_reply_with_ts(
        channel_id,
        post_thread_ts or thread_ts,
        message,
        blocks=blocks,
        usage=usage,
        agent_thread_id=agent_thread_id,
    )
    if message_ts:
        resolved_client = langgraph_client or get_langgraph_client()
        await store_slack_message_run_mapping(
            resolved_client,
            channel_id,
            thread_ts,
            message_ts,
            run_id=run_id,
            triggering_user_id=triggering_user_id,
            should_ask_for_feedback=should_ask_for_feedback,
        )
    return message_ts, slack_error
