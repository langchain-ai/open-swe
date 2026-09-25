"""Keep a bridged thread's run from ending without the result its CLI prints."""

import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput, hook_config
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from agent.input_messages import (
    SystemIdentity,
    build_input_messages,
    visible_dynamic_context_hashes,
)
from agent.middleware.message_content import content_to_text
from agent.middleware.require_user_reply import reported_failure, turn_tail
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt

logger = logging.getLogger(__name__)

CLI_RESULT_GUARD: SystemIdentity = {
    "id": "system:cli-result-guard",
    "display_name": "CLI result guard",
    "platform": "open-swe",
}


class CliResultState(AgentState):
    cli_result_nudges: NotRequired[Annotated[int, OmitFromOutput]]


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
        return {"cli_result_nudges": 0}

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

    def _nudge(self, state: Mapping[str, Any]) -> list[HumanMessage]:
        instruction = prompt("runs/missing-cli-result", result_tool=self._tool_name)
        built = build_input_messages(
            instruction,
            {"sender_id": CLI_RESULT_GUARD["id"], "surface": "automation", "kind": "system"},
            systems=[CLI_RESULT_GUARD],
            injected_dynamic_context_hashes=visible_dynamic_context_hashes(state),
        )
        return [
            HumanMessage(content=content_to_text(m["content"]), id=str(uuid.uuid7())) for m in built
        ]

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if self._satisfied(messages):
            return {"cli_result_nudges": 0}
        nudges = state.get("cli_result_nudges") or 0
        if nudges >= self._max_retries:
            logger.warning(
                "Run ended without a CLI result after every nudge",
                extra={"result_tool": self._tool_name, "result_attempts": nudges},
            )
            return {"cli_result_nudges": 0}
        logger.info(
            "Re-invoking the model for a missing CLI result",
            extra={"result_tool": self._tool_name, "result_attempt": nudges + 1},
        )
        return {
            "cli_result_nudges": nudges + 1,
            "messages": self._nudge(state),
            "jump_to": "model",
        }
