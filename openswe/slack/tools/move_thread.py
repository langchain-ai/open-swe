import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from langgraph.config import get_config
from pydantic import Field

from openswe.run_config import RunConfig
from openswe.slack.breakout_destination import resolve_breakout_destination
from openswe.slack.client import get_active_slack_thread
from openswe.slack.move import move_slack_thread, rebind_slack_thread
from openswe.source_context import SlackThreadRef
from openswe.threads.store import THREADS
from openswe.threads.summary import thread_is_private
from openswe.utils.dashboard_links import dashboard_thread_url

_MESSAGE_MAX_CHARS = 2800
_CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]+$")


async def _finish_existing_move(
    thread_id: str,
    active: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    destination = SlackThreadRef.model_validate(dict(active))
    await rebind_slack_thread(thread_id, SlackThreadRef.model_validate(dict(source)), destination)
    return {
        "success": True,
        "thread_id": thread_id,
        "channel_id": destination.channel_id,
        "thread_ts": destination.thread_ts,
        "dashboard_url": dashboard_thread_url(thread_id),
    }


@dataclass(frozen=True)
class CheckedMove:
    """A move the checks let through: the thread, where it is, and where it goes."""

    thread_id: str
    active: Mapping[str, Any]
    target_channel: str
    message: str


async def check_move(message: str, destination: str | None = None) -> CheckedMove | dict[str, Any]:
    """The run's move once the checks pass, or the tool's answer when they don't.

    The answer is an error, or a move an earlier call left unfinished, finished now.
    The thread is the run's own, from its config. Without ``destination`` (a person's
    bare move) it goes where the workspace sends breakouts.
    """
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

    try:
        thread = await THREADS.get(thread_id)
    except Exception:
        thread = None
    if thread is None:
        return {"success": False, "error": "Cannot verify thread credential scope"}
    if thread_is_private(thread.metadata):
        return {
            "success": False,
            "error": "Private threads cannot be moved; start a separate public thread instead",
        }
    active = await get_active_slack_thread(thread_id, configured_slack)
    if not active:
        return {"success": False, "error": "Current Slack location is unavailable"}

    source_channel = str(configured_slack.get("channel_id") or "")
    source_ts = str(configured_slack.get("thread_ts") or "")
    active_channel = str(active.get("channel_id") or "")
    active_ts = str(active.get("thread_ts") or "")
    if (active_channel, active_ts) != (source_channel, source_ts):
        try:
            return await _finish_existing_move(thread_id, active, configured_slack)
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
    return CheckedMove(thread_id, active, target_channel, clean_message)


async def slack_move_thread(
    message: str,
    destination: Annotated[str, Field(pattern=r"^[CGD][A-Z0-9]+$")],
) -> dict[str, Any]:
    """Implement the `slack_move_thread` tool."""
    if not _CHANNEL_ID_RE.fullmatch(destination):
        return {"success": False, "error": "destination must be a Slack channel ID"}
    checked = await check_move(message, destination)
    if not isinstance(checked, CheckedMove):
        return checked
    return await move_slack_thread(
        checked.thread_id,
        checked.active,
        checked.target_channel,
        checked.message,
    )
