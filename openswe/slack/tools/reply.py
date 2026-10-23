import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Any, Literal

import httpx2
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.messages.content import create_text_block
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph_sdk.client import LangGraphClient

from openswe import event_claims
from openswe.dashboard.options import model_supports_images
from openswe.input_messages import input_message_timestamps
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import (
    MARKDOWN_TEXT_MAX_CHARS,
    MESSAGE_MAX_BLOCKS,
    SECTION_TEXT_MAX_CHARS,
    block_payload,
    option_actions,
    section,
)
from openswe.slack.client import (
    get_active_slack_thread,
    post_slack_ephemeral_reply,
    post_slack_thread_reply_with_ts,
    remove_slack_reaction,
    replace_slack_command_message,
    slack_thread_mutation_lock,
)
from openswe.slack.events import claim_slack_event
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, SlackRequestError, slack_error
from openswe.slack.markdown import markdown_blocks, markdown_to_mrkdwn
from openswe.slack.orphan import (
    dashboard_handoff_message,
    move_thread_to_dashboard,
    slack_thread_detached,
)
from openswe.slack.records import SlackRun, SlackThread, store_slack_message_run_mapping
from openswe.slack.run_feedback import feedback_block
from openswe.slack.thinking import restore_slack_thinking_status, settle_slack_thread_status
from openswe.slack.tools.read_thread_messages import fetch_and_format_thread
from openswe.utils.http import DEFAULT_HTTP_TIMEOUT
from openswe.utils.http_pool import pooled_transport
from openswe.utils.json_types import thread_metadata
from openswe.utils.multimodal import fetch_image_block, vision_not_supported_warning
from openswe.utils.run_usage import RunUsageSummary, summarize_run_usage
from openswe.utils.thread_ops import langgraph_client as get_langgraph_client

logger = logging.getLogger(__name__)

_NATIVE_MARKDOWN_MAX_CHARS = MARKDOWN_TEXT_MAX_CHARS
_BY_THE_WAY_ANSWER_SCOPE = "slack_btw_answer"
_BY_THE_WAY_ANSWER_TTL = timedelta(days=7)
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
    *,
    option_user_id: str | None = None,
) -> dict[str, Any] | list[dict[str, object]]:
    """Implement the `slack_reply` tool."""
    result = await send_slack_reply(
        message, response_type, options, blocks, state, option_user_id=option_user_id
    )
    image_urls = result.pop("image_urls", None)
    if not image_urls:
        return result
    cfg = RunConfig.from_config(get_config())
    model_id = (state or {}).get("selected_model_id") or cfg.resolved_agent_model_id
    if model_id and not model_supports_images(model_id):
        result["images"] = vision_not_supported_warning(model_id, len(image_urls)).strip()
        return result
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=DEFAULT_HTTP_TIMEOUT
    ) as http_client:
        images = [
            block for url in image_urls if (block := await fetch_image_block(url, http_client))
        ]
    return [dict(create_text_block(json.dumps(result))), *map(dict, images)]


