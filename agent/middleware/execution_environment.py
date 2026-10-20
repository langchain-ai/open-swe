"""Expose observed sandbox capabilities to the coding loop and eval reports."""

from collections.abc import Awaitable, Callable
from typing import NotRequired

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import render_prompt
from agent.sandboxes.environment import ExecutionEnvironment, probe_execution_environment


class ExecutionEnvironmentState(AgentState):
    execution_environment: NotRequired[dict[str, object]]


class ExecutionEnvironmentMiddleware(OpenSWEMiddleware[ExecutionEnvironmentState]):
    state_schema = ExecutionEnvironmentState

    def __init__(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend

    async def abefore_agent(
        self, state: ExecutionEnvironmentState, runtime: Runtime
    ) -> dict[str, object]:
        environment = await probe_execution_environment(self._backend)
        return {"execution_environment": environment.model_dump()}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        raw = request.state.get("execution_environment")
        if raw is None:
            return await handler(request)
        environment = ExecutionEnvironment.model_validate(raw)
        guidance = render_prompt(
            f"system/execution-{environment.shell_family}.md",
            environment=environment.model_dump_json(indent=2),
        )
        existing = request.system_message.content if request.system_message is not None else ""
        content = (
            [*existing, {"type": "text", "text": guidance}]
            if isinstance(existing, list)
            else f"{existing}\n\n{guidance}"
        )
        return await handler(request.override(system_message=SystemMessage(content=content)))
