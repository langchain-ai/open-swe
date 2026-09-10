import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, SystemMessage

from coding_agent.config import ENV
from coding_agent.middleware.trace import CodingAgentMiddleware
from coding_agent.prompts import load_prompt

_DEFAULT_TIMEOUT_SECONDS = 45 * 60
_WRAPUP_INSTRUCTION = load_prompt("timeout-wrapup.md")


def _configured_timeout_seconds() -> int:
    raw = ENV.OPEN_SWE_WRAPUP_TIMEOUT_SECONDS.optional()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def _content_with_instruction(
    message: BaseMessage | None, instruction: str
) -> str | list[str | dict[Any, Any]]:
    if message is None:
        return instruction
    content = message.content
    if isinstance(content, list):
        if any(
            instruction in (block if isinstance(block, str) else str(block.get("text", "")))
            for block in content
        ):
            return content
        return [*content, {"type": "text", "text": instruction}]
    if instruction in content:
        return content
    return f"{content}\n\n{instruction}" if content else instruction


class TimeoutWrapupMiddleware(CodingAgentMiddleware):
    def __init__(self, timeout_seconds: int | None = None) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds or _configured_timeout_seconds()
        # Graph construction should create one middleware instance per run; start
        # lazily so construction-time caching cannot age the run clock.
        self._start: float | None = None

    def _should_wrapup(self) -> bool:
        if self._start is None:
            self._start = time.monotonic()
        return (time.monotonic() - self._start) >= self._timeout_seconds

    def _apply(self, request: ModelRequest) -> ModelRequest:
        if not self._should_wrapup():
            return request
        content = _content_with_instruction(request.system_message, _WRAPUP_INSTRUCTION.strip())
        return request.override(system_message=SystemMessage(content=content))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._apply(request))
