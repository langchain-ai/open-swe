"""The run hooks that tie this runtime's model loop to the Open SWE backend.

The backend prepares each run (sandbox, checkout, diff, rendered review prompt,
model choice) and owns everything with credentials. These hooks fetch that
preparation once per run, apply it to every model call, and give the backend
its per-call and end-of-run turns.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Final, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, JsonValue

from open_swe_reviewer.backend import (
    DRAIN_HOOK,
    PREPARE_HOOK,
    REFRESH_HOOK,
    SETTLE_HOOK,
    BackendCallError,
    call_backend,
    current_thread_id,
)
from open_swe_reviewer.models import ModelSpec, build_model
from open_swe_reviewer.sandbox import remember_sandbox

logger = logging.getLogger(__name__)

# Preparation waits on the review scout's walkthrough for up to ten minutes.
_PREPARE_TIMEOUT_SECONDS: Final = 1800.0
_HOOK_TIMEOUT_SECONDS: Final = 120.0


class SandboxSpec(BaseModel):
    provider: str
    sandbox_id: str


class PreparedRun(BaseModel):
    system_prompt: str
    work_dir: str | None
    sandbox: SandboxSpec
    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


class RunState(AgentState):
    open_swe_prepared_for: NotRequired[Annotated[str, OmitFromOutput]]
    open_swe_run: NotRequired[Annotated[dict[str, JsonValue], OmitFromOutput]]


def _invocation_id() -> str:
    invocation = get_config().get("configurable", {}).get("invocation_id")
    return invocation if isinstance(invocation, str) and invocation else current_thread_id()


def _prepared(state: RunState) -> PreparedRun | None:
    raw = state.get("open_swe_run")
    if raw is None:
        return None
    prepared = PreparedRun.model_validate(raw)
    remember_sandbox(current_thread_id(), prepared.sandbox.sandbox_id)
    return prepared


def _with_prompt(request: ModelRequest, prompt: str) -> SystemMessage:
    existing = request.system_message.text if request.system_message is not None else ""
    return SystemMessage(content=f"{prompt}\n\n{existing}" if existing else prompt)


class BackendRunMiddleware(AgentMiddleware[RunState]):
    """Prepare each run on the backend and apply that preparation to the reviewer."""

    state_schema = RunState

    async def abefore_agent(
        self,
        state: RunState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        invocation = _invocation_id()
        if state.get("open_swe_prepared_for") == invocation and _prepared(state) is not None:
            return None
        prepared = PreparedRun.model_validate(
            await call_backend(PREPARE_HOOK, {}, timeout_seconds=_PREPARE_TIMEOUT_SECONDS)
        )
        remember_sandbox(current_thread_id(), prepared.sandbox.sandbox_id)
        return {"open_swe_prepared_for": invocation, "open_swe_run": prepared.model_dump()}

    async def abefore_model(
        self,
        state: RunState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        try:
            await call_backend(REFRESH_HOOK, {}, timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        except BackendCallError:
            # The proxy token still has time left unless this keeps failing; the
            # next model call retries it.
            logger.warning("Sandbox credential refresh failed", exc_info=True)
        drained = await call_backend(DRAIN_HOOK, {}, timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        messages = drained.get("messages")
        return {"messages": messages} if isinstance(messages, list) and messages else None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = _prepared(request.state)
        if prepared is None:
            return await handler(request)
        return await handler(
            request.override(
                model=build_model(prepared.model, use_gateway=prepared.use_gateway),
                system_message=_with_prompt(request, prepared.system_prompt),
            )
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        _prepared(request.state)
        return await handler(request)

    async def aafter_agent(
        self,
        state: RunState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, JsonValue] | None:
        try:
            await call_backend(SETTLE_HOOK, {}, timeout_seconds=_HOOK_TIMEOUT_SECONDS)
        except BackendCallError:
            # The backend's completion webhook settles a check this run left open.
            logger.warning("Settling the review check run failed", exc_info=True)
        return None


class SubagentRunMiddleware(AgentMiddleware[RunState]):
    """Run the reviewer subagent on the subagent model the backend picked."""

    state_schema = RunState

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = _prepared(request.state)
        if prepared is None:
            return await handler(request)
        return await handler(
            request.override(
                model=build_model(prepared.subagent_model, use_gateway=prepared.use_gateway)
            )
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        _prepared(request.state)
        return await handler(request)
