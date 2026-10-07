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

from openswe.dashboard.options import SUPPORTED_MODEL_IDS
from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.openai_responses.conversations import SandboxCaller
from openswe.openai_responses.ids import OpenSweId
from openswe.openai_responses.models import (
    DEFAULT_MODEL,
    ConversationRef,
    CreateResponseRequest,
    InputItem,
    ModelList,
    ModelObject,
    Response,
)
from openswe.openai_responses.projection import ResponseProjection
from openswe.openai_responses.stream import ResponseRun
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_access import OPENAI_PATH
from openswe.transcript.snapshot import load_head, load_run_start
from openswe.utils.json_types import run_metadata
from openswe.utils.thread_ops import langgraph_client

router = APIRouter(prefix=OPENAI_PATH, tags=["sandbox-openai"])

# Streaming requests answer 200 with server-sent events instead of the JSON body.
_SSE_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}
}
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _caller(request: Request) -> SandboxCaller:
    caller = await SandboxCaller.authenticate(request)
    workspace = caller.host_metadata.get("workspace")
    settings = await get_workspace_settings(workspace if isinstance(workspace, str) else "default")
    if not settings.sandbox_openai_enabled:
        raise HTTPException(403, "Sandbox OpenAI API is disabled")
    return caller


Caller = Annotated[SandboxCaller, Depends(_caller)]


def _body(response: Response) -> JSONResponse:
    return JSONResponse(response.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def _projection(
    request: Request, response: Response, ids: OpenSweId, client_tools: list[ClientToolSpec]
) -> ResponseProjection:
    # Codex drops ``mcp_call`` items; ``web_search_call`` is the one server tool it renders.
    codex = request.headers.get("originator", "").startswith("codex")
    return ResponseProjection(
        response,
        ids,
        web_search_tools=codex,
        client_tools={spec.name: spec.kind for spec in client_tools},
    )


def _stream(run: ResponseRun) -> StreamingResponse:
    return StreamingResponse(run.sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/models", response_model=ModelList)
async def list_models(_: Caller) -> ModelList:
    ids = [DEFAULT_MODEL, *sorted(SUPPORTED_MODEL_IDS)]
    return ModelList(data=[ModelObject(id=model_id) for model_id in ids])


@router.post("/responses", response_model=Response, responses=_SSE_RESPONSE)
async def create_response(
    body: CreateResponseRequest, caller: Caller, request: Request
) -> JSONResponse | StreamingResponse:
    continuation = await caller.resolve(body)
    prompt = InputItem.render(continuation.items)
    tool_results = [result for item in continuation.items if (result := item.tool_result())]
    if not prompt and not (tool_results and continuation.thread_id):
        raise HTTPException(400, "input has no new user message or tool output")
    model = body.agent_model()
    client_tools = body.client_tools()
    async with caller.reserve_capacity(continuation.thread_id):
        thread_id = continuation.thread_id or await caller.create_guest_thread(prompt, model)
        after = await load_head(thread_id) or 0
        run_id = await caller.start_run(
            thread_id,
            prompt=prompt,
            tool_results=tool_results,
            model=model,
            client_tools=client_tools,
        )
    ids = OpenSweId(thread_id, run_id)
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
    run = ResponseRun(ids, _projection(request, response, ids, client_tools), after)
    if body.background:
        return _body(response)
    if body.stream:
        return _stream(run)
    return _body(await run.result())


async def _existing_run(caller: SandboxCaller, request: Request, response_id: str) -> ResponseRun:
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
    client_tools = RunConfig.parse(run["kwargs"]["config"]["configurable"]).client_tools
    return ResponseRun(ids, _projection(request, response, ids, client_tools), after)


@router.get("/responses/{response_id}", response_model=Response, responses=_SSE_RESPONSE)
async def get_response(
    response_id: str, caller: Caller, request: Request, stream: bool = False
) -> JSONResponse | StreamingResponse:
    run = await _existing_run(caller, request, response_id)
    if stream:
        return _stream(run)
    return _body(await run.snapshot())


@router.post("/responses/{response_id}/cancel", response_model=Response)
async def cancel_response(response_id: str, caller: Caller, request: Request) -> JSONResponse:
    run = await _existing_run(caller, request, response_id)
    await langgraph_client().runs.cancel(run.thread_id, str(run.ids.run_id), wait=False)
    response = await run.snapshot()
    if not run.projection.done:
        response.status = "cancelled"
    return _body(response)
