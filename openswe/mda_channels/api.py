"""``POST /mda/channel-api/{endpoint}``: the typed calls MDA's channels make with no run yet.

A channel's handlers run in the MDA deployment and decide what each event starts
there; what needs Open SWE's data or credentials, such as who the sender is in
Open SWE or the run context the backend signs, is a named endpoint in
``runtime_api`` (``runtime_api.linear``). Only the deployment, which signs each
call with the secret both share, may call one (:mod:`.auth`).
"""

from dataclasses import dataclass
from functools import cache

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api.endpoint import (
    CHANNEL_API_PATH,
    Handlers,
    InvalidRequestError,
    UnknownEndpointError,
)

from openswe.mda_channels.auth import require_mda_channel

router = APIRouter(dependencies=[Depends(require_mda_channel)])
_body = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class ChannelCall:
    """The call a channel endpoint answers: its request, and work to do after the answer."""

    request: Request
    background: BackgroundTasks


@cache
def _handlers() -> Handlers[ChannelCall]:
    # Imported on the first call, so the webapp doesn't load every channel at startup.
    from runtime_api.linear import RECORD_LINEAR_DELIVERY, START_LINEAR_SESSION

    from openswe.linear.channel import record_linear_delivery, start_linear_session

    handlers = Handlers[ChannelCall]()
    handlers.serve(RECORD_LINEAR_DELIVERY, record_linear_delivery)
    handlers.serve(START_LINEAR_SESSION, start_linear_session)
    return handlers


@router.post(CHANNEL_API_PATH)
async def channel_api(
    endpoint: str, request: Request, background_tasks: BackgroundTasks
) -> JsonValue:
    """What ``endpoint`` answers the channel's call with."""
    try:
        body = _body.validate_json(await request.body())
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="The request body must be a JSON object"
        ) from None
    try:
        return await _handlers().dispatch(endpoint, ChannelCall(request, background_tasks), body)
    except UnknownEndpointError:
        raise HTTPException(status_code=404, detail=f"Unknown endpoint {endpoint}") from None
    except InvalidRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
