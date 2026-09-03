"""Posting into the Slack session a run belongs to.

Both Slack posting tools and the choice tool land here, so message posting,
run-mapping bookkeeping and the option blocks Slack's interactivity endpoint
recognizes have one implementation.
"""

import json
from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.slack.client import (
    convert_mentions_to_slack_format,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
    store_slack_message_run_mapping,
)
from agent.utils.run_usage import RunUsageSummary
from agent.utils.thread_ops import langgraph_client as get_langgraph_client


def current_run_id() -> str | None:
    """This run's id, which rides at the top of the config as well as inside it."""
    config = get_config()
    candidates = [config.get("run_id"), RunConfig.from_config(config).run_id]
    return next((str(candidate) for candidate in candidates if candidate), None)


def triggering_user_id(cfg: RunConfig) -> str | None:
    return (cfg.slack_thread.triggering_user_id or None) if cfg.slack_thread else None


def option_blocks(message: str, options: list[str] | None) -> list[dict[str, Any]] | None:
    """Blocks for a choice, using the action ids Slack interactivity routes on."""
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
                    "text": {"type": "plain_text", "text": "Approve workflow push", "emoji": True},
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
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
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


def slack_post_failure_hint(slack_error: str | None) -> str:
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


async def post_session_message(
    *,
    channel_id: str,
    thread_ts: str,
    post_thread_ts: str,
    message: str,
    blocks: list[dict[str, Any]] | None = None,
    usage: RunUsageSummary | None = None,
    agent_thread_id: str | None = None,
    run_id: str | None = None,
    triggering_user: str | None = None,
) -> dict[str, Any]:
    """Post one message into a Slack session and map it to the run behind it."""
    client = get_langgraph_client()
    async with slack_thread_mutation_lock(client, channel_id, thread_ts):
        message = convert_mentions_to_slack_format(message)
        message_ts, slack_error = await post_slack_thread_reply_with_ts(
            channel_id,
            post_thread_ts or thread_ts,
            message,
            blocks=blocks,
            usage=usage,
            agent_thread_id=agent_thread_id,
        )
        if message_ts:
            await store_slack_message_run_mapping(
                client,
                channel_id,
                thread_ts,
                message_ts,
                run_id=run_id,
                triggering_user_id=triggering_user,
            )
    if message_ts is None:
        return {
            "success": False,
            "error": slack_error or "post failed",
            "slack_error": slack_error,
            "message_chars": len(message),
            "hint": slack_post_failure_hint(slack_error),
        }
    return {"success": True, "message_ts": message_ts}
