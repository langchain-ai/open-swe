"""Linear deliveries this deployment's channel receives, handed to the Open SWE backend.

MDA's Linear channel verifies each delivery and owns the conversation: it starts the
run on its thread, so the answer, Stop and errors reach the Linear session. The
backend decides the rest, as for any Open SWE run, so the channel forwards each
delivery exactly as Linear sent it, and the backend checks Linear's signature again.
"""

import logging
import os
from typing import Final

import httpx2
from managed_deepagents import (
    ChannelRunOptions,
    ChannelRunResult,
    LinearChannelRuntime,
    LinearEvent,
)
from pydantic import BaseModel, JsonValue

from rt.backend import BACKEND_URL_ENV

logger = logging.getLogger(__name__)

#: Shared with the backend (its MDA_SERVICE_SECRET), which only answers this deployment.
SERVICE_SECRET_ENV: Final = "MDA_SERVICE_SECRET"
_SERVICE_SECRET_HEADER: Final = "x-mda-service-secret"
# Preparing a run reads the issue's images; Linear waits for no answer meanwhile.
_SECONDS: Final = 60.0


class _PreparedRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class _Prepared(BaseModel):
    run: _PreparedRun | None


class LinearIntake:
    """Hands this deployment's Linear deliveries to the Open SWE backend."""

    async def start_run(
        self, event: object, runtime: LinearChannelRuntime
    ) -> ChannelRunResult | None:
        """The run an agent session turn starts, as the backend prepared it; None skips it."""
        prepared = _Prepared.model_validate(
            await self._post("/linear/channel/runs", event, runtime)
        ).run
        if prepared is None:
            return None
        options: ChannelRunOptions = {"context": prepared.context, "metadata": prepared.metadata}
        if prepared.webhook:
            options["webhook"] = prepared.webhook
        return (prepared.input, options)

    async def forward(self, event: object, runtime: LinearChannelRuntime) -> None:
        """Log an event that starts no conversation, and run the automations it triggers."""
        await self._post("/linear/channel/events", event, runtime)

    async def _post(
        self, path: str, event: object, runtime: LinearChannelRuntime
    ) -> dict[str, JsonValue]:
        # Handlers take any delivery: the backend reads what it needs from the raw one.
        if not isinstance(event, LinearEvent):
            raise TypeError("expected a Linear event")
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        secret = os.environ.get(SERVICE_SECRET_ENV, "").strip()
        if not base or not secret:
            raise RuntimeError(f"{BACKEND_URL_ENV} and {SERVICE_SECRET_ENV} are required")
        async with httpx2.AsyncClient(timeout=_SECONDS) as client:
            response = await client.post(
                f"{base}{path}",
                headers={_SERVICE_SECRET_HEADER: secret},
                json={
                    "thread_id": runtime.thread_id,
                    "delivery_id": event.delivery_id,
                    "raw_body": runtime.delivery.raw_body.decode(),
                    "signature": runtime.delivery.signature,
                },
            )
        if response.is_error:
            logger.error(
                "The backend refused a Linear delivery",
                extra={"path": path, "status": response.status_code},
            )
            response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}


LINEAR: Final = LinearIntake()
