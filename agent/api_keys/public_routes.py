"""Public HTTP API authenticated by a workspace API key.

Threads started here belong to the key's workspace and to no person: they are
system-owned and public, and carry no GitHub login or email, so nothing they do
borrows a user's credentials.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException
from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, Field, JsonValue, field_validator

from agent.api_keys.deps import API_KEY_DEP
from agent.api_keys.models import ApiKey
from agent.dashboard.repo_access import require_repo_access_for_workspace
from agent.dispatch import create_durable_run
from agent.input_messages import InputMessageContext, build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.review.styles import normalize_repo_full_name
from agent.store import now_ms
from agent.threads.access import agent_version_metadata
from agent.threads.summary import run_status_to_agent_status
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.workspaces.routing import workspace_for_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["public-api"])

Key = Annotated[ApiKey, API_KEY_DEP]

_AGENT_ASSISTANT_ID = "agent"
_TITLE_FROM_PROMPT_CHARS = 80


class ThreadCreate(BaseModel):
    prompt: str = Field(min_length=1)
    repo: str | None = None
    title: str | None = None

    @field_validator("prompt")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("prompt must not be blank")
        return stripped


class StartedThread(BaseModel):
    thread_id: str
    run_id: str | None
    url: str | None


class ThreadState(BaseModel):
    thread_id: str
    status: str
    title: str | None
    url: str | None


async def _repo_in_workspace(raw: str, workspace: str) -> dict[str, str]:
    try:
        full_name = normalize_repo_full_name(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    owner, name = full_name.split("/", 1)
    if await workspace_for_repo(owner, name) != workspace:
        raise HTTPException(403, "repository is not in this workspace")
    await require_repo_access_for_workspace(full_name)
    return {"owner": owner, "name": name}


@router.post("/threads", status_code=201)
async def api_start_thread(body: ThreadCreate, key: Key) -> StartedThread:
    repo = await _repo_in_workspace(body.repo, key.workspace) if body.repo else None
    thread_id = str(uuid.uuid4())
    created_ms = now_ms()
    title = body.title or body.prompt[:_TITLE_FROM_PROMPT_CHARS] or f"API: {key.name}"
    metadata: dict[str, JsonValue] = {
        "source": "api",
        "origin": "api",
        "thread_category": "automation",
        "trigger_kind": "api",
        "automation_scope": "workspace",
        "owner_type": "system",
        "visibility": "public",
        "workspace": key.workspace,
        "api_key_id": key.id,
        "api_key_name": key.name,
        "created_by": key.created_by,
        "title": title,
        "base_branch": "main",
        "model": "Default",
        "created_at_ms": created_ms,
        "updated_at_ms": created_ms,
    }
    configurable: dict[str, JsonValue] = with_invocation_id(
        {
            "thread_id": thread_id,
            "source": "api",
            "workspace": key.workspace,
            "environment": key.workspace,
            "api_key_id": key.id,
        },
        new_invocation_id(),
    )
    if repo is not None:
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
        configurable["repo"] = repo

    client = langgraph_client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    input_context: InputMessageContext = {
        "sender_id": f"system:api-key:{key.id}",
        "surface": "automation",
        "kind": "system",
    }
    run = await create_durable_run(
        thread_id,
        _AGENT_ASSISTANT_ID,
        input=build_run_input(
            body.prompt,
            input_context,
            systems=[
                {
                    "id": f"system:api-key:{key.id}",
                    "display_name": key.name,
                    "platform": "open-swe",
                }
            ],
        ),
        source="api",
        config={"configurable": configurable, "metadata": agent_version_metadata()},
        client=client,
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    run_id = run_id if isinstance(run_id, str) else None
    # The run is durable now; recording it on the thread is bookkeeping the
    # caller's 201 must not depend on.
    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                "latest_run_id": run_id,
                "latest_run_status": "pending",
                "updated_at_ms": now_ms(),
            },
        )
    except Exception:
        logger.exception(
            "Failed to save API-started thread metadata",
            extra={
                "api_key_id": key.id,
                "open_swe_thread_id": thread_id,
                "open_swe_run_id": run_id,
            },
        )
    return StartedThread(thread_id=thread_id, run_id=run_id, url=dashboard_thread_url(thread_id))


async def _latest_run_status(client: LangGraphClient, thread_id: str) -> str | None:
    runs = await client.runs.list(thread_id, limit=1)
    status = runs[0].get("status") if runs else None
    return status.lower() if isinstance(status, str) else None


@router.get("/threads/{thread_id}")
async def api_get_thread(thread_id: str, key: Key) -> ThreadState:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 404:
            raise HTTPException(404, "thread not found") from exc
        raise
    metadata = thread_metadata(thread)
    if metadata.get("api_key_id") != key.id:
        raise HTTPException(404, "thread not found")
    thread_status = thread.get("status")
    stored_status = metadata.get("latest_run_status")
    run_status = await _latest_run_status(client, thread_id) or (
        stored_status if isinstance(stored_status, str) else None
    )
    title = metadata.get("title")
    return ThreadState(
        thread_id=thread_id,
        status=run_status_to_agent_status(
            thread_status if isinstance(thread_status, str) else None, run_status
        ),
        title=title if isinstance(title, str) else None,
        url=dashboard_thread_url(thread_id),
    )
