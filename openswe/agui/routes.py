"""The CopilotKit runtime protocol, served by the backend so every deployment has it.

CopilotKit's browser client talks to ``runtimeUrl`` with four calls: ``GET /info``
and ``POST /agent/{id}/run | connect | stop/{thread}``. Run and connect bodies are
AG-UI ``RunAgentInput`` and their responses are AG-UI event streams.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from ag_ui.core import RunAgentInput
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from openswe.agui.stream import connect, start_run
from openswe.dashboard.deps import SESSION_DEP

AGENT_ID = "default"
COPILOTKIT_VERSION = "1.77.0"

router = APIRouter(prefix="/copilotkit", tags=["copilotkit"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive"}


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentInfo(_CamelModel):
    name: str
    description: str
    class_name: str


class ThreadEndpoints(_CamelModel):
    list: bool = False
    inspect: bool = False
    mutations: bool = False
    realtime_metadata: bool = False


class RuntimeInfo(_CamelModel):
    version: str
    agents: dict[str, AgentInfo]
    audio_file_transcription_enabled: bool = False
    mode: Literal["sse"] = "sse"
    thread_endpoints: ThreadEndpoints = ThreadEndpoints()
    suggestions: bool = False
    a2ui_enabled: bool = Field(default=False, alias="a2uiEnabled")
    open_generative_ui_enabled: bool = Field(default=False, alias="openGenerativeUIEnabled")
    telemetry_disabled: bool = True


class StopResult(BaseModel):
    stopped: bool


def _require_agent(agent_id: str) -> None:
    if agent_id != AGENT_ID:
        raise HTTPException(404, f"agent {agent_id!r} does not exist")


def _sse(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/info", response_model_by_alias=True)
async def api_copilotkit_info(session: dict[str, Any] = SESSION_DEP) -> RuntimeInfo:
    return RuntimeInfo(
        version=COPILOTKIT_VERSION,
        agents={
            AGENT_ID: AgentInfo(name=AGENT_ID, description="Open SWE", class_name="OpenSweAgent")
        },
    )


@router.post("/agent/{agent_id}/run")
async def api_copilotkit_run(
    agent_id: str,
    input: RunAgentInput,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    _require_agent(agent_id)
    return _sse(await start_run(input, session["sub"], session.get("email")))


@router.post("/agent/{agent_id}/connect")
async def api_copilotkit_connect(
    agent_id: str,
    input: RunAgentInput,
    session: dict[str, Any] = SESSION_DEP,
) -> StreamingResponse:
    _require_agent(agent_id)
    return _sse(await connect(input.thread_id, session["sub"], session.get("email")))


@router.post("/agent/{agent_id}/stop/{thread_id}")
async def api_copilotkit_stop(
    agent_id: str,
    thread_id: str,
    session: dict[str, Any] = SESSION_DEP,
) -> StopResult:
    """Runs are stopped through the dashboard's thread cancel endpoint, not here."""
    _require_agent(agent_id)
    return StopResult(stopped=False)
