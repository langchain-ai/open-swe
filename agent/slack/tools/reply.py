import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any, Literal, NoReturn

from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import ConflictError

from agent.input_messages import input_message_timestamps
from agent.prompts import prompt
from agent.run_config import RunConfig
from agent.slack.blocks import (
    MARKDOWN_TEXT_MAX_CHARS,
    MESSAGE_MAX_BLOCKS,
    SECTION_TEXT_MAX_CHARS,
    block_payload,
    section,
)
from agent.slack.client import (
    get_active_slack_thread,
    post_slack_ephemeral_reply,
    post_slack_thread_reply_with_ts,
    remove_slack_reaction,
    replace_slack_command_message,
    slack_thread_mutation_lock,
    store_slack_message_run_mapping,
)
from agent.slack.dm import note_for_concierge
from agent.slack.events import claim_slack_event
from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from agent.slack.markdown import markdown_blocks, markdown_to_mrkdwn
from agent.slack.orphan import (
    dashboard_handoff_message,
    move_thread_to_dashboard,
    slack_thread_detached,
)
from agent.slack.run_feedback import feedback_block
from agent.slack.thinking import restore_slack_thinking_status, settle_slack_thread_status
from agent.slack.tools.read_thread_messages import fetch_and_format_thread
from agent.threads.creation import create_lock_thread
from agent.tools.errors import ToolError
from agent.utils.json_types import thread_metadata
from agent.utils.run_usage import RunUsageSummary, summarize_run_usage
from agent.utils.thread_ops import langgraph_client as get_langgraph_client

logger = logging.getLogger(__name__)

_NATIVE_MARKDOWN_MAX_CHARS = MARKDOWN_TEXT_MAX_CHARS
_BY_THE_WAY_ANSWER_TTL_MINUTES = 7 * 24 * 60
# The posting helpers append a dashboard-link context block.
_WEB_LINK_BLOCKS = 1


def _usage_with_effort(
    usage: RunUsageSummary | None, state: dict[str, Any] | None, cfg: RunConfig
) -> RunUsageSummary | None:
    state = state or {}
    selected = state.get("selected_model_id")
    model_id = selected or cfg.resolved_agent_model_id
    if usage is None or len(usage.models) != 1 or not model_id:
        return usage
    reported_model = usage.models[0].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if model_id.rsplit("/", 1)[-1].rsplit(":", 1)[-1] != reported_model:
        return usage
    effort = state.get("selected_effort") if selected else cfg.resolved_agent_effort
    return replace(usage, reasoning_effort=effort)


