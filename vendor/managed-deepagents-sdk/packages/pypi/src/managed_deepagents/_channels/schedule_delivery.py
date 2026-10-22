"""Deliver trusted schedule results through captured or explicit channel targets."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.config import get_config
from langgraph.runtime import Runtime

from managed_deepagents._channels.assistant_text import (
    extract_assistant_content,
    extract_assistant_text,
)
from managed_deepagents._channels.binding import (
    SLACK_BOT_TOKEN_ENV,
    _current_routing,
    resolve_runtime_channel,
)
from managed_deepagents._channels.trigger.client import create_trigger_client
from managed_deepagents._identity_runtime import (
    _ambient_configurable,
    _configurable_from_runtime,
    _field,
)
from managed_deepagents._log import logger
from managed_deepagents._memory.async_util import await_if_needed

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.types import TriggerChannelActionRequest
    from managed_deepagents._channels.types import (
        ChannelAddress,
        ChannelDestinationProviderConversation,
        ChannelDestinationProviderThread,
        JsonValue,
    )

_schedule_metadata_for_tests: Callable[[], Mapping[str, object] | None] | None = None


def set_schedule_metadata_for_tests(
    loader: Callable[[], Mapping[str, object] | None] | None = None,
) -> None:
    """Override LangGraph run metadata resolution. Test-only."""
    global _schedule_metadata_for_tests  # noqa: PLW0603
    _schedule_metadata_for_tests = loader


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
    if dest_type == "provider_conversation":
        normalized_to: ChannelDestinationProviderConversation | ChannelDestinationProviderThread = {
            "type": "provider_conversation",
            "conversation_id": conversation_id.strip(),
        }
    elif dest_type == "provider_thread":
        thread_id = to_raw.get("thread_id", to_raw.get("threadId"))
        if not isinstance(thread_id, str) or not thread_id.strip():
            return None
        normalized_to = cast(
            "ChannelDestinationProviderThread",
            {
                "type": "provider_thread",
                "conversation_id": conversation_id.strip(),
                "thread_id": thread_id.strip(),
            },
        )
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
    """Post result text; the middleware fails the run on a failed outcome."""
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
        posted = transport.post(
            message={"type": "content", "content": text},
            target=cast("JsonValue", dict(address)),
        )
        if hasattr(posted, "__await__"):
            await cast("Awaitable[object]", posted)
        return "posted"
    except Exception as error:  # noqa: BLE001 - return a failed delivery outcome
        logger.error(
            "schedule Slack delivery failed (%s)",
            logger.safe_error_label(error),
        )
        return "failed"


def is_trusted_schedule_delivery_run(configurable: Mapping[str, object] | None) -> bool:
    """True when this run is a managed cron fire (not a caller-forged HTTP invoke).

    Only the server writes the principal, so its cron marker cannot be forged.
    """
    if not isinstance(configurable, Mapping):
        return False
    if _field(configurable.get("langgraph_auth_user"), "mda_scheduled_run") is not True:
        return False
    metadata = _resolve_schedule_metadata()
    return isinstance(metadata, Mapping) and metadata.get("mda_managed") is True


def _resolve_schedule_metadata() -> Mapping[str, object] | None:
    if _schedule_metadata_for_tests is not None:
        return _schedule_metadata_for_tests()
    try:
        config = get_config()
    except RuntimeError:
        return None
    if isinstance(config, Mapping):
        metadata = config.get("metadata")
        if isinstance(metadata, Mapping):
            return cast("Mapping[str, object]", metadata)
    return None


async def _deliver_captured_result(
    configurable: Mapping[str, object] | None,
    output: Mapping[str, object],
    deliver: Callable[..., Awaitable[object]],
) -> None:
    reply_text = extract_assistant_text(output)
    routing = _current_routing.get() or {}
    saved_channel = routing.get("mda_channel")
    if saved_channel is not None:
        channel = resolve_runtime_channel({"mda_channel": saved_channel}, configurable)
        if channel is None or channel.post is None:
            raise RuntimeError("Scheduled channel delivery is unavailable")
        content = (
            reply_text or None
            if isinstance(saved_channel, Mapping)
            and saved_channel.get("transport") == "trigger_server"
            else extract_assistant_content(output)
        )
        if content is not None:
            await channel.post({"type": "content", "content": content})
        return
    deliver_to = read_schedule_deliver_to(_field(routing.get("mda_schedule"), "deliver_to"))
    if deliver_to is not None:
        outcome = await deliver(deliver_to=deliver_to, reply_text=reply_text)
        if outcome == "failed":
            raise RuntimeError("Scheduled channel delivery failed")


def create_schedule_delivery_middleware(
    *,
    deliver: Callable[..., Awaitable[object]] | None = None,
) -> AgentMiddleware:
    """Post trusted schedule results and propagate delivery failures."""
    deliver_fn = deliver or deliver_schedule_result

    class _ScheduleDeliveryMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
        """Always-on middleware that auto-posts schedule results on trusted cron runs."""

        def __init__(self, deliver_impl: Callable[..., Awaitable[object]] | None = None) -> None:
            self._deliver = deliver_impl or deliver_schedule_result

        @property
        def name(self) -> str:
            return "mda-schedule-delivery"

        def after_agent(
            self, state: AgentState[Any], runtime: Runtime[Any]
        ) -> dict[str, Any] | None:
            # Local langgraph may invoke the sync hook; bridge to the async deliver path.
            return await_if_needed(self._deliver_after_agent(state, runtime))

        async def aafter_agent(
            self, state: AgentState[Any], runtime: Runtime[Any]
        ) -> dict[str, Any] | None:
            return await self._deliver_after_agent(state, runtime)

        async def _deliver_after_agent(
            self, state: AgentState[Any], runtime: Runtime[Any]
        ) -> dict[str, Any] | None:
            # Keep schedule config even when langgraph_auth_user is absent.
            from_runtime = _configurable_from_runtime(runtime)
            configurable = from_runtime if from_runtime is not None else _ambient_configurable()
            if not is_trusted_schedule_delivery_run(configurable):
                return None
            messages = None
            if isinstance(state, Mapping):
                messages = state.get("messages")
            await _deliver_captured_result(configurable, {"messages": messages}, self._deliver)
            return None

    return _ScheduleDeliveryMiddleware(deliver_impl=deliver_fn)


__all__ = [
    "NormalizedScheduleDeliverTo",
    "address_from_deliver_to",
    "create_schedule_delivery_middleware",
    "deliver_schedule_result",
    "get_deployment_id",
    "is_trusted_schedule_delivery_run",
    "read_schedule_deliver_to",
]
