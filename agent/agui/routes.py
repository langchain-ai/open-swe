"""AG-UI endpoints for dashboard threads, consumed by the CopilotKit runtime."""

from typing import Any

from ag_ui.core import RunAgentInput
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.agui.stream import connect, start_run
from agent.dashboard.deps import SESSION_DEP

router = APIRouter(prefix="/ag-ui", tags=["ag-ui"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive"}


@router.post("/run")
async def api_ag_ui_run(
    input: RunAgentInput,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    stream = await start_run(input, session["sub"], session.get("email"))
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/threads/{thread_id}/connect")
async def api_ag_ui_connect(
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    stream = await connect(thread_id, session["sub"], session.get("email"))
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)
