"""Keep a bridged thread's run from ending without the result its CLI prints."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from agent.middleware.require_user_reply import nudged_content, reported_failure, turn_tail
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import render_prompt

logger = logging.getLogger(__name__)


class CliResultState(AgentState):
    cli_result_nudges: NotRequired[int]
    cli_result_nudge_pending: NotRequired[bool]


class RequireCliResultMiddleware(OpenSWEMiddleware):
    """Re-invoke the model when a run ends without calling the result tool.

    Past the retry budget the run ends anyway, and the CLI reports that no
    result came back.
    """

    state_schema = CliResultState

    def __init__(self, tool_name: str, *, max_retries: int = 2) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._max_retries = max_retries

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        return {"cli_result_nudges": 0, "cli_result_nudge_pending": False}

    def _satisfied(self, messages: Sequence[BaseMessage]) -> bool:
        tail = turn_tail(messages)
        call_ids = {
            call.get("id")
            for message in tail
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call.get("name") == self._tool_name
        }
        return any(
            isinstance(message, ToolMessage)
            and message.tool_call_id in call_ids
            and not reported_failure(message)
            for message in tail
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not request.state.get("cli_result_nudge_pending"):
            return await handler(request)
        instruction = render_prompt("runs/missing-cli-result.md", {"result_tool": self._tool_name})
        content = nudged_content(request.system_message, instruction)
        return await handler(request.override(system_message=SystemMessage(content=content)))

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        settled: dict[str, Any] = {"cli_result_nudge_pending": False}
        if not isinstance(last, AIMessage) or last.tool_calls:
            return settled
        if self._satisfied(messages):
            return {**settled, "cli_result_nudges": 0}
        nudges = state.get("cli_result_nudges") or 0
        if nudges >= self._max_retries:
            logger.warning(
                "Run ended without a CLI result after every nudge",
                extra={"result_tool": self._tool_name, "result_attempts": nudges},
            )
            return {**settled, "cli_result_nudges": 0}
        logger.info(
            "Re-invoking the model for a missing CLI result",
            extra={"result_tool": self._tool_name, "result_attempt": nudges + 1},
        )
        return {
            "cli_result_nudges": nudges + 1,
            "cli_result_nudge_pending": True,
            "jump_to": "model",
        }
