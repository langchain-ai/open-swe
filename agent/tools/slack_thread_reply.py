import json
import os
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..utils.slack import (
    convert_mentions_to_slack_format,
    get_last_outgoing_message_for_thread,
    normalize_slack_reply_text,
    post_slack_thread_reply_with_ts,
    store_last_outgoing_message_for_thread,
    store_slack_message_run_mapping,
)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)


async def slack_thread_reply(
    message: str,
    options: list[str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    plan_approval: bool = False,
) -> dict[str, Any]:
    """Post a message to the current Slack thread.

    Use this for clarifying questions, mid-run progress updates, and the final
    summary. You can call this multiple times during a run — if you're about to
    do long-running work (cloning, large refactors, big test runs) consider
    posting a brief status update first so the user knows what's happening.
    Always end the run with a final reply summarizing what you did.

    Format messages using Slack's mrkdwn format, NOT standard Markdown.
    Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
    bullet lists with "• ", ```code blocks```, > blockquotes.
    Do NOT use **bold**, [link](url), or other standard Markdown syntax.

    To ask a user to choose from predefined options, pass `options`. Slack will
    render interactive buttons and the web UI will render the same choices.
    The user can still reply manually in the Slack thread.

    To present a plan for approval with action buttons, pass plan_approval=True. This renders "Approve & Implement", "Revise Plan", and "Cancel" buttons. The plan itself should be posted to the dashboard thread; use this to post a summary with a link to the dashboard thread view where the full plan can be reviewed.

    To mention/tag a user, use Slack's mention format: <@USER_ID>.
    You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
    Example: <@U06KD8BFY95> will tag that user in the message."""
    config = get_config()
    configurable = config.get("configurable", {})
    slack_thread = configurable.get("slack_thread", {})

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not channel_id or not thread_ts:
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    message = convert_mentions_to_slack_format(message)
    normalized_message = normalize_slack_reply_text(message)
    run_id = _current_run_id(config)
    langgraph_client = get_client(url=LANGGRAPH_URL)
    if run_id:
        last = await get_last_outgoing_message_for_thread(
            langgraph_client, channel_id, thread_ts, run_id
        )
        if last is not None and last[1] == normalized_message:
            return {
                "success": False,
                "error": "duplicate of previous reply — not sent",
                "hint": "The previous reply with identical content is still visible in this thread; skip the repost.",
            }
    if plan_approval:
        slack_blocks = _build_plan_approval_blocks(message)
    else:
        slack_blocks = blocks or _build_option_blocks(message, options)
    message_ts, slack_error = await _post_and_store_mapping(
        channel_id,
        thread_ts,
        message,
        blocks=slack_blocks,
        normalized_text=normalized_message,
        run_id=run_id,
        langgraph_client=langgraph_client,
    )
    if message_ts is None:
        return {
            "success": False,
            "error": slack_error or "post failed",
            "slack_error": slack_error,
            "message_chars": len(message),
            "hint": _slack_reply_failure_hint(slack_error),
        }
    return {"success": True}


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
                    "value": json.dumps({"type": "open_swe_option", "response": option}),
                    "action_id": "open_swe_option_select",
                }
                for option in clean_options[:5]
            ],
        },
    ]


def _build_plan_approval_blocks(message: str) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Implement", "emoji": True},
                    "style": "primary",
                    "value": json.dumps({"type": "plan_approval", "action": "approve"}),
                    "action_id": "open_swe_option_select",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Revise Plan", "emoji": True},
                    "value": json.dumps({"type": "plan_approval", "action": "revise"}),
                    "action_id": "open_swe_option_select",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel", "emoji": True},
                    "style": "danger",
                    "value": json.dumps({"type": "plan_approval", "action": "cancel"}),
                    "action_id": "open_swe_option_select",
                },
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
                    "text": {"type": "plain_text", "text": "Approve workflow push", "emoji": True},
                    "style": "primary",
                    "value": json.dumps(
                        {
                            "type": "workflow_push_approval",
                            "action": "approve",
                            "fingerprint": fingerprint,
                        }
                    ),
                    "action_id": "open_swe_option_select",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "style": "danger",
                    "value": json.dumps(
                        {
                            "type": "workflow_push_approval",
                            "action": "reject",
                            "fingerprint": fingerprint,
                        }
                    ),
                    "action_id": "open_swe_option_select",
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


def _current_run_id(config: dict[str, Any]) -> str | None:
    candidates = [config.get("run_id")]
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        candidates.append(configurable.get("run_id"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


async def _post_and_store_mapping(
    channel_id: str,
    thread_ts: str,
    message: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    normalized_text: str | None = None,
    run_id: str | None = None,
    langgraph_client: Any | None = None,
) -> tuple[str | None, str | None]:
    message_ts, slack_error = await post_slack_thread_reply_with_ts(
        channel_id, thread_ts, message, blocks=blocks
    )
    if message_ts:
        if langgraph_client is None:
            langgraph_client = get_client(url=LANGGRAPH_URL)
        await store_slack_message_run_mapping(langgraph_client, channel_id, thread_ts, message_ts)
        if run_id and normalized_text is not None:
            await store_last_outgoing_message_for_thread(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts,
                normalized_text,
            )
    return message_ts, slack_error
