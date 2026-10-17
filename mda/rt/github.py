"""GitHub issue events this deployment's channel receives, handed to the Open SWE backend.

MDA's GitHub channel verifies each delivery and owns the conversation: one thread
per issue, on which it starts the run. The backend decides the rest, as for any
Open SWE run: whether the event starts one, its input, and the run context with
the token the run calls back with, signed for the channel's thread.
"""

import logging
import os
from typing import TYPE_CHECKING, Final

import httpx2
from pydantic import BaseModel, JsonValue

from rt.backend import BACKEND_URL_ENV

if TYPE_CHECKING:
    # Types only: the backend's tests import this module without the SDK.
    from managed_deepagents import (
        ChannelRunOptions,
        ChannelRunResult,
        GitHubChannelThread,
        GitHubEvent,
        GitHubPayload,
    )

logger = logging.getLogger(__name__)

#: Shared with the backend (its MDA_SERVICE_SECRET), which only answers this deployment.
SERVICE_SECRET_ENV: Final = "MDA_SERVICE_SECRET"
_SERVICE_SECRET_HEADER: Final = "x-mda-service-secret"
_RUNS_PATH: Final = "/github/channel/runs"
# Preparing a run reads the issue's comments and reacts to the mention.
_SECONDS: Final = 60.0
# Starts nothing: the channel only asks whether to tell the issue the thread waits.
_ACTS_WHILE_INTERRUPTED: Final = "Open SWE was mentioned while the thread waits."


class _PreparedRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class _Prepared(BaseModel):
    run: _PreparedRun | None = None
    acts: bool = False


class GitHubIntake:
    """Hands this deployment's GitHub issue events to the Open SWE backend."""

    # Generic over the payload, so one handler serves each event key's own type.
    async def start_run[P: "GitHubPayload"](
        self, event: "GitHubEvent[P]", thread: "GitHubChannelThread"
    ) -> "ChannelRunResult | None":
        """The run an @openswe mention on an issue starts, as the backend prepared it."""
        prepared = _Prepared.model_validate(await self._post(event, thread))
        if thread.interrupted:
            return _ACTS_WHILE_INTERRUPTED if prepared.acts else None
        if prepared.run is None:
            return None
        options: "ChannelRunOptions" = {
            "context": prepared.run.context,
            "metadata": prepared.run.metadata,
        }
        return (prepared.run.input, options)

    async def _post[P: "GitHubPayload"](
        self, event: "GitHubEvent[P]", thread: "GitHubChannelThread"
    ) -> dict[str, JsonValue]:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        secret = os.environ.get(SERVICE_SECRET_ENV, "").strip()
        if not base or not secret:
            raise RuntimeError(f"{BACKEND_URL_ENV} and {SERVICE_SECRET_ENV} are required")
        async with httpx2.AsyncClient(timeout=_SECONDS, follow_redirects=False) as client:
            response = await client.post(
                f"{base}{_RUNS_PATH}",
                headers={_SERVICE_SECRET_HEADER: secret},
                json={
                    "thread_id": thread.id,
                    "interrupted": thread.interrupted,
                    "delivery_id": event.delivery_id,
                    "event": event.key,
                    "payload": dict(event.payload),
                },
            )
        if response.is_error:
            logger.error(
                "The backend refused a GitHub event",
                extra={"event": event.key, "status": response.status_code},
            )
            response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}


GITHUB: Final = GitHubIntake()
