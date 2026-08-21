import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from weakref import WeakValueDictionary

from langgraph.config import get_config

from ..store import delete_value, get_value, now_iso, put_value
from ..utils.dashboard_links import dashboard_thread_url
from ..utils.slack_api import post_slack_top_level_message_with_ts
from ..utils.slack_format import append_slack_web_link_footer

logger = logging.getLogger(__name__)

_NOTIFICATION_NAMESPACE = ["automation_notifications"]
_MAX_MESSAGE_CHARS = 3_000
_notification_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _notification_lock(thread_id: str) -> asyncio.Lock:
    lock = _notification_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _notification_locks[thread_id] = lock
    return lock


async def _release_reservation(thread_id: str) -> None:
    try:
        await delete_value(_NOTIFICATION_NAMESPACE, thread_id)
    except Exception:
        logger.exception("Failed to release automation notification for %s", thread_id)


async def notify_automation_channel(message: str) -> dict[str, Any]:
    """Notify the configured automation channel once after a concrete requested action."""
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    if not isinstance(configurable, Mapping) or configurable.get("source") != "schedule":
        return {"success": False, "error": "This tool is only available to scheduled runs"}

    notification = configurable.get("automation_slack_notification")
    if not isinstance(notification, Mapping) or notification.get("mode") != "on_action":
        return {
            "success": False,
            "error": "This schedule is not configured for action-only Slack notifications",
        }

    channel_id = notification.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id:
        return {"success": False, "error": "Missing configured automation Slack channel"}

    schedule_id = configurable.get("schedule_id")
    if not isinstance(schedule_id, str) or notification.get("schedule_id") != schedule_id:
        return {"success": False, "error": "Invalid automation notification configuration"}

    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "Missing scheduled thread ID"}

    clean_message = message.strip()
    if not clean_message:
        return {"success": False, "error": "Message cannot be empty"}
    if len(clean_message) > _MAX_MESSAGE_CHARS:
        return {
            "success": False,
            "error": f"Message must be at most {_MAX_MESSAGE_CHARS} characters",
        }

    async with _notification_lock(thread_id):
        try:
            existing = await get_value(_NOTIFICATION_NAMESPACE, thread_id)
        except Exception:
            logger.exception("Failed to check automation notification for %s", thread_id)
            return {"success": False, "error": "Could not check the Slack notification state"}
        if existing is not None:
            return {
                "success": True,
                "already_notified": True,
                "message_ts": existing.get("message_ts"),
            }

        pending = {
            "status": "pending",
            "channel_id": channel_id,
            "schedule_id": schedule_id,
        }
        try:
            await put_value(_NOTIFICATION_NAMESPACE, thread_id, pending)
        except Exception:
            logger.exception("Failed to reserve automation notification for %s", thread_id)
            return {"success": False, "error": "Could not reserve the Slack notification"}

        schedule_name = notification.get("schedule_name")
        title = schedule_name.strip() if isinstance(schedule_name, str) else ""
        text = f"*Open SWE automation:* {title or 'Scheduled agent'}\n\n{clean_message}"
        text = append_slack_web_link_footer(text, dashboard_thread_url(thread_id))
        try:
            message_ts, slack_error = await post_slack_top_level_message_with_ts(
                channel_id,
                text,
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception:
            logger.exception("Automation Slack post raised for %s", thread_id)
            await _release_reservation(thread_id)
            return {"success": False, "error": "Slack post failed unexpectedly"}
        if message_ts is None:
            await _release_reservation(thread_id)
            return {
                "success": False,
                "error": f"Slack post failed: {slack_error or 'unknown error'}",
                "slack_error": slack_error,
            }

        delivered = {
            **pending,
            "status": "delivered",
            "message_ts": message_ts,
            "notified_at": now_iso(),
        }
        try:
            await put_value(_NOTIFICATION_NAMESPACE, thread_id, delivered)
        except Exception:
            logger.exception("Failed to finalize automation notification for %s", thread_id)
        return {"success": True, "message_ts": message_ts}
