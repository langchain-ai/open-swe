"""Per-provider-attempt deadlines and stream safety inside same-model recovery."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.runnables.config import ensure_config, merge_configs, set_config_context

type ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]


class RecoveryCall(Protocol):
    def __call__(
        self,
        request: ModelRequest,
        handler: ModelHandler,
        *,
        retry_on: Callable[[Exception], bool] | None = None,
    ) -> Awaitable[ModelResponse]: ...


_recovery: ContextVar[RecoveryCall | None] = ContextVar("provider_attempts", default=None)


@contextmanager
def provider_attempts(recovery: RecoveryCall) -> Iterator[None]:
    token = _recovery.set(recovery)
    try:
        yield
    finally:
        _recovery.reset(token)


class ModelCallTimeoutError(TimeoutError):
    """A provider attempt exceeded its deadline, reported by name to the backend."""


class ModelCallAttempt(AsyncCallbackHandler):
    """Recover only the provider request, never preparation or a partially streamed reply."""

    def __init__(self, request: ModelRequest, timeout_seconds: float = 900.0) -> None:
        self._request = request
        self._timeout_seconds = timeout_seconds
        self.emitted = False

    async def on_llm_new_token(self, token: object, **kwargs: object) -> None:
        self.emitted = True

    async def call(
        self, invoke: Callable[[], Awaitable[ModelResponse]], *, streaming: bool = True
    ) -> ModelResponse:
        async def attempt(_request: ModelRequest) -> ModelResponse:
            deadline = asyncio.timeout(self._timeout_seconds)
            try:
                async with deadline:
                    if not streaming:
                        return await invoke()
                    config = merge_configs(ensure_config(), {"callbacks": [self]})

                    async def run() -> ModelResponse:
                        return await invoke()

                    with set_config_context(config) as context:
                        return await asyncio.create_task(run(), context=context)
            except TimeoutError as exc:
                if not deadline.expired():
                    raise
                raise ModelCallTimeoutError(
                    f"Model call exceeded the {self._timeout_seconds}s deadline"
                ) from exc

        recovery = _recovery.get()
        if recovery is None:
            return await attempt(self._request)
        return await recovery(self._request, attempt, retry_on=lambda _exc: not self.emitted)
