import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, NotRequired, cast

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from agent.middleware.trace import OpenSWEMiddleware
from agent.utils.startup_trace import aphase, flush_phases


def _take_latest[T](left: T | None, right: T | None) -> T | None:
    return right if right is not None else left


class PrepareRunState(AgentState):
    run_prepared: NotRequired[Annotated[bool, _take_latest]]
    run_prepared_for: NotRequired[Annotated[str, _take_latest]]
    work_dir: NotRequired[Annotated[str | None, _take_latest]]
    rendered_system_prompt: NotRequired[Annotated[str | None, _take_latest]]


def _latest_message_fingerprint(state: Mapping[str, Any]) -> str | None:
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    latest = messages[-1]
    message_id = getattr(latest, "id", None)
    content = getattr(latest, "content", latest)
    payload = {
        "type": latest.__class__.__name__,
        "id": message_id,
        "content": content,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BasePrepareRunMiddleware(OpenSWEMiddleware):
    """Checkpointed per-run setup.

    Subclasses must keep `_prepare` idempotent. LangGraph checkpoints the
    `run_prepared_for` latch after this before-agent node, so resumed attempts
    of the same invocation skip completed setup while later invocations on the
    same thread re-prepare fresh tokens, prompts, and diff context. If a run
    fails before that checkpoint, setup may execute again and every operation it
    calls must tolerate that.
    """

    state_schema = PrepareRunState

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        if state.get("_deepagents_forked_context"):
            return None
        try:
            prepared_state = cast(PrepareRunState, state)
            fingerprint = self._prepare_fingerprint(prepared_state, runtime)
            if (
                prepared_state.get("run_prepared")
                and prepared_state.get("run_prepared_for") == fingerprint
            ):
                return None
            async with aphase(getattr(self, "_thread_id", None), "prepare.total"):
                updates = await self._prepare(prepared_state, runtime)
            return {"run_prepared": True, "run_prepared_for": fingerprint, **updates}
        finally:
            # This hook is the first span the startup work can hang off of. A
            # resumed invocation latches out of `_prepare` but its graph factory
            # ran regardless, so the flush has to cover that path too.
            flush_phases(getattr(self, "_thread_id", None))

    def _prepare_fingerprint(self, state: PrepareRunState, runtime: Runtime) -> str:  # noqa: ARG002
        payload = {
            "middleware": self.__class__.__name__,
            "message": _latest_message_fingerprint(state),
            "config": self._prepare_config_fingerprint(),
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prepare_config_fingerprint(self) -> Any:
        return None

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:
        raise NotImplementedError

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        rendered = request.state.get("rendered_system_prompt")
        if isinstance(rendered, str) and rendered:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{rendered}\n\n{existing}" if existing else rendered
            request = request.override(system_message=SystemMessage(content=content))
        return await handler(request)
