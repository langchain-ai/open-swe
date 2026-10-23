"""Deliver run results to channels the run itself didn't come from.

Trusted schedule results go to a captured or explicit channel target. Task
notification replies go to the channel thread the task started from: deepagents
wakes an agent when one of its async subagent tasks ends by starting a run on
the agent's thread, which carries no channel delivery of its own. The agent's
thread remembers the channel binding of its last channel run; a notification
run restores it, after the caller's identity is checked against it, and posts
the agent's reply there.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict, cast

from langchain.agents.middleware.types import AgentMiddleware, AgentState, OmitFromInput
from langgraph.config import get_config
from langgraph.runtime import Runtime

from managed_deepagents._channels.assistant_text import (
    NOTIFICATION_KEY,
    QUIET_REPLY,
    RUN_START_KEY,
    extract_assistant_text,
    extract_reply_content,
    extract_reply_text,
    is_task_notification,
    run_start_id,
)
from managed_deepagents._channels.binding import (
    SLACK_BOT_TOKEN_ENV,
    ChannelBinding,
    _current_channel,
    _current_routing,
    resolve_channel_binding,
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


async def _post_to_saved_channel(
    saved_channel: object,
    configurable: Mapping[str, object] | None,
    output: Mapping[str, object],
    *,
    start_id: str | None = None,
) -> bool:
    """Post a run's reply to a saved channel; ``False`` when it can't be delivered to."""
    channel = resolve_runtime_channel({"mda_channel": saved_channel}, configurable)
    if channel is None or channel.post is None:
        return False
    content = (
        extract_reply_text(output, start_id=start_id) or None
        if isinstance(saved_channel, Mapping) and saved_channel.get("transport") == "trigger_server"
        else extract_reply_content(output, start_id=start_id)
    )
    if content is not None:
        await channel.post({"type": "content", "content": content})
    return True


async def _deliver_captured_result(
    configurable: Mapping[str, object] | None,
    output: Mapping[str, object],
    deliver: Callable[..., Awaitable[object]],
) -> None:
    reply_text = extract_assistant_text(output)
    routing = _current_routing.get() or {}
    saved_channel = routing.get("mda_channel")
    if saved_channel is not None:
        if not await _post_to_saved_channel(saved_channel, configurable, output):
            raise RuntimeError("Scheduled channel delivery is unavailable")
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


#: State key holding the channel binding of the thread's last channel run.
SAVED_CHANNEL_KEY = "mda_channel"

#: Set for the steps of a notification run whose channel binding was restored.
_delivering_notification: ContextVar[bool] = ContextVar(
    "mda_delivering_notification", default=False
)


class ChannelNotificationState(AgentState):
    """Thread state remembering where the agent's channel conversation is."""

    mda_channel: Annotated[NotRequired[dict[str, Any]], OmitFromInput]
    mda_run_start: Annotated[NotRequired[str | None], OmitFromInput]
    """ID of the task notification that started the current notification run (kept
    across its resumes), so its reply never repeats an answer an earlier run posted."""


def is_task_notification_input(input_value: object) -> bool:
    """Whether a run's input carries a deepagents task notification."""
    messages = input_value.get("messages") if isinstance(input_value, Mapping) else None
    if not isinstance(messages, list):
        return False
    return any(is_task_notification(message) for message in messages)


def restored_channel(
    saved: object, configurable: Mapping[str, object] | None
) -> ChannelBinding | None:
    """Return the thread's saved binding if this run's caller may deliver to it."""
    if saved is None:
        return None
    return resolve_channel_binding({"mda_channel": saved}, configurable)


def _saved_form(binding: ChannelBinding) -> dict[str, Any]:
    """What to keep in state: the binding without the raw provider event."""
    return {key: value for key, value in binding.items() if key != "raw_event"}


async def _post_notification_reply(
    binding: ChannelBinding,
    configurable: Mapping[str, object] | None,
    messages: object,
    start_id: str | None,
) -> None:
    output = {"messages": messages}
    text = extract_reply_text(output, start_id=start_id).strip()
    if not text or text == QUIET_REPLY:
        return
    if not await _post_to_saved_channel(binding, configurable, output, start_id=start_id):
        logger.error("task notification reply: channel delivery is unavailable")


