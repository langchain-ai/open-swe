"""Deliver trusted schedule results to a static ``deliver_to`` target.

The run's delivery step (``_channels/delivery.py``) posts there for a scheduled
run without a thread origin (``runtime.thread.origin``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from managed_deepagents._channels.binding import SLACK_BOT_TOKEN_ENV
from managed_deepagents._channels.trigger.client import create_trigger_client
from managed_deepagents._identity_runtime import _field
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.types import TriggerChannelActionRequest
    from managed_deepagents._channels.types import (
        ChannelAddress,
        ChannelDestinationProviderConversation,
        ChannelDestinationProviderThread,
        JsonValue,
    )

PostFn = Callable[..., Awaitable[object] | object]
DeliverOutcome = Literal["posted", "skipped", "failed"]


class NormalizedScheduleDeliverTo(TypedDict):
    channel: str
    to: ChannelDestinationProviderConversation | ChannelDestinationProviderThread
    auto_post: bool


def get_deployment_id(env: Mapping[str, str] | None = None) -> str | None:
    """Return the deployment ID supplied by the managed runtime."""
    resolved_env = os.environ if env is None else env
    for key in ("LANGSMITH_HOST_PROJECT_ID", "LANGGRAPH_CLOUD_ID"):
        value = (resolved_env.get(key) or "").strip()
        if value:
            return value
    return None


def read_schedule_deliver_to(  # noqa: PLR0911 - validation short-circuits
    raw: object,
) -> NormalizedScheduleDeliverTo | None:
    """Normalize a schedule ``deliver_to`` target.

    Accepts camelCase (TS compile) and snake_case (Python compile) shapes.
    """
    if not isinstance(raw, Mapping):
        return None
    channel = raw.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        return None
    to_raw = raw.get("to")
    if not isinstance(to_raw, Mapping):
        return None
    dest_type = to_raw.get("type")
    conversation_id = to_raw.get("conversation_id", to_raw.get("conversationId"))
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    normalized_to: ChannelDestinationProviderConversation | ChannelDestinationProviderThread
    if dest_type == "provider_conversation":
        normalized_to = {
            "type": "provider_conversation",
            "conversation_id": conversation_id.strip(),
        }
    elif dest_type == "provider_thread":
        thread_id = to_raw.get("thread_id", to_raw.get("threadId"))
        if not isinstance(thread_id, str) or not thread_id.strip():
            return None
        normalized_to = {
            "type": "provider_thread",
            "conversation_id": conversation_id.strip(),
            "thread_id": thread_id.strip(),
        }
    else:
        return None
    auto_post_raw = raw.get("auto_post", raw.get("autoPost"))
    auto_post = True if auto_post_raw is None else bool(auto_post_raw)
    result: NormalizedScheduleDeliverTo = {
        "channel": channel.strip(),
        "to": normalized_to,
        "auto_post": auto_post,
    }
    return result


def address_from_deliver_to(
    to: ChannelDestinationProviderConversation | ChannelDestinationProviderThread,
) -> ChannelAddress:
    """Map a deliver_to destination onto a runtime channel address."""
    if to["type"] == "provider_thread":
        return {
            "conversation_id": to["conversation_id"],
            "thread_id": to["thread_id"],
        }
    return {"conversation_id": to["conversation_id"]}


async def deliver_schedule_result(  # noqa: C901, PLR0911 - delivery outcomes
    *,
    deliver_to: NormalizedScheduleDeliverTo,
    reply_text: str,
    bot_token: str | None = None,
    post: PostFn | None = None,
) -> DeliverOutcome:
    """Post result text; the run's delivery step fails the run on a failed outcome."""
    text = reply_text.strip()
    if not text or not deliver_to["auto_post"]:
        return "skipped"
    if deliver_to["channel"] != "slack":
        logger.error(
            'schedule deliver_to channel "%s" is not supported yet',
            logger.log_identifier(deliver_to["channel"]),
        )
        return "failed"

    address = address_from_deliver_to(deliver_to["to"])
    if post is not None:
        try:
            result = post(
                text=text,
                conversation_id=address["conversation_id"],
                thread_id=address.get("thread_id"),
            )
            if hasattr(result, "__await__"):
                await cast("Awaitable[object]", result)
            return "posted"
        except Exception as error:  # noqa: BLE001 - return a failed delivery outcome
            logger.error(
                "schedule Slack delivery failed (%s)",
                logger.safe_error_label(error),
                error=error,
            )
            return "failed"

    deployment_id = get_deployment_id()
    try:
        client = create_trigger_client()
    except ValueError:
        client = None
    if client is not None and deployment_id:
        action: TriggerChannelActionRequest = {
            "type": "post",
            "action_id": str(uuid.uuid4()),
            "address": dict(address),
            "message": {"text": text},
        }
        try:
            await client.post_deployment_action(
                deployment_id,
                deliver_to["channel"],
                action,
            )
            return "posted"
        except Exception as error:  # noqa: BLE001 - return a failed delivery outcome
            logger.error(
                "schedule Trigger channel delivery failed (%s)",
                logger.safe_error_label(error),
                error=error,
            )
            return "failed"

    token = (
        bot_token if bot_token is not None else os.environ.get(SLACK_BOT_TOKEN_ENV) or ""
    ).strip()
    if not token:
        logger.error(
            "schedule deliver_to requires Trigger channel configuration or "
            "SLACK_BOT_TOKEN; skipping auto-post"
        )
        return "failed"

    try:
        from managed_deepagents._channels.slack.transport import (  # noqa: PLC0415
            create_slack_web_api_transport,
        )

        transport = create_slack_web_api_transport(bot_token=token)
        await transport.post(
            message={"type": "content", "content": text},
            target=cast("JsonValue", dict(address)),
        )
        return "posted"
    except Exception as error:  # noqa: BLE001 - return a failed delivery outcome
        logger.error(
            "schedule Slack delivery failed (%s)",
            logger.safe_error_label(error),
            error=error,
        )
        return "failed"


def is_trusted_schedule_delivery_run(
    configurable: Mapping[str, object] | None, metadata: object
) -> bool:
    """True when this run is a managed cron fire (not a caller-forged HTTP invoke).

    Only the server writes the principal, so its cron marker cannot be forged.
    ``metadata`` is the run's, which marks a managed cron fire.
    """
    if not isinstance(configurable, Mapping):
        return False
    if _field(configurable.get("langgraph_auth_user"), "mda_scheduled_run") is not True:
        return False
    return isinstance(metadata, Mapping) and metadata.get("mda_managed") is True


__all__ = [
    "NormalizedScheduleDeliverTo",
    "address_from_deliver_to",
    "deliver_schedule_result",
    "get_deployment_id",
    "is_trusted_schedule_delivery_run",
    "read_schedule_deliver_to",
]
