import json
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.platforms.slack.client import (
    convert_mentions_to_slack_format,
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
    store_slack_message_run_mapping,
)
from agent.utils.run_usage import RunUsageSummary, summarize_run_usage
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
    config = get_config()
    configurable = config.get("configurable", {})
    run_id = _current_run_id(config)
    slack_thread = configurable.get("slack_thread", {})
    thread_id = configurable.get("thread_id")
    client = get_langgraph_client()
    active = await get_active_slack_thread(
        client,
        thread_id if isinstance(thread_id, str) else None,
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
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    from agent.platforms.slack.code_channels import is_code_channel_session

    reply_thread_ts = active.get("reply_thread_ts")
    post_thread_ts = (
        str(reply_thread_ts)
        if is_code_channel_session(str(thread_ts)) and isinstance(reply_thread_ts, str)
        else str(thread_ts)
    )

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
            triggering_user_id=_triggering_user_id(configurable),
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


def _current_run_id(config: Mapping[str, Any]) -> str | None:
    candidates = [config.get("run_id")]
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        candidates.append(configurable.get("run_id"))
    return next((str(candidate) for candidate in candidates if candidate), None)


def _triggering_user_id(configurable: object) -> str | None:
    if not isinstance(configurable, dict):
        return None
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return None
    user_id = slack_thread.get("triggering_user_id")
    return user_id if isinstance(user_id, str) and user_id else None


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
