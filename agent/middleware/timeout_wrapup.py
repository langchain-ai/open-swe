from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

_DEFAULT_TIMEOUT_SECONDS = 45 * 60
_WRAPUP_INSTRUCTION = """
<time_limit_warning>
You have been running for a long time. Wrap up immediately: finish the current
step, save or report useful state, avoid starting new investigations, and end
your turn with the best available result.
</time_limit_warning>
"""

_CHILD_RUN_WRAPUP_INSTRUCTION = """
<child_run_limit_warning>
You are approaching the run's child-run ceiling and are about to be terminated.
Stop all investigation NOW. Do not issue any more read_file, grep, or diff
fetches. You MUST call `publish_review` on your very next turn — publish the
findings you already have, or an empty/partial review noting the run was
truncated. If you do not publish now, the PR receives no review at all.
</child_run_limit_warning>
"""


def _configured_timeout_seconds() -> int:
    raw = os.environ.get("OPEN_SWE_WRAPUP_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def _configured_child_run_cap() -> int | None:
    raw = os.environ.get("OPEN_SWE_WRAPUP_CHILD_RUN_CAP")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _child_run_count(messages: list[BaseMessage]) -> int:
    count = 0
    for message in messages:
        if isinstance(message, ToolMessage):
            count += 1
        elif isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            count += len(message.tool_calls)
    return count


def _content_with_instruction(message: BaseMessage | None, instruction: str) -> str | list[object]:
    if message is None:
        return instruction
    content = message.content
    if isinstance(content, list):
        return [*content, {"type": "text", "text": instruction}]
    return f"{content}\n\n{instruction}" if content else instruction


class TimeoutWrapupMiddleware(AgentMiddleware):
    def __init__(
        self,
        timeout_seconds: int | None = None,
        *,
        child_run_cap: int | None = None,
        child_run_wrapup_ratio: float = 0.85,
    ) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds or _configured_timeout_seconds()
        self._child_run_cap = (
            child_run_cap if child_run_cap is not None else _configured_child_run_cap()
        )
        self._child_run_wrapup_ratio = child_run_wrapup_ratio
        # Graph construction should create one middleware instance per run; start
        # lazily so construction-time caching cannot age the run clock.
        self._start: float | None = None

    def _should_wrapup(self) -> bool:
        if self._start is None:
            self._start = time.monotonic()
        return (time.monotonic() - self._start) >= self._timeout_seconds

    def _should_child_run_wrapup(self, request: ModelRequest) -> bool:
        if not self._child_run_cap:
            return False
        threshold = self._child_run_cap * self._child_run_wrapup_ratio
        return _child_run_count(request.messages) >= threshold

    def _apply(self, request: ModelRequest) -> ModelRequest:
        if self._should_child_run_wrapup(request):
            content = _content_with_instruction(
                request.system_message, _CHILD_RUN_WRAPUP_INSTRUCTION
            )
            return request.override(system_message=SystemMessage(content=content))
        if not self._should_wrapup():
            return request
        content = _content_with_instruction(request.system_message, _WRAPUP_INSTRUCTION)
        return request.override(system_message=SystemMessage(content=content))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._apply(request))
