import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph_sdk.client import LangGraphClient
from pydantic import Field

from openswe.run_config import RunConfig
from openswe.slack.breakout_destination import resolve_breakout_destination
from openswe.slack.client import get_active_slack_thread
from openswe.slack.move import move_slack_thread, rebind_slack_thread
from openswe.source_context import SlackThreadRef
from openswe.threads.summary import thread_is_private
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_MESSAGE_MAX_CHARS = 2800
_CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]+$")


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
    destination: Annotated[str, Field(pattern=r"^[CGD][A-Z0-9]+$")],
) -> dict[str, Any]:
    """Implement the `slack_move_thread` tool."""
    if not _CHANNEL_ID_RE.fullmatch(destination):
        return {"success": False, "error": "destination must be a Slack channel ID"}
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
        logger.warning(
            "Could not load the thread to verify its credential scope",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
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

    resolved = await resolve_breakout_destination(
        active_channel, destination, workspace=RunConfig.from_runtime().workspace_slug
    )
    target_channel = resolved.channel_id
    if error := await resolved.channel_error(active_channel):
        return {"success": False, "error": error}

    return await move_slack_thread(client, thread_id, active, target_channel, clean_message)
