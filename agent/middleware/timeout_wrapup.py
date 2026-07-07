from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

_DEFAULT_TIMEOUT_SECONDS = 45 * 60
_WRAPUP_INSTRUCTION = """
<time_limit_warning>
You have been running for a long time. Wrap up immediately: finish the current
step, save or report useful state, avoid starting new investigations, and end
your turn with the best available result.
</time_limit_warning>
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


class TimeoutWrapupMiddleware(AgentMiddleware):
    def __init__(self, timeout_seconds: int | None = None) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds or _configured_timeout_seconds()
        self._start = time.monotonic()

    def _should_wrapup(self) -> bool:
        return (time.monotonic() - self._start) >= self._timeout_seconds

    def _apply(self, request: ModelRequest) -> ModelRequest:
        if not self._should_wrapup():
            return request
        existing = request.system_message.text if request.system_message is not None else ""
        content = f"{existing}\n\n{_WRAPUP_INSTRUCTION}" if existing else _WRAPUP_INSTRUCTION
        return request.override(system_message=SystemMessage(content=content))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._apply(request))
