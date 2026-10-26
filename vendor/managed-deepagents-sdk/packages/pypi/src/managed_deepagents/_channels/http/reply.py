"""An HTTP channel run posts its own answer.

The run does it, not the process that received the delivery, so a restart of
that process doesn't lose the answer, and a run LangSmith Studio resumes after
a decision answers the conversation the paused run came from.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr
from langgraph.config import get_config

from managed_deepagents._channels import messaging_registry
from managed_deepagents._channels.assistant_text import extract_reply_content, run_start_id
from managed_deepagents._channels.binding import resolve_channel_binding
from managed_deepagents._channels.runtime import is_json_value
from managed_deepagents._channels.schedule_delivery import is_trusted_schedule_delivery_run
from managed_deepagents._identity_runtime import (
    CHANNEL_THREAD_METADATA,
    _ambient_configurable,
    _configurable_from_runtime,
)
from managed_deepagents._log import logger
from managed_deepagents._memory.async_util import await_if_needed

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from managed_deepagents._channels.types import JsonValue

CHANNEL_REPLY_STATE_KEY = "_mda_channel_reply"
"""Where the thread's latest channel delivery came from, kept in its state.

Private, so it's never part of a run's input or output.
"""


class ReplyTarget(TypedDict):
    name: str
    target: JsonValue


class ChannelReplyState(AgentState[Any]):
    _mda_channel_reply: NotRequired[Annotated[ReplyTarget, PrivateStateAttr]]


def _configurable(runtime: object) -> Mapping[str, object] | None:
    from_runtime = _configurable_from_runtime(runtime)
    return from_runtime if from_runtime is not None else _ambient_configurable()


def _delivered_target(runtime: object) -> ReplyTarget | None:
    """The run's verified HTTP channel delivery.

    One the channel's ingress started for that conversation (the binding
    matches the run's authenticated source), never one a caller put in the
    run's context.
    """
    binding = resolve_channel_binding(None, _configurable(runtime))
    # Only HTTP channels deliver directly; others answer on their own.
    if binding is None or binding["transport"] != "direct":
        return None
    return {"name": binding["name"], "target": binding["target"]}


def _run_metadata() -> Mapping[str, object]:
    try:
        metadata = get_config().get("metadata")
    except RuntimeError:
        # Outside a runnable context.
        return {}
    return metadata if isinstance(metadata, Mapping) else {}


def _on_channel_thread() -> bool:
    """Whether the run is on a channel's thread.

    Only managed ingress marks one; the runtime's auth strips the marker from
    every other thread and run, so a caller can't point its own thread's stored
    target at someone else's conversation. Channel threads take runs only from
    their channel and resumes from LangSmith Studio.
    """
    return _run_metadata().get(CHANNEL_THREAD_METADATA) == "true"


def _stored_target(state: object) -> ReplyTarget | None:
    if not _on_channel_thread() or not isinstance(state, Mapping):
        return None
    value = state.get(CHANNEL_REPLY_STATE_KEY)
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    target = value.get("target")
    if not isinstance(name, str) or not name or not is_json_value(target):
        return None
    return {"name": name, "target": target}


class ChannelReplyMiddleware(AgentMiddleware[ChannelReplyState, Any, Any]):
    """Remember a channel delivery's target, and post the answer to it.

    A run that stops on ``interrupt()`` or is cancelled doesn't finish, so it
    posts nothing; the run that resumes it carries no delivery, so it answers
    the remembered target.
    """

    state_schema = ChannelReplyState

    @property
    def name(self) -> str:
        return "mda-channel-reply"

    def before_agent(
        self,
        state: ChannelReplyState,  # noqa: ARG002 - hook signature
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        delivered = _delivered_target(runtime)
        return {CHANNEL_REPLY_STATE_KEY: delivered} if delivered is not None else None

    async def abefore_agent(
        self, state: ChannelReplyState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state: ChannelReplyState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        # Local langgraph may invoke the sync hook; bridge to the async path.
        return await_if_needed(self._post_answer(state, runtime))

    async def aafter_agent(
        self, state: ChannelReplyState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return await self._post_answer(state, runtime)

    async def _post_answer(self, state: object, runtime: object) -> None:
        # Scheduled runs post through schedule delivery.
        if is_trusted_schedule_delivery_run(_configurable(runtime)):
            return
        reply = _delivered_target(runtime) or _stored_target(state)
        if reply is None:
            return
        if not isinstance(state, Mapping):
            return
        # Only this run's answers: deepagents can give a run more turns.
        content = extract_reply_content(state, start_id=run_start_id(state))
        if content is None:
            return
        transport = messaging_registry.messaging_transport_for_name(reply["name"])
        if transport is None:
            logger.warning(
                "channel reply skipped: no channel %s",
                json.dumps(logger.log_identifier(reply["name"])),
            )
            return
        try:
            posted = transport.post(
                message={"type": "content", "content": content}, target=reply["target"]
            )
            if inspect.isawaitable(posted):
                await posted
        except Exception as error:  # noqa: BLE001
            # The answer stays in the thread; the run itself succeeded.
            logger.error("channel reply failed (%s)", logger.safe_error_label(error))
        return


def create_channel_reply_middleware() -> ChannelReplyMiddleware:
    """Post an HTTP channel run's answer from the run itself."""
    return ChannelReplyMiddleware()


__all__ = ["CHANNEL_REPLY_STATE_KEY", "create_channel_reply_middleware"]
