"""Wall-clock deadline around every model call.

Provider ``timeout`` kwargs bound HTTP requests, but the OpenAI Responses
websocket transport can stall without the client ever raising a read timeout,
which parks the whole run: no stream events, no queued-message pickup, and
nothing for ``ModelFallbackMiddleware`` to react to (a hang is not an error).
This middleware turns that hang into a timeout error, so the fallback model — or
the run-completion webhook — reports it instead of the run going silent.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse

from ..config import model_call_timeout_seconds

logger = logging.getLogger(__name__)

# Above the provider-level ``timeout`` (agent.models.factory), so a stalled HTTP
# request fails and retries inside the provider client first and this only fires
# for stalls the provider never notices.
DEFAULT_MODEL_CALL_TIMEOUT_SECONDS = 900.0


class ModelCallTimeoutError(TimeoutError):
    """A model call exceeded its wall-clock deadline."""


def _configured_timeout_seconds() -> float:
    return model_call_timeout_seconds(DEFAULT_MODEL_CALL_TIMEOUT_SECONDS)


class ModelCallTimeoutMiddleware(AgentMiddleware):
    """Fail a model call that exceeds the deadline instead of hanging forever."""

    def __init__(self, timeout_seconds: float | None = None) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds or _configured_timeout_seconds()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await asyncio.wait_for(handler(request), timeout=self._timeout_seconds)
        except TimeoutError as exc:
            logger.warning("Model call exceeded %ss deadline; aborting", self._timeout_seconds)
            raise ModelCallTimeoutError(
                f"Model call exceeded the {self._timeout_seconds}s deadline"
            ) from exc
