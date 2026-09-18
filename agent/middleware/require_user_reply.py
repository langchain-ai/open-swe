"""Push a turn back to the model when it answers in plain text nobody receives."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.middleware.message_content import content_to_text
from agent.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)

_NUDGE = (
    "Your last message went nowhere: a plain assistant message is invisible in "
    "Slack, and `{tool}` is the only way your words reach the person who asked. "
    "Call `{tool}` now with the answer you just wrote."
)


def _nudged_content(message: BaseMessage | None, instruction: str) -> str | list[Any]:
    if message is None:
        return instruction
    content = message.content
    if isinstance(content, list):
        if any(instruction in content_to_text(block) for block in content):
            return content
        return [*content, {"type": "text", "text": instruction}]
    if instruction in content:
        return content
    return f"{content}\n\n{instruction}" if content else instruction


class RequireUserReplyMiddleware(OpenSWEMiddleware):
    """Re-invoke the model when a Slack turn ends without a user-facing reply.

    A model that narrates its answer instead of calling the reply tool leaves the
    asker with silence and the run still recorded as a success. The retry budget
    is small: a model that will not use the tool after two nudges will not use it
    on the tenth either.
    """

    state_schema = AgentState

    def __init__(self, tool_name: str, max_retries: int = 2) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._max_retries = max_retries
        self._retries = 0
        self._nudging = False

    def _replied_this_turn(self, messages: Sequence[BaseMessage]) -> bool:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return False
            if isinstance(message, AIMessage) and any(
                call.get("name") == self._tool_name for call in message.tool_calls
            ):
                return True
        return False

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not self._nudging:
            return await handler(request)
        instruction = _NUDGE.format(tool=self._tool_name)
        content = _nudged_content(request.system_message, instruction)
        return await handler(request.override(system_message=SystemMessage(content=content)))

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            self._nudging = False
            return None
        if self._replied_this_turn(messages) or self._retries >= self._max_retries:
            self._nudging = False
            return None
        self._retries += 1
        self._nudging = True
        logger.info(
            "Re-invoking the model for a missing user-facing reply",
            extra={"reply_tool": self._tool_name, "reply_attempt": self._retries},
        )
        return {"jump_to": "model"}
