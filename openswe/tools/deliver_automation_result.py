import logging
from typing import Literal, TypedDict

from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.schedules.delivery import CreatorDelivery, DeliveryRefused
from openswe.slack.http import SlackRequestError

logger = logging.getLogger(__name__)


class AutomationDelivered(TypedDict):
    success: Literal[True]
    recipient: str
    channel_id: str
    message_ts: str


class AutomationDeliveryError(TypedDict):
    success: Literal[False]
    error: str


@audit_tool()
async def deliver_automation_result(
    message: str,
) -> AutomationDelivered | AutomationDeliveryError:
    """DM this automation run's result to the person who created the automation."""
    if not message.strip():
        return {"success": False, "error": "message is required"}
    cfg = RunConfig.from_runtime()
    try:
        delivery = await CreatorDelivery.for_run(cfg)
        channel_id, message_ts = await delivery.send(message)
    except DeliveryRefused as exc:
        return {"success": False, "error": str(exc)}
    except SlackRequestError as exc:
        logger.warning(
            "Slack refused an automation result DM",
            extra={
                "schedule_id": cfg.schedule_id,
                "agent_thread_id": cfg.thread_id,
                "slack_error": exc.code,
            },
        )
        return {"success": False, "error": exc.code or "post_failed"}
    return {
        "success": True,
        "recipient": delivery.creator_login,
        "channel_id": channel_id,
        "message_ts": message_ts,
    }
