import re
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from agent.slack.client import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    get_active_slack_thread,
)
from agent.slack.move import move_slack_thread
from agent.threads.summary import thread_is_private
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

_MESSAGE_MAX_CHARS = 2800
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


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
    """Implement the `slack_move_thread` tool."""
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

    client = langgraph_client()
    try:
        metadata = thread_metadata(await client.threads.get(thread_id))
    except Exception:
        return {"success": False, "error": "Cannot verify thread credential scope"}
    if thread_is_private(metadata):
        return {
            "success": False,
            "error": "Private threads cannot be moved; start a separate public thread instead",
        }
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

    return await move_slack_thread(client, thread_id, active, target_channel, clean_message)
