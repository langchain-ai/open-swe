"""Dashboard API a browser tab reads its page commands from and records their results with."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, JsonValue, field_validator

from openswe.audit_logs.middleware import audit_endpoint
from openswe.page_commands.records import PageCommand, PageCommandOutOfTurn, PageQueue

router = APIRouter(prefix="/page-sessions", tags=["page-commands"])

QueueDep = Annotated[PageQueue, Depends(PageQueue.of_request)]
_MAX_RESULT_CHARS = 64_000


class PageQueueView(BaseModel):
    # The invalidation topic the tab subscribes to, to hear new commands as they're queued.
    topic: str
    commands: list[PageCommand]


class PageCommandOutcome(BaseModel):
    # What the page's tool returned, or ``{"error": …}`` when it failed.
    result: JsonValue

    @field_validator("result")
    @classmethod
    def _small(cls, result: JsonValue) -> JsonValue:
        if len(json.dumps(result)) > _MAX_RESULT_CHARS:
            raise ValueError(f"a result may be at most {_MAX_RESULT_CHARS} characters")
        return result


@router.get("/{page_session}/commands")
async def api_page_commands(queue: QueueDep) -> PageQueueView:
    """The commands this tab's page has yet to carry out, in order."""
    return PageQueueView(topic=queue.topic, commands=await queue.waiting())


@router.post("/{page_session}/commands/{position}/result", status_code=204)
@audit_endpoint
async def api_record_page_command(
    position: int, body: PageCommandOutcome, queue: QueueDep
) -> Response:
    """Record what the page's next command returned once the page has carried it out."""
    try:
        await queue.record(position, body.result, by=queue.owner)
    except PageCommandOutOfTurn as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)