async def report_notification_interrupts(
    binding: ChannelBinding,
    *,
    run_thread_id: str,
    run_id: str,
    interrupts: list[dict[str, object]],
) -> None:
    """Show a paused notification run's pending interrupts in its channel thread.

    Sends the same ``run.interrupted`` event a channel-started run sends, so
    Trigger shows the approval in the thread and its answer resumes this run.
    Only Trigger channels show interrupts; elsewhere MDA leaves them to the app.
    """
    target = binding["target"]
    if (
        binding["transport"] != "trigger_server"
        or not interrupts
        or not isinstance(target, Mapping)
    ):
        return
    from managed_deepagents._channels.trigger.http import (  # noqa: PLC0415 - avoids an import cycle
        _best_effort,
        _runtime_event,
    )

    client = create_trigger_client()
    if client is None:
        logger.error("task notification interrupt: Trigger channel is unavailable")
        return
    event = {
        "delivery_id": binding["delivery_id"],
        "source_thread_key": binding["source_thread_key"],
        "address": target["address"],
    }
    started = {"thread_id": run_thread_id, "run_id": run_id}
    body = _runtime_event(
        "run.interrupted", cast("Any", event), cast("Any", started), cast("Any", interrupts)
    )
    await _best_effort(client.post_runtime_event(str(target["channel_id"]), body))


def create_channel_notification_middleware() -> AgentMiddleware:
    """Remember channel threads, and post replies to task notifications there."""

    class _ChannelNotificationMiddleware(AgentMiddleware):
        state_schema = ChannelNotificationState

        @property
        def name(self) -> str:
            return "mda-channel-notifications"

        def before_agent(
            self,
            state: object,
            runtime: object | None = None,  # noqa: ARG002 - signature set by AgentMiddleware
        ) -> dict[str, Any] | None:
            return _remember_channel(state)

        async def abefore_agent(
            self,
            state: object,
            runtime: object | None = None,  # noqa: ARG002 - signature set by AgentMiddleware
        ) -> dict[str, Any] | None:
            return _remember_channel(state)

        def after_agent(
            self, state: object, runtime: object | None = None
        ) -> dict[str, Any] | None:
            # Local langgraph may invoke the sync hook; bridge to the async delivery path.
            return await_if_needed(self.aafter_agent(state, runtime))

        async def aafter_agent(
            self, state: object, runtime: object | None = None
        ) -> dict[str, Any] | None:
            binding = _current_channel.get()
            if not _delivering_notification.get() or binding is None:
                return None
            from_runtime = _configurable_from_runtime(runtime)
            configurable = from_runtime if from_runtime is not None else _ambient_configurable()
            messages = state.get("messages") if isinstance(state, Mapping) else None
            start_id = run_start_id(state)
            try:
                await _post_notification_reply(binding, configurable, messages, start_id)
            except Exception as error:  # noqa: BLE001 - delivery must not fail the agent's run
                logger.error(
                    "task notification reply: posting failed (%s)", logger.safe_error_label(error)
                )
            return None

    return _ChannelNotificationMiddleware()


def _remember_channel(state: object) -> dict[str, Any] | None:
    """Save a channel run's binding; a notification run records where it starts instead."""
    binding = _current_channel.get()
    if binding is None:
        return None
    if _delivering_notification.get():
        messages = state.get("messages") if isinstance(state, Mapping) else None
        start_id = _field(messages[-1], "id") if isinstance(messages, list) and messages else None
        return {RUN_START_KEY: start_id} if isinstance(start_id, str) else None
    update: dict[str, Any] = {}
    # An ordinary run replies with its last answer, so drop an earlier notification's start.
    if isinstance(state, Mapping) and state.get(RUN_START_KEY) is not None:
        update[RUN_START_KEY] = None
    saved = _saved_form(binding)
    current = state.get(SAVED_CHANNEL_KEY) if isinstance(state, Mapping) else None
    if current != saved:
        update[SAVED_CHANNEL_KEY] = saved
    return update or None


__all__ = [
    "NOTIFICATION_KEY",
    "QUIET_REPLY",
    "SAVED_CHANNEL_KEY",
    "ChannelNotificationState",
    "NormalizedScheduleDeliverTo",
    "address_from_deliver_to",
    "create_channel_notification_middleware",
    "create_schedule_delivery_middleware",
    "deliver_schedule_result",
    "get_deployment_id",
    "is_task_notification_input",
    "is_trusted_schedule_delivery_run",
    "read_schedule_deliver_to",
    "report_notification_interrupts",
    "restored_channel",
]
