"""How this deployment's channels hand their events to the Open SWE backend.

MDA's channels verify each delivery and own their conversations: they start the
runs on their threads. The backend decides the rest, as for any Open SWE run, and
completes each run the channel reports ended. Every call presents Open SWE's own
credential between the two, the backend's ``MDA_SERVICE_SECRET``.
"""

import logging
import os
from typing import TYPE_CHECKING, Final, Protocol

import httpx2
from pydantic import JsonValue

from rt.backend import BACKEND_URL_ENV

if TYPE_CHECKING:
    import httpx
    from managed_deepagents import HttpDeliveryOutcome

logger = logging.getLogger(__name__)

SERVICE_SECRET_ENV: Final = "MDA_SERVICE_SECRET"
_SERVICE_SECRET_HEADER: Final = "x-mda-service-secret"
_SETTLED_PATH: Final = "/mda/channel/settled"
# Preparing a run reads the issue's images or comments; the provider waits for no answer.
_SECONDS: Final = 60.0


class _Thread(Protocol):
    @property
    def id(self) -> str: ...


def _backend() -> tuple[str, str]:
    base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
    secret = os.environ.get(SERVICE_SECRET_ENV, "").strip()
    if not base or not secret:
        raise RuntimeError(f"{BACKEND_URL_ENV} and {SERVICE_SECRET_ENV} are required")
    return base, secret


async def post(path: str, body: dict[str, object]) -> dict[str, JsonValue]:
    """Call one of the backend's channel routes; a refusal raises."""
    base, secret = _backend()
    async with httpx2.AsyncClient(timeout=_SECONDS, follow_redirects=False) as client:
        response = await client.post(
            f"{base}{path}", headers={_SERVICE_SECRET_HEADER: secret}, json=body
        )
    if response.is_error:
        logger.error(
            "The backend refused a channel call",
            extra={"path": path, "status": response.status_code},
        )
        response.raise_for_status()
    answer = response.json()
    return answer if isinstance(answer, dict) else {}


def forward(request: "httpx.Request", path: str) -> "httpx.Request":
    """The delivery as the provider sent it, addressed to one of the backend's routes."""
    # Late: the backend's tests import this module without the SDK.
    from managed_deepagents import forward_to

    base, secret = _backend()
    return forward_to(request, f"{base}{path}", headers={_SERVICE_SECRET_HEADER: secret})


async def settled(_event: object, thread: _Thread, outcome: "HttpDeliveryOutcome") -> None:
    """Have the backend complete the run a delivery started, now that it ended.

    MDA starts the run, so no run-complete webhook follows it. The backend reads
    how it ended from MDA; one MDA lost track of is the backend's reconcile job's.
    """
    run_id = outcome.get("run_id")
    if outcome["type"] == "unknown" or not isinstance(run_id, str):
        return
    await post(_SETTLED_PATH, {"thread_id": thread.id, "run_id": run_id})
