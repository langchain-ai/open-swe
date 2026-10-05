import re
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config
from langgraph_sdk.client import LangGraphClient

from agent.run_config import RunConfig
from agent.slack.breakout_destination import resolve_breakout_destination
from agent.slack.channels import SlackChannel
from agent.slack.client import get_active_slack_thread
from agent.slack.move import move_slack_thread, rebind_slack_thread
from agent.source_context import SlackThreadRef
from agent.threads.summary import thread_is_private
from agent.tools.errors import ToolError
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

_MESSAGE_MAX_CHARS = 2800
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


async def _finish_existing_move(
    client: LangGraphClient,
    thread_id: str,
    active: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    destination = SlackThreadRef.model_validate(dict(active))
    await rebind_slack_thread(
        client, thread_id, SlackThreadRef.model_validate(dict(source)), destination
    )
    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": destination.channel_id,
        "thread_ts": destination.thread_ts,
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
        raise ToolError("Missing thread_id in config")
    if not isinstance(configured_slack, Mapping):
        raise ToolError("Missing slack_thread config")

    clean_message = message.strip() if isinstance(message, str) else ""
    if not clean_message:
        raise ToolError("message is required")
    if len(clean_message) > _MESSAGE_MAX_CHARS:
        raise ToolError(
            "message is too long",
            details={"max_chars": _MESSAGE_MAX_CHARS, "actual_chars": len(clean_message)},
        )

    client = langgraph_client()
    try:
        metadata = thread_metadata(await client.threads.get(thread_id))
    except Exception as exc:
        raise ToolError("Cannot verify thread credential scope") from exc
    if thread_is_private(metadata):
        raise ToolError("Private threads cannot be moved; start a separate public thread instead")
    active = await get_active_slack_thread(client, thread_id, configured_slack)
    if not active:
        raise ToolError("Current Slack location is unavailable")

    source_channel = str(configured_slack.get("channel_id") or "")
    source_ts = str(configured_slack.get("thread_ts") or "")
    active_channel = str(active.get("channel_id") or "")
    active_ts = str(active.get("thread_ts") or "")
    if (active_channel, active_ts) != (source_channel, source_ts):
        try:
            return await _finish_existing_move(client, thread_id, active, configured_slack)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Move cleanup failed: {exc}", details={"retryable": True}) from exc

    destination = await resolve_breakout_destination(
        active_channel, channel_id, workspace=RunConfig.from_runtime().workspace_slug
    )
    target_channel = destination.channel_id
    if not _CHANNEL_ID_RE.fullmatch(target_channel):
        raise ToolError("channel_id must be a Slack channel ID")
    for candidate in dict.fromkeys((active_channel, target_channel)):
        channel = await SlackChannel.load(candidate, use_cache=False)
        if channel is None or not channel.public:
            raise ToolError("Breakouts only work from and to public channels.")

    return await move_slack_thread(client, thread_id, active, target_channel, clean_message)