async def slack_reply(
    message: str,
    response_type: Literal["progress", "final"],
    options: list[str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Implement the `slack_reply` tool."""
    config = get_config()
    cfg = RunConfig.from_config(config)
    run_id = _current_run_id(config)
    slack_thread = cfg.slack_thread.dump() if cfg.slack_thread else {}
    thread_id = cfg.thread_id
    if cfg.slack_ask is True and cfg.slack_by_the_way_thread_ts:
        return await _by_the_way_reply(
            cfg, cfg.slack_by_the_way_thread_ts, message, response_type, blocks, options
        )
    if cfg.slack_ask is True:
        return await _ephemeral_reply(cfg, message, blocks, options, state)
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
        raise ToolError("Missing slack_thread.channel_id or slack_thread.thread_ts in config")

    if not message.strip():
        raise ToolError("Message cannot be empty")

    from agent.slack.code_channels import is_code_channel_session

    reply_thread_ts = active.get("reply_thread_ts")
    post_thread_ts = (
        str(reply_thread_ts)
        if is_code_channel_session(str(thread_ts)) and isinstance(reply_thread_ts, str)
        else str(thread_ts)
    )

    async with slack_thread_mutation_lock(client, channel_id, thread_ts):
        if state and run_id:
            await _stale_reply_guard(state, channel_id, post_thread_ts, run_id)
        if options and len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
            return _oversized_options_error(message)
        feedback = bool(response_type == "final" and run_id and _triggering_user_id(cfg))
        slack_blocks = blocks
        if blocks is None:
            slack_blocks = _reply_blocks(message, options, reserve=_WEB_LINK_BLOCKS + feedback)
            if len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
                message = markdown_to_mrkdwn(message)
        if feedback and run_id:
            if slack_blocks is None:
                slack_blocks = block_payload(
                    [
                        section(message[start : start + SECTION_TEXT_MAX_CHARS])
                        for start in range(0, len(message), SECTION_TEXT_MAX_CHARS)
                    ]
                )
            slack_blocks = [*slack_blocks, *block_payload([feedback_block(run_id)])]
        usage = _usage_with_effort(summarize_run_usage(state), state, cfg)
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
        )
        if message_ts and cfg.source == "slack" and not is_code_channel_session(str(thread_ts)):
            try:
                await _handle_kickoff(
                    client,
                    str(channel_id),
                    str(thread_ts),
                    message_ts,
                    response_type=response_type,
                    eligible=cfg.slack_kickoff_eligible is True and not cfg.slack_breakout,
                )
            except Exception:
                logger.exception(
                    "Could not update Slack investigation kickoff state",
                    extra={"slack_channel": channel_id, "slack_thread_ts": thread_ts},
                )
    if message_ts is None:
        if slack_error == "thread_not_found":
            moved = bool(thread_id) and await move_thread_to_dashboard(
                client, str(thread_id), str(channel_id), str(thread_ts)
            )
            return _dashboard_handoff(thread_id) if moved else _dashboard_handoff_failed()
        raise ToolError(
            slack_error or "post failed",
            details={
                "slack_error": slack_error,
                "message_chars": len(message),
                "hint": _slack_reply_failure_hint(slack_error),
            },
        )
    if cfg.automation_dm_user_id:
        await note_for_concierge(cfg.automation_dm_user_id, str(channel_id), message)
    if run_id and not is_code_channel_session(str(thread_ts)):
        # Slack drops the status when the app posts.
        await restore_slack_thinking_status(str(channel_id), str(thread_ts))
    return {"success": True}


async def _stale_reply_guard(
    state: Mapping[str, object], channel_id: str, thread_ts: str, run_id: str
) -> None:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    client = get_langgraph_client()
    namespace = ("slack_reply_freshness", run_id)
    item = await client.store.get_item(namespace, "progress")
    progress = item.get("value", {}) if item else {}
    stored_seen = progress.get("seen", [])
    seen = (
        {ts for ts in stored_seen if isinstance(ts, str)}
        if isinstance(stored_seen, list)
        else set()
    )
    stored_conflicts = progress.get("conflicts", 0)
    conflicts = stored_conflicts if isinstance(stored_conflicts, int) else 0
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        seen.update(input_message_timestamps(message.content))
        if isinstance(message, ToolMessage):
            try:
                payload = json.loads(message.content) if isinstance(message.content, str) else None
            except ValueError:
                continue
            if isinstance(payload, dict):
                timestamps = payload.get("human_timestamps")
                if isinstance(timestamps, list):
                    seen.update(ts for ts in timestamps if isinstance(ts, str))
    if conflicts >= 2 or not seen:
        return None
    try:
        latest = await fetch_and_format_thread(channel_id, thread_ts)
    except Exception:
        logger.warning("Could not check Slack reply freshness", exc_info=True)
        return None
    timestamps = latest.get("human_timestamps")
    if not isinstance(timestamps, list) or not any(
        isinstance(ts, str) and ts not in seen and ts > max(seen) for ts in timestamps
    ):
        return None
    await client.store.put_item(
        namespace,
        "progress",
        {
            "seen": sorted(seen | {ts for ts in timestamps if isinstance(ts, str)}),
            "conflicts": conflicts + 1,
        },
    )
    raise ToolError(
        "new_slack_messages",
        details={
            "run_id": run_id,
            "hint": prompt("tools/slack-reply-conflict"),
            "formatted": latest.get("formatted"),
            "human_timestamps": timestamps,
        },
    )


async def _handle_kickoff(
    client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    update_ts: str,
    *,
    response_type: Literal["progress", "final"],
    eligible: bool,
) -> None:
    namespace = ("slack_kickoff", channel_id)
    item = await client.store.get_item(namespace, thread_ts)
    value = item.get("value") if isinstance(item, dict) else None
    if isinstance(value, dict) and value.get("removed") is True:
        return
    kickoff_ts = value.get("kickoff_ts") if isinstance(value, dict) else None
    if not kickoff_ts and eligible and response_type == "progress" and update_ts != thread_ts:
        await client.store.put_item(namespace, thread_ts, {"kickoff_ts": update_ts})
        return
    if not isinstance(kickoff_ts, str) or not kickoff_ts or kickoff_ts == update_ts:
        return
    try:
        async with SlackClient.bot() as slack:
            await slack.chat_delete(channel=channel_id, ts=kickoff_ts)
    except SLACK_REQUEST_ERRORS as exc:
        logger.warning(
            "Could not remove Slack investigation kickoff",
            extra={
                "slack_channel": channel_id,
                "slack_thread_ts": thread_ts,
                "slack_error": slack_error(exc),
            },
        )
        return
    await client.store.put_item(namespace, thread_ts, {"removed": True})


async def _by_the_way_reply(
    cfg: RunConfig,
    thread_ts: str,
    message: str,
    response_type: Literal["progress", "final"],
    blocks: list[dict[str, Any]] | None,
    options: list[str] | None,
) -> dict[str, Any]:
    """Post the one public `/btw` answer, with no link back to the asker's private thread."""
    if options or response_type != "final":
        raise ToolError(
            "only one final answer is posted for /btw",
            details={
                "retry": True,
                "hint": "Nothing was posted. Everyone in the Slack thread reads this reply and this run ends with it, so send a single `final` reply without `options`.",
            },
        )
    channel_id = cfg.slack_thread.channel_id if cfg.slack_thread else ""
    if not channel_id:
        raise ToolError("Missing the Slack channel to answer in")
    if not message.strip():
        raise ToolError("Message cannot be empty")
    if blocks is None:
        blocks = _reply_blocks(message, None, reserve=0)
        if len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
            message = markdown_to_mrkdwn(message)
    client = get_langgraph_client()
    reservation = _by_the_way_answer_id(cfg.thread_id or "")
    try:
        await create_lock_thread(client, reservation, ttl_minutes=_BY_THE_WAY_ANSWER_TTL_MINUTES)
    except ConflictError as exc:
        raise ToolError(
            "the /btw answer was already posted",
            details={"hint": "Do not post again; end the run."},
        ) from exc
    except Exception as exc:
        logger.exception(
            "Could not reserve the /btw answer", extra={"agent_thread_id": cfg.thread_id}
        )
        raise ToolError("could not reserve the answer", details={"retry": True}) from exc
    message_ts, slack_error = await post_slack_thread_reply_with_ts(
        channel_id, thread_ts, message, blocks=blocks
    )
    if message_ts is None:
        try:
            await client.threads.delete(reservation)
        except Exception:
            logger.exception(
                "Could not release the /btw answer reservation",
                extra={"agent_thread_id": cfg.thread_id},
            )
        raise ToolError(
            slack_error or "post failed",
            details={"slack_error": slack_error, "hint": _slack_reply_failure_hint(slack_error)},
        )
    if cfg.slack_by_the_way_message_ts:
        await remove_slack_reaction(
            channel_id, cfg.slack_by_the_way_message_ts, "hourglass_flowing_sand"
        )
    # Slack drops a thread's status on any bot post, including a conversation's already there.
    await settle_slack_thread_status(channel_id, thread_ts)
    return {"success": True}


def _by_the_way_answer_id(thread_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-by-the-way-answer:{thread_id}"))


async def _ephemeral_reply(
    cfg: RunConfig,
    message: str,
    blocks: list[dict[str, Any]] | None,
    options: list[str] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    if options:
        raise ToolError(
            "options cannot be answered on an ephemeral reply",
            details={
                "retry": True,
                "hint": "Slack cannot route a choice button on an ephemeral message back to this run, so nothing was posted. Call this tool again without `options`, putting the choice in `message` as a question.",
            },
        )
    slack_thread = cfg.slack_thread
    channel_id = slack_thread.channel_id if slack_thread else ""
    user_id = slack_thread.triggering_user_id if slack_thread else ""
    if not channel_id or not user_id:
        raise ToolError("Missing the Slack channel or user to answer")
    if not message.strip():
        raise ToolError("Message cannot be empty")
    if blocks is None:
        blocks = _reply_blocks(message, None, reserve=_WEB_LINK_BLOCKS)
        if len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
            message = markdown_to_mrkdwn(message)
    usage = _usage_with_effort(summarize_run_usage(state), state, cfg)
    response_url = cfg.slack_ask_response_url or ""
    if response_url and await claim_slack_event(f"slack-ask-answer:{cfg.thread_id}"):
        if await replace_slack_command_message(
            response_url,
            message,
            blocks=blocks,
            usage=usage,
            agent_thread_id=cfg.thread_id,
        ):
            return {"success": True}
        logger.warning(
            "Could not replace the Slack slash command acknowledgement",
            extra={"agent_thread_id": cfg.thread_id},
        )
    posted = await post_slack_ephemeral_reply(
        channel_id,
        user_id,
        message,
        blocks=blocks,
        usage=usage,
        agent_thread_id=cfg.thread_id,
    )
    if not posted:
        raise ToolError(
            "post failed",
            details={"hint": "The ephemeral answer could not be delivered. Retry once, then stop."},
        )
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


def _dashboard_handoff_failed() -> NoReturn:
    raise ToolError(
        "Slack thread no longer exists and it could not be moved to the dashboard",
        details={
            "moved_to_dashboard": False,
            "retry": True,
            "hint": "The Slack thread you were replying in is gone, so posting there cannot work, and moving this thread to the dashboard failed. Retry once; if it fails again, give your answer as your final response.",
        },
    )


def _dashboard_handoff(thread_id: str | None) -> NoReturn:
    raise ToolError(
        "Slack thread no longer exists",
        details={
            "moved_to_dashboard": True,
            "retry": False,
            "hint": dashboard_handoff_message(str(thread_id or "")),
        },
    )


def _current_run_id(config: Mapping[str, Any]) -> str | None:
    candidates = [config.get("run_id"), RunConfig.from_config(config).run_id]
    return next((str(candidate) for candidate in candidates if candidate), None)


def _triggering_user_id(cfg: RunConfig) -> str | None:
    return (cfg.slack_thread.triggering_user_id or None) if cfg.slack_thread else None


def _oversized_options_error(message: str) -> NoReturn:
    raise ToolError(
        "Message with options exceeds Slack's 12000-character native Markdown limit",
        details={
            "message_chars": len(message),
            "retry": True,
            "hint": "Retry with the options and a message of at most 12000 characters.",
        },
    )


def _reply_blocks(
    message: str, options: list[str] | None, *, reserve: int
) -> list[dict[str, Any]] | None:
    """``message`` then option buttons; ``None`` when they cannot fit in one message."""
    actions = _option_actions(options)
    body = markdown_blocks(message)
    if body is None or len(body) + len(actions) + reserve > MESSAGE_MAX_BLOCKS:
        return None
    return [*block_payload(body), *actions]


def _option_actions(options: list[str] | None) -> list[dict[str, Any]]:
    clean_options = [option.strip() for option in options or [] if option.strip()]
    if not clean_options:
        return []
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option[:75], "emoji": True},
                    "value": json.dumps({"type": "open_swe_option", "response": option}),
                    "action_id": f"open_swe_option_select_{index}",
                }
                for index, option in enumerate(clean_options[:5])
            ],
        }
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
        )
    return message_ts, slack_error
