"""Where this deployment's channels call the Open SWE backend.

MDA's channels verify each delivery and own their conversations: they start the
runs on their threads. A channel whose ``on_event`` names a backend endpoint
(``backend_endpoint``) lets the backend decide what each event starts. One whose
handlers decide here asks the backend only for what needs its data, through the
typed channel API in ``runtime_api`` (``ask_backend``). MDA signs every call with
the deployment's ``MDA_SERVICE_SECRET``, and the backend verifies it.
"""

import os
from typing import Final

# The client MDA calls a channel's endpoints with: it signs each call and bounds the answer.
from managed_deepagents._channels.http.endpoint import (  # noqa: PLC2701
    EndpointClient,
    EndpointLimits,
)
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint

from rt.backend import BACKEND_URL_ENV

# MDA reads the channels to build the deployment, without its environment.
_UNCONFIGURED: Final = "https://open-swe-backend.invalid"
# The pauses before trying a call again after a network error, HTTP 429 or a 5xx,
# with the same webhook ID. The provider has its answer before a handler asks, and
# never sends the delivery again: a call the backend acts on once per delivery
# ("idempotent") is tried for as long as a backend rollout takes, any other briefly.
_RETRY_DELAYS_SECONDS: Final = (1.0, 5.0)
_ROLLOUT_RETRY_DELAYS_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0, 30.0, 30.0, 30.0)


def backend_endpoint(path: str) -> str:
    """The backend's endpoint at ``path``, as a channel's ``on_event`` takes it.

    Without ``OPEN_SWE_BACKEND_URL``, as when MDA builds the deployment, it names
    a host that never resolves: a call fails, and the channel says so in the
    conversation.
    """
    base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/") or _UNCONFIGURED
    return f"{base}{path}"


async def ask_backend[Request: BaseModel, Response: BaseModel](
    endpoint: Endpoint[Request, Response], request: Request, *, msg_id: str
) -> Response:
    """What the backend's channel ``endpoint`` answers ``request`` with.

    Signed as webhook ``msg_id``, which a call sent again keeps, so the backend
    acts on a delivery once. A refused or failed call raises, and the channel
    reports the failure in the conversation; the provider doesn't send it again.
    """
    client = EndpointClient(backend_endpoint(endpoint.path), endpoint.name, endpoint.name)
    delays = (
        _ROLLOUT_RETRY_DELAYS_SECONDS
        if endpoint.delivery == "idempotent"
        else _RETRY_DELAYS_SECONDS
    )
    answer = await client.ask(
        msg_id, request.model_dump_json().encode(), EndpointLimits(endpoint.seconds, delays)
    )
    return endpoint.response.model_validate(answer)
