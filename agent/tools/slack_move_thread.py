import os
import re
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..utils.dashboard_links import dashboard_thread_url
from ..utils.slack import (
    append_slack_web_link_footer,
    bind_slack_thread_id,
    delete_slack_thread_associations,
    get_active_slack_thread,
    lookup_slack_thread_run_mapping,
    post_slack_top_level_message_with_ts,
    store_slack_run_mapping,
)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)
_MESSAGE_MAX_CHARS = 2800
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _slack_error_hint(error: str | None) -> str:
    if error in {"channel_not_found", "not_in_channel"}:
        return "Slack rejected the destination channel; verify the channel ID and bot access."
    if error and error.startswith("rate_limited"):
        return "Slack rate limited the request; wait before retrying."
    if error == "missing_slack_bot_token":
        return "Slack bot token is missing; do not retry."
    return "Slack could not create the destination thread; retry once."


def _new_slack_context(
    current: Mapping[str, Any], channel_id: str, thread_ts: str
) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "triggering_user_id": current.get("triggering_user_id", ""),
        "triggering_user_name": current.get("triggering_user_name", ""),
        "triggering_user_email": current.get("triggering_user_email", ""),
        "triggering_event_ts": thread_ts,
    }


async def _finish_existing_move(
    client: Any,
    thread_id: str,
    active: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    channel_id = str(active.get("channel_id") or "")
    thread_ts = str(active.get("thread_ts") or "")
    await bind_slack_thread_id(client, channel_id, thread_ts, thread_id)
    await delete_slack_thread_associations(
        client,
        str(source.get("channel_id") or ""),
        str(source.get("thread_ts") or ""),
        expected_thread_id=thread_id,
    )
    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }


async def slack_move_thread(
    message: str,
    channel_id: str | None = None,
) -> dict[str, Any]:
    """Move the current Open SWE thread to a new Slack thread."""
    config = get_config()
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    configured_slack = configurable.get("slack_thread")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "Missing thread_id in config"}
    if not isinstance(configured_slack, Mapping):
        return {"success": False, "error": "Missing slack_thread config"}

    clean_message = message.strip() if isinstance(message, str) else ""
    if not clean_message:
        return {"success": False, "error": "message is required"}
    if len(clean_message) > _MESSAGE_MAX_CHARS:
        return {
            "success": False,
            "error": "message is too long",
            "max_chars": _MESSAGE_MAX_CHARS,
            "actual_chars": len(clean_message),
        }

    client = get_client(url=LANGGRAPH_URL)
    active = await get_active_slack_thread(client, thread_id, configured_slack)
    if not active:
        return {"success": False, "error": "Current Slack location is unavailable"}

    source_channel = str(configured_slack.get("channel_id") or "")
    source_ts = str(configured_slack.get("thread_ts") or "")
    active_channel = str(active.get("channel_id") or "")
    active_ts = str(active.get("thread_ts") or "")
    if (active_channel, active_ts) != (source_channel, source_ts):
        try:
            return await _finish_existing_move(client, thread_id, active, configured_slack)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": f"Move cleanup failed: {exc}",
                "retryable": True,
            }

    target_channel = (channel_id or active_channel).strip()
    if not _CHANNEL_ID_RE.fullmatch(target_channel):
        return {"success": False, "error": "channel_id must be a Slack channel ID"}

    root_text = append_slack_web_link_footer(clean_message, dashboard_thread_url(thread_id))
    new_ts, slack_error = await post_slack_top_level_message_with_ts(
        target_channel,
        root_text,
        unfurl_links=False,
        unfurl_media=False,
    )
    if not new_ts:
        return {
            "success": False,
            "error": slack_error or "Slack post failed",
            "slack_error": slack_error,
            "hint": _slack_error_hint(slack_error),
        }

    new_slack = _new_slack_context(active, target_channel, new_ts)
    destination_bound = False
    try:
        await bind_slack_thread_id(client, target_channel, new_ts, thread_id)
        destination_bound = True
        await client.threads.update(
            thread_id=thread_id,
            metadata={"source": "slack", "source_context": {"slack_thread": new_slack}},
        )
        persisted = await get_active_slack_thread(client, thread_id)
        if not persisted or (persisted.get("channel_id"), persisted.get("thread_ts")) != (
            target_channel,
            new_ts,
        ):
            raise RuntimeError("destination metadata did not persist")
    except Exception as exc:  # noqa: BLE001
        if destination_bound:
            try:
                await delete_slack_thread_associations(
                    client,
                    target_channel,
                    new_ts,
                    expected_thread_id=thread_id,
                )
            except Exception:  # noqa: BLE001
                pass
        return {
            "success": False,
            "error": f"Could not persist Slack move: {exc}",
            "retryable": True,
        }

    source_run = await lookup_slack_thread_run_mapping(client, source_channel, source_ts)
    if isinstance(source_run, Mapping):
        run_id = source_run.get("run_id")
        if isinstance(run_id, str) and run_id:
            await store_slack_run_mapping(
                client,
                target_channel,
                new_ts,
                run_id,
                message_ts=new_ts,
                triggering_user_id=(
                    str(source_run.get("triggering_user_id"))
                    if source_run.get("triggering_user_id")
                    else None
                ),
            )

    try:
        await delete_slack_thread_associations(
            client,
            source_channel,
            source_ts,
            expected_thread_id=thread_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"Move cleanup failed: {exc}",
            "retryable": True,
            "channel_id": target_channel,
            "thread_ts": new_ts,
        }

    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": target_channel,
        "thread_ts": new_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }
