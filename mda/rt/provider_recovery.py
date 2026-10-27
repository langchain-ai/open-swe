"""Waiting out a provider outage on the model the run chose, never another.

A model call its provider fails for overload, rate limiting, server errors, a
dropped connection or a timeout is made again on the same model, after a capped
exponential backoff, until the provider answers or the wait runs out. The backend
is told when the run starts waiting and when it stops: it tells the person once
and shows the wait on the dashboard. Anything else a call fails with, such as a
rejected request, a refused key or our own bug, raises at once, so it never holds
a run in the loop.

Only the model call is made again; no tool runs twice. A run stopped while it
waits is cancelled as any other, and one whose process dies resumes from its last
checkpoint, which makes the same call and waits again if the provider is still down.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Final

import httpx
import httpx2
from langchain.agents.middleware import AgentMiddleware, TracePolicy, omit_payload
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ModelError
from runtime_api.catalog import ModelSpec
from runtime_api.endpoint import call
from runtime_api.loop import MODEL_RECOVERY, ModelFailureCode, ModelRecovery

from rt.backend import BackendCallError, OpenSweBackend, RunLocal
from rt.model_calls import http_status, provider_failure_code
from rt.tools import run_id

logger = logging.getLogger(__name__)

_OUTAGES: Final[frozenset[ModelFailureCode]] = frozenset(
    {"provider_overloaded", "provider_rate_limited", "provider_unavailable", "provider_timeout"}
)
# Any other 4xx is the request's or the account's fault, whatever its body says.
_TRANSIENT_CLIENT_STATUSES: Final = frozenset({408, 409, 425, 429})
# Spent credit comes back as a rate limit, but does not pass by waiting.
_ACCOUNT_LIMITS: Final = ("insufficient_quota",)
_PROVIDER_SDKS: Final = frozenset({"anthropic", "openai", "google", "groq", "fireworks"})


def _from_provider(exc: BaseException) -> bool:
    """Whether the provider, or the way to it, raised ``exc``; never our code or the backend."""
    return (
        http_status(exc) is not None
        or isinstance(exc, (ModelError, TimeoutError, httpx.TransportError, httpx2.TransportError))
        or type(exc).__module__.partition(".")[0] in _PROVIDER_SDKS
    )


def provider_outage(exc: BaseException) -> ModelFailureCode | None:
    """Why the provider failed a call, when waiting on the same model may get it through.

    Only an error the provider's side raised counts, whatever another error's
    message says: one of ours that mentions an overload must still fail the run.
    """
    if not _from_provider(exc):
        return None
    code = provider_failure_code(exc)
    if code not in _OUTAGES:
        return None
    status = http_status(exc)
    if status is not None and 400 <= status < 500 and status not in _TRANSIENT_CLIENT_STATUSES:
        return None
    body = getattr(exc, "body", None)
    if any(limit in f"{body} {exc}".lower() for limit in _ACCOUNT_LIMITS):
        return None
    return code


@dataclass(frozen=True)
class Backoff:
    """How long to wait before each call again: doubling, capped, jittered, then giving up."""

    initial_seconds: float = 5.0
    max_seconds: float = 300.0
    # Below the platform's run timeout, so the run ends naming the provider instead.
    give_up_after_seconds: float = 6 * 60 * 60

    def delays(self) -> Iterator[float]:
        waited = 0.0
        delay = self.initial_seconds
        while waited < self.give_up_after_seconds:
            # Jittered, so the runs one outage stopped do not all call again at once.
            wait = min(random.uniform(delay / 2, delay), self.give_up_after_seconds - waited)
            waited += wait
            yield wait
            delay = min(delay * 2, self.max_seconds)


@dataclass
class _Outage:
    """How many of a run's model calls wait on their provider right now."""

    waiting: int = 0
    told: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProviderRecovery:
    """Holds a run's model calls through their provider's outages, on the model they chose.

    Shared by the agent's calls and its subagents', so the backend hears that a
    run is waiting when its first call starts to and that it stopped when its last
    call got through; what each run waits on is its own.
    """

    def __init__(self, backend: OpenSweBackend, backoff: Backoff | None = None) -> None:
        self._backend = backend
        self._backoff = backoff or Backoff()
        self._outages = RunLocal(_Outage)

    @property
    def backend(self) -> OpenSweBackend:
        return self._backend

    async def call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        model: ModelSpec | None,
        *,
        via_gateway: bool,
    ) -> ModelResponse:
        """``handler``'s response, made again through any outage of ``model``'s provider."""
        delays = self._backoff.delays()
        waiting = False
        try:
            while True:
                try:
                    return await handler(request)
                except Exception as exc:
                    code = provider_outage(exc)
                    delay = next(delays, None) if code is not None else None
                    if code is None or delay is None:
                        raise
                    if not waiting:
                        waiting = True
                        await self._started(code, model, via_gateway=via_gateway)
                    logger.warning(
                        "Model provider failed the call; calling the same model again",
                        exc_info=exc,
                        extra={
                            "provider_recovery": {
                                "model_id": model.model_id if model is not None else None,
                                "code": code,
                                "retry_in_seconds": round(delay, 1),
                            }
                        },
                    )
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # A stopped run's turn ends with it, wait and all; nothing to tell.
            if waiting:
                waiting = False
                self._outages.get(self._backend.run).waiting -= 1
            raise
        finally:
            if waiting:
                await self._stopped(model, via_gateway=via_gateway)

    async def _started(
        self, code: ModelFailureCode, model: ModelSpec | None, *, via_gateway: bool
    ) -> None:
        outage = self._outages.get(self._backend.run)
        async with outage.told:
            outage.waiting += 1
            if outage.waiting == 1:
                await self._tell(
                    ModelRecovery(
                        run_id=run_id(),
                        waiting=True,
                        code=code,
                        model_id=model.model_id if model is not None else None,
                        via_gateway=via_gateway,
                    )
                )

    async def _stopped(self, model: ModelSpec | None, *, via_gateway: bool) -> None:
        outage = self._outages.get(self._backend.run)
        async with outage.told:
            outage.waiting -= 1
            if outage.waiting == 0:
                await self._tell(
                    ModelRecovery(
                        run_id=run_id(),
                        waiting=False,
                        model_id=model.model_id if model is not None else None,
                        via_gateway=via_gateway,
                    )
                )

    async def _tell(self, recovery: ModelRecovery) -> None:
        try:
            await call(self._backend, MODEL_RECOVERY, recovery)
        except BackendCallError:
            logger.warning(
                "Could not tell the backend the run is waiting on its model", exc_info=True
            )


class ProviderRecoveryMiddleware(AgentMiddleware):
    """Wait out a provider outage around a subagent's calls of the model its run gives it."""

    trace_policy = TracePolicy(process_inputs=omit_payload)

    def __init__(self, recovery: ProviderRecovery) -> None:
        super().__init__()
        self._recovery = recovery

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        models = self._recovery.backend.run.catalog.models
        return await self._recovery.call(
            request,
            handler,
            models.subagent_model if models is not None else None,
            via_gateway=models is not None and models.use_gateway,
        )