async def send_slack_reply(
    message: str,
    response_type: Literal["progress", "final"],
    options: list[str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    *,
    option_user_id: str | None = None,
) -> dict[str, Any]:
    """Post a reply in the run's Slack thread."""
    if option_user_id is not None and (
        not re.fullmatch(r"[UW][A-Z0-9]+", option_user_id) or not options or blocks is not None
    ):
        return {
            "success": False,
            "error": "option_user_id requires a canonical Slack member ID (U/W followed by uppercase letters or digits), options, and no custom blocks",
        }
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
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    from openswe.slack.code_channels import is_code_channel_session

    reply_thread_ts = active.get("reply_thread_ts")
    post_thread_ts = (
        str(reply_thread_ts)
        if is_code_channel_session(str(thread_ts)) and isinstance(reply_thread_ts, str)
        else str(thread_ts)
    )

    async with slack_thread_mutation_lock(client, channel_id, thread_ts):
        if state and run_id:
            conflict = await _stale_reply_guard(state, channel_id, post_thread_ts, run_id)
            if conflict is not None:
                return conflict
        if options and len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
            return _oversized_options_error(message)
        feedback = bool(response_type == "final" and run_id and _triggering_user_id(cfg))
        slack_blocks = blocks
        if blocks is None:
            slack_blocks = _reply_blocks(
                message, options, reserve=_WEB_LINK_BLOCKS + feedback, option_user_id=option_user_id
            )
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
        try:
            message_ts = await _post_and_store_mapping(
                channel_id,
                thread_ts,
                message,
                blocks=slack_blocks,
                usage=usage,
                post_thread_ts=post_thread_ts,
                agent_thread_id=(
                    None
                    if is_code_channel_session(str(thread_ts))
                    else str(thread_id or "") or None
                ),
                run_id=run_id,
                triggering_user_id=_triggering_user_id(cfg),
            )
        except SlackRequestError as exc:
            if exc.code == "thread_not_found":
                moved = bool(thread_id) and await move_thread_to_dashboard(
                    client, str(thread_id), str(channel_id), str(thread_ts)
                )
                return _dashboard_handoff(thread_id) if moved else _dashboard_handoff_failed()
            return {
                "success": False,
                "error": exc.code,
                "slack_error": exc.code,
                "message_chars": len(message),
                "hint": _slack_reply_failure_hint(exc.code),
            }
        if message_ts and cfg.source == "slack" and not is_code_channel_session(str(thread_ts)):
            try:
                await _handle_kickoff(
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
    if run_id and not is_code_channel_session(str(thread_ts)):
        # Slack drops the status when the app posts.
        await restore_slack_thinking_status(str(channel_id), str(thread_ts))
    return {"success": True, "message_ts": message_ts}


async def _stale_reply_guard(
    state: Mapping[str, object], channel_id: str, thread_ts: str, run_id: str
) -> dict[str, object] | None:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    seen, conflicts = await SlackRun.reply_progress(channel_id, run_id)
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
    if not isinstance(timestamps, list):
        return None
    arrived = [ts for ts in timestamps if isinstance(ts, str) and ts not in seen and ts > max(seen)]
    if not arrived:
        return None
    await SlackRun.record_reply_conflict(
        channel_id,
        thread_ts,
        run_id,
        sorted(seen | {ts for ts in timestamps if isinstance(ts, str)}),
    )
    image_urls = latest.get("image_urls") or {}
    return {
        "success": False,
        "error": "new_slack_messages",
        "run_id": run_id,
        "hint": prompt("tools/slack-reply-conflict"),
        "formatted": latest.get("formatted"),
        "human_timestamps": timestamps,
        "image_urls": [url for ts in arrived for url in image_urls.get(ts, [])],
    }


async def _handle_kickoff(
    channel_id: str,
    thread_ts: str,
    update_ts: str,
    *,
    response_type: Literal["progress", "final"],
    eligible: bool,
) -> None:
    thread = await SlackThread.find(channel_id, thread_ts)
    if thread is None or not thread.kickoff_recorded:
        if eligible and response_type == "progress" and update_ts != thread_ts:
            await SlackThread.record_kickoff(channel_id, thread_ts, update_ts)
        return
    kickoff_ts = thread.kickoff_ts
    if kickoff_ts is None or kickoff_ts == update_ts:
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
    await SlackThread.record_kickoff(channel_id, thread_ts, None)


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
        return {
            "success": False,
            "error": "only one final answer is posted for /btw",
            "retry": True,
            "hint": (
                "Nothing was posted. Everyone in the Slack thread reads this reply and this run "
                "ends with it, so send a single `final` reply without `options`."
            ),
        }
    channel_id = cfg.slack_thread.channel_id if cfg.slack_thread else ""
    if not channel_id:
        return {"success": False, "error": "Missing the Slack channel to answer in"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}
    if blocks is None:
        blocks = _reply_blocks(message, None, reserve=0)
        if len(message) > _NATIVE_MARKDOWN_MAX_CHARS:
            message = markdown_to_mrkdwn(message)
    reservation = cfg.thread_id or ""
    try:
        reserved = await event_claims.claim(
            _BY_THE_WAY_ANSWER_SCOPE, reservation, ttl=_BY_THE_WAY_ANSWER_TTL
        )
    except Exception:
        logger.exception(
            "Could not reserve the /btw answer", extra={"agent_thread_id": cfg.thread_id}
        )
        return {"success": False, "error": "could not reserve the answer", "retry": True}
    if not reserved:
        return {
            "success": False,
            "error": "the /btw answer was already posted",
            "hint": "Do not post again; end the run.",
        }
    try:
        await post_slack_thread_reply_with_ts(channel_id, thread_ts, message, blocks=blocks)
    except SlackRequestError as exc:
        try:
            await event_claims.release(_BY_THE_WAY_ANSWER_SCOPE, reservation)
        except Exception:
            logger.exception(
                "Could not release the /btw answer reservation",
                extra={"agent_thread_id": cfg.thread_id},
            )
        return {
            "success": False,
            "error": exc.code or "post failed",
            "slack_error": exc.code,
            "hint": _slack_reply_failure_hint(exc.code),
        }
    if cfg.slack_by_the_way_message_ts:
        await remove_slack_reaction(
            channel_id, cfg.slack_by_the_way_message_ts, "hourglass_flowing_sand"
        )
    # Slack drops a thread's status on any bot post, including a conversation's already there.
    await settle_slack_thread_status(channel_id, thread_ts)
    return {"success": True}


async def _ephemeral_reply(
    cfg: RunConfig,
    message: str,
    blocks: list[dict[str, Any]] | None,
    options: list[str] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    if options:
        return {
            "success": False,
            "error": "options cannot be answered on an ephemeral reply",
            "retry": True,
            "hint": (
                "Slack cannot route a choice button on an ephemeral message back to this run, "
                "so nothing was posted. Call this tool again without `options`, putting the "
                "choice in `message` as a question."
            ),
        }
    slack_thread = cfg.slack_thread
    channel_id = slack_thread.channel_id if slack_thread else ""
    user_id = slack_thread.triggering_user_id if slack_thread else ""
    if not channel_id or not user_id:
        return {"success": False, "error": "Missing the Slack channel or user to answer"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}
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


def _oversized_options_error(message: str) -> dict[str, str | int | bool]:
    return {
        "success": False,
        "error": "Message with options exceeds Slack's 12000-character native Markdown limit",
        "message_chars": len(message),
        "retry": True,
        "hint": "Retry with the options and a message of at most 12000 characters.",
    }


def _reply_blocks(
    message: str, options: list[str] | None, *, reserve: int, option_user_id: str | None = None
) -> list[dict[str, Any]] | None:
    """``message`` then option buttons; ``None`` when they cannot fit in one message."""
    actions = option_actions(options, user_id=option_user_id)
    body = markdown_blocks(message)
    if body is None or len(body) + len(actions) + reserve > MESSAGE_MAX_BLOCKS:
        return None
    return [*block_payload(body), *actions]


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
    run_id: str | None = None,
    triggering_user_id: str | None = None,
    post_thread_ts: str | None = None,
) -> str:
    message_ts = await post_slack_thread_reply_with_ts(
        channel_id,
        post_thread_ts or thread_ts,
        message,
        blocks=blocks,
        usage=usage,
        agent_thread_id=agent_thread_id,
    )
    if message_ts:
        await store_slack_message_run_mapping(
            channel_id,
            thread_ts,
            message_ts,
            run_id=run_id,
            triggering_user_id=triggering_user_id,
        )
    return message_ts
