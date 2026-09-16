import asyncio
import logging
from typing import Any, Literal
from weakref import WeakValueDictionary

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from agent.run_config import RunConfig
from agent.slack.client import (
    append_slack_web_link_footer,
    post_slack_thread_reply_with_ts,
    post_slack_top_level_message_with_ts,
)
from agent.store import TypedStore, now_iso
from agent.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

_NOTIFICATION_NAMESPACE = ("automation_notifications",)
_MAX_MESSAGE_CHARS = 3_000
_MAX_TOP_LEVEL_LINES = 4
_notification_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

NotificationStatus = Literal["pending", "posted", "delivered"]


class AutomationNotification(BaseModel):
    """One channel notification per scheduled thread.

    ``pending`` means the channel post was attempted and its outcome is
    unknown; only ``posted`` (``message_ts`` confirmed) may be resumed, and only
    a deleted record allows the channel message to be sent at all.
    """

    status: NotificationStatus = "pending"
    channel_id: str = ""
    schedule_id: str = ""
    message_ts: str = ""
    notified_at: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _known_status(cls, value: object) -> object:
        # A status this version does not know must never read as delivered.
        return value if value in ("pending", "posted", "delivered") else "pending"


def _notification_store() -> TypedStore[AutomationNotification]:
    return TypedStore(_NOTIFICATION_NAMESPACE, AutomationNotification)


def _notification_lock(thread_id: str) -> asyncio.Lock:
    lock = _notification_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _notification_locks[thread_id] = lock
    return lock


async def _release_reservation(thread_id: str) -> None:
    try:
        await _notification_store().delete(thread_id)
    except Exception:
        logger.exception(
            "Failed to release automation notification", extra={"thread_id": thread_id}
        )


async def _mark_action_posted(thread_id: str, notified_at: str) -> None:
    try:
        await get_client().threads.update(
            thread_id=thread_id,
            metadata={"automation_action_posted_at": notified_at},
        )
    except Exception:
        logger.exception("Failed to mark automation action posted", extra={"thread_id": thread_id})


async def notify_automation_channel(content: str, summary: str = "") -> dict[str, Any]:
    """Implement the `notify_automation_channel` tool."""
    cfg = RunConfig.from_runtime()
    if cfg.source != "schedule":
        return {"success": False, "error": "This tool is only available to scheduled runs"}

    notification = cfg.automation_slack_notification
    if notification is None or notification.mode != "on_action":
        return {
            "success": False,
            "error": "This schedule is not configured for action-only Slack notifications",
        }

    channel_id = notification.channel_id
    if not channel_id:
        return {"success": False, "error": "Missing configured automation Slack channel"}

    schedule_id = cfg.schedule_id
    if not schedule_id or notification.schedule_id != schedule_id:
        return {"success": False, "error": "Invalid automation notification configuration"}

    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "Missing scheduled thread ID"}

    clean_content = content.strip()
    clean_summary = summary.strip()
    if not clean_content:
        return {"success": False, "error": "Content cannot be empty"}
    if len(clean_content) > _MAX_MESSAGE_CHARS:
        return {
            "success": False,
            "error": f"Content must be at most {_MAX_MESSAGE_CHARS} characters",
        }
    if len(clean_content.splitlines()) > _MAX_TOP_LEVEL_LINES and not clean_summary:
        return {
            "success": False,
            "error": f"Summary is required when content exceeds {_MAX_TOP_LEVEL_LINES} lines",
        }
    if clean_summary and len(clean_summary.splitlines()) > _MAX_TOP_LEVEL_LINES:
        return {
            "success": False,
            "error": f"Summary must be at most {_MAX_TOP_LEVEL_LINES} lines",
        }
    if len(clean_summary) > _MAX_MESSAGE_CHARS:
        return {
            "success": False,
            "error": f"Summary must be at most {_MAX_MESSAGE_CHARS} characters",
        }

    store = _notification_store()
    async with _notification_lock(thread_id):
        try:
            existing = await store.get(thread_id)
        except Exception:
            logger.exception(
                "Failed to check automation notification", extra={"thread_id": thread_id}
            )
            return {"success": False, "error": "Could not check the Slack notification state"}

        if existing is not None and existing.status == "delivered":
            if existing.notified_at:
                await _mark_action_posted(thread_id, existing.notified_at)
            return {
                "success": True,
                "already_notified": True,
                "message_ts": existing.message_ts,
            }

        if existing is not None and not existing.message_ts:
            logger.error(
                "Automation notification outcome is unknown", extra={"thread_id": thread_id}
            )
            return {
                "success": False,
                "error": (
                    "A previous Slack notification for this thread did not record its "
                    "outcome; not posting again because it may already be in the channel"
                ),
            }

        if existing is None:
            record = AutomationNotification(
                status="pending", channel_id=channel_id, schedule_id=schedule_id
            )
            try:
                await store.put(thread_id, record)
            except Exception:
                logger.exception(
                    "Failed to reserve automation notification", extra={"thread_id": thread_id}
                )
                return {"success": False, "error": "Could not reserve the Slack notification"}

            title = (notification.schedule_name or "").strip()
            channel_message = clean_summary or clean_content
            text = f"*Open SWE automation:* {title or 'Scheduled agent'}\n\n{channel_message}"
            text = append_slack_web_link_footer(text, dashboard_thread_url(thread_id))
            try:
                posted_ts, slack_error = await post_slack_top_level_message_with_ts(
                    channel_id,
                    text,
                    unfurl_links=False,
                    unfurl_media=False,
                )
            except Exception:
                logger.exception("Automation Slack post raised", extra={"thread_id": thread_id})
                await _release_reservation(thread_id)
                return {"success": False, "error": "Slack post failed unexpectedly"}
            if posted_ts is None:
                await _release_reservation(thread_id)
                return {
                    "success": False,
                    "error": f"Slack post failed: {slack_error or 'unknown error'}",
                    "slack_error": slack_error,
                }

            record = record.model_copy(update={"status": "posted", "message_ts": posted_ts})
            try:
                await store.put(thread_id, record)
            except Exception:
                logger.exception(
                    "Failed to record the automation Slack post",
                    extra={"thread_id": thread_id},
                )
                return {
                    "success": False,
                    "error": (
                        "Slack post succeeded but its state could not be saved; this thread "
                        "will not notify again"
                    ),
                    "message_ts": posted_ts,
                }
        else:
            record = existing

        message_ts = record.message_ts
        if clean_summary:
            try:
                reply_ts, slack_error = await post_slack_thread_reply_with_ts(
                    channel_id,
                    message_ts,
                    clean_content,
                    unfurl_links=False,
                    unfurl_media=False,
                )
            except Exception:
                logger.exception("Automation Slack reply raised", extra={"thread_id": thread_id})
                return {"success": False, "error": "Slack thread reply failed unexpectedly"}
            if reply_ts is None:
                return {
                    "success": False,
                    "error": f"Slack thread reply failed: {slack_error or 'unknown error'}",
                    "slack_error": slack_error,
                }

        delivered = record.model_copy(update={"status": "delivered", "notified_at": now_iso()})
        try:
            await store.put(thread_id, delivered)
        except Exception:
            # Slack already has both messages; failing the call here would only invite a
            # duplicate thread reply on the next attempt.
            logger.exception(
                "Failed to finalize automation notification", extra={"thread_id": thread_id}
            )
        await _mark_action_posted(thread_id, delivered.notified_at)
        return {"success": True, "message_ts": message_ts}
