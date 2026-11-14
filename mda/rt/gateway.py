"""Model calls through the LLM gateway as whoever the run acts for.

A run a person started calls models on their LangSmith login; any other run on this
deployment's own key (``runtime_api.models.gateway_api_key``). The backend decides
which from the run's token (``gateway_credential``) and hands over the person's
current token, never this deployment's key.

Models are built once for every run (``rt.models``), so the one ``GATEWAY_BEARER``
every gateway-routed model holds finds, on each request, the run it serves and that
run's credential and current model route.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Final

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from runtime_api.catalog import ROUTES, kept_route
from runtime_api.endpoint import call
from runtime_api.gateway_auth import GatewayBearer
from runtime_api.loop import GATEWAY_CREDENTIAL, GatewayCredential
from runtime_api.models import GatewayModelRoute, gateway_api_key
from runtime_api.operations import RunRequest

from rt.backend import BackendCallError, Run, RunLocal, current_run

logger = logging.getLogger(__name__)

# The backend refreshes a person's token once it has two minutes left, so one kept for a
# minute is never sent expired.
_KEEP_SECONDS: Final = 60.0


class GatewaySignInRequired(RuntimeError):
    """The run acts for a person whose LangSmith login can no longer call models."""


class _KeptCredential:
    """One run's gateway credential, asked of the backend at most once a minute."""

    def __init__(self) -> None:
        self._credential: GatewayCredential | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, run: Run) -> GatewayCredential:
        clock = asyncio.get_running_loop()
        async with self._lock:
            if self._credential is None or clock.time() - self._fetched_at >= _KEEP_SECONDS:
                self._credential = await call(run.backend, GATEWAY_CREDENTIAL, RunRequest())
                self._fetched_at = clock.time()
                logger.info(
                    "Read the run's gateway credential",
                    extra={"gateway_credential": self._credential.kind},
                )
            return self._credential


class RunGateway:
    """The gateway credential of the run in progress."""

    def __init__(self) -> None:
        self._kept = RunLocal(_KeptCredential)
        self._route: ContextVar[GatewayModelRoute] = ContextVar(
            "gateway_model_route", default="default"
        )

    async def call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        *,
        run: Run,
        routed_model: BaseChatModel | str | None,
    ) -> ModelResponse:
        """Attribute only the routed model call, not fixed-model work or fallbacks."""
        route: GatewayModelRoute = "default"
        if routed_model is not None and request.model is routed_model:
            rules = run.catalog.loop
            models = run.catalog.models
            selected = kept_route(run.route)
            if (
                not rules.pinned_model
                and rules.routing is not None
                and rules.routing.mode == "auto"
                and selected in ROUTES
                and models is not None
                and selected in models.routes
            ):
                route = "auto"
        token = self._route.set(route)
        try:
            return await handler(request)
        finally:
            self._route.reset(token)

    async def credential(self) -> GatewayCredential:
        """The run's credential; this deployment's key outside a run that may ask for one.

        A backend that cannot answer (mid-rollout, or one from before this call) leaves
        the call on this deployment's key rather than failing the run; nothing is kept,
        so the next call asks again. Only a login the backend says is gone stops calls.
        """
        try:
            run = current_run()
        except RuntimeError:
            # No graph context (BackendCallError is one too): not a run's call.
            logger.warning("A gateway model call outside a run uses the deployment key")
            return GatewayCredential()
        if not run.has_run or run.awaiting_adoption:
            return GatewayCredential()
        try:
            return await self._kept.get(run).get(run)
        except BackendCallError:
            logger.warning("Could not read the run's gateway credential", exc_info=True)
            return GatewayCredential()

    async def unavailable(self) -> str | None:
        """Why the run cannot call models as the person it acts for, checked before a call.

        A failure inside a request's own auth reaches the loop as a connection error it
        retries, so the loop asks first and tells the person instead.
        """
        return (await self.credential()).unavailable

    async def route(self) -> GatewayModelRoute:
        """The current call's attribution, independent of cached models and other tasks."""
        return self._route.get()

    async def token(self) -> str:
        """The bearer for one gateway request."""
        credential = await self.credential()
        if credential.unavailable is not None:
            raise GatewaySignInRequired(credential.unavailable)
        if credential.token is not None:
            return credential.token
        key = gateway_api_key()
        if key is None:
            raise RuntimeError("No LANGSMITH_GATEWAY_API_KEY or LANGSMITH_API_KEY is set")
        return key


RUN_GATEWAY = RunGateway()
GATEWAY_BEARER = GatewayBearer(RUN_GATEWAY.token, route=RUN_GATEWAY.route)
