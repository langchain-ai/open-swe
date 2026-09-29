"""An OpenAI Responses endpoint for programs in a sandbox, answered by Open SWE threads.

Each conversation is a thread that runs in the caller's sandbox. The agent's
tool calls come back as already-executed ``mcp_call`` items, so the client never
runs a tool itself; any tools it declares are ignored.
"""

import time
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent.dashboard.options import SUPPORTED_MODEL_IDS
from agent.openai_responses.conversations import SandboxCaller
from agent.openai_responses.ids import OpenSweId
from agent.openai_responses.models import (
    DEFAULT_MODEL,
    ConversationRef,
    CreateResponseRequest,
    InputItem,
    ModelList,
    ModelObject,
    Response,
)
from agent.openai_responses.projection import ResponseProjection
from agent.openai_responses.stream import ResponseRun
from agent.sandboxes.tool_access import OPENAI_PATH
from agent.transcript.snapshot import load_head, load_run_start
from agent.utils.json_types import run_metadata
from agent.utils.thread_ops import langgraph_client

router = APIRouter(prefix=OPENAI_PATH, tags=["sandbox-openai"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _caller(request: Request) -> SandboxCaller:
    return await SandboxCaller.authenticate(request)


Caller = Annotated[SandboxCaller, Depends(_caller)]


def _body(response: Response) -> JSONResponse:
    return JSONResponse(response.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def _stream(run: ResponseRun) -> StreamingResponse:
    return StreamingResponse(run.sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/models", response_model=ModelList)
async def list_models(_: Caller) -> ModelList:
    ids = [DEFAULT_MODEL, *sorted(SUPPORTED_MODEL_IDS)]
    return ModelList(data=[ModelObject(id=model_id) for model_id in ids])


@router.post("/responses", response_model=None)
async def create_response(
    body: CreateResponseRequest, caller: Caller
) -> JSONResponse | StreamingResponse:
    continuation = await caller.resolve(body)
    prompt = InputItem.render(continuation.items)
    if not prompt:
        raise HTTPException(400, "input has no new user message")
    model = body.agent_model()
    await caller.require_capacity(continuation.thread_id)
    thread_id = continuation.thread_id or await caller.create_guest_thread(prompt, model)
    after = await load_head(thread_id) or 0
    ids = OpenSweId(thread_id, await caller.start_run(thread_id, prompt, model))
    response = Response(
        id=ids.response_id(),
        created_at=int(time.time()),
        status="queued",
        model=body.model,
        conversation=ConversationRef(id=thread_id),
        previous_response_id=body.previous_response_id,
        background=body.background,
        metadata=body.metadata or {},
    )
    run = ResponseRun(ids, ResponseProjection(response, ids), after)
    if body.background:
        return _body(response)
    if body.stream:
        return _stream(run)
    return _body(await run.result())


async def _existing_run(caller: SandboxCaller, response_id: str) -> ResponseRun:
    ids = OpenSweId.parse(response_id)
    if ids is None or ids.run_id is None:
        raise HTTPException(404, "Response not found")
    await caller.guest_thread(ids.thread_id)
    try:
        run = await langgraph_client().runs.get(ids.thread_id, ids.run_id)
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 404:
            raise HTTPException(404, "Response not found") from exc
        raise
    after = await load_run_start(ids.thread_id, ids.run_id)
    if after is None:
        after = await load_head(ids.thread_id) or 0
    model = run_metadata(run).get("agent_model_id")
    response = Response(
        id=response_id,
        created_at=int(datetime.fromisoformat(str(run["created_at"])).timestamp()),
        status="queued",
        model=model if isinstance(model, str) else DEFAULT_MODEL,
        conversation=ConversationRef(id=ids.thread_id),
    )
    return ResponseRun(ids, ResponseProjection(response, ids), after)


@router.get("/responses/{response_id}", response_model=None)
async def get_response(
    response_id: str, caller: Caller, stream: bool = False
) -> JSONResponse | StreamingResponse:
    run = await _existing_run(caller, response_id)
    if stream:
        return _stream(run)
    return _body(await run.snapshot())


@router.post("/responses/{response_id}/cancel")
async def cancel_response(response_id: str, caller: Caller) -> JSONResponse:
    run = await _existing_run(caller, response_id)
    await langgraph_client().runs.cancel(run.thread_id, str(run.ids.run_id), wait=False)
    response = await run.snapshot()
    if not run.projection.done:
        response.status = "cancelled"
    return _body(response)
