"""The final messages of a run whose state no checkpointer keeps.

A stateless run, such as a cron fire without a thread, has no state to read
back when its stream ends, so the delivery step takes its answer from here.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.runtime import Runtime

RUN_OUTPUT_MIDDLEWARE_NAME = "mda-run-output"


@dataclass
class RunOutput:
    """What a run's agent ended with; ``None`` until the agent finishes."""

    messages: object = None


_current_output: ContextVar[RunOutput | None] = ContextVar("mda_run_output", default=None)


class RunOutputMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Record the agent's final messages for a run that collects them."""

    @property
    def name(self) -> str:
        return RUN_OUTPUT_MIDDLEWARE_NAME

    def after_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> None:  # noqa: ARG002
        output = _current_output.get()
        if output is not None:
            output.messages = state.get("messages")

    async def aafter_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> None:
        self.after_agent(state, runtime)
