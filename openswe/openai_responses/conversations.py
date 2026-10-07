"""Which Open SWE thread a Responses request continues, and starting its run."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request
from sqlalchemy import text

from openswe.database import postgres
from openswe.openai_responses.associations import record_guest_thread
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.openai_responses.ids import OpenSweId
from openswe.openai_responses.models import CreateResponseRequest, InputItem
from openswe.sandboxes.tool_access import (
    OPENAI_API_KEY_PLACEHOLDER,
    SANDBOX_HOST_THREAD_KEY,
    SANDBOX_PROXY_CONFIG_METADATA_KEY,
    TOOLS_HEADER,
    authenticate_tool_access,
)
from openswe.threads.runs import create_dashboard_thread_record, start_sandbox_guest_run
from openswe.threads.summary import repo_config_from_metadata
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

MAX_ACTIVE_GUESTS = 5


@dataclass(frozen=True, slots=True)
class Continuation:
    """The thread a request continues, or ``None`` for a new one, and what it adds."""

    thread_id: str | None
    items: list[InputItem]


@dataclass(frozen=True, slots=True)
class SandboxCaller:
    """A program in a sandbox, acting as the owner of the thread that created the sandbox."""

    host_thread_id: str
    sandbox_id: str
    host_metadata: JsonObject

    @classmethod
    async def authenticate(cls, request: Request) -> SandboxCaller:
        token = request.headers.get(TOOLS_HEADER)
        if not token:
            scheme, _, bearer = request.headers.get("Authorization", "").partition(" ")
            if scheme.lower() == "bearer" and bearer != OPENAI_API_KEY_PLACEHOLDER:
                token = bearer
        access = await authenticate_tool_access(token)
        host = await langgraph_client().threads.get(access.thread_id)
        return cls(access.thread_id, access.sandbox_id, thread_metadata(host))

    @property
    def owner_login(self) -> str:
        owner = self.host_metadata.get("owner_login")
        if not isinstance(owner, str) or not owner.strip():
            raise HTTPException(403, "This sandbox's thread has no owning user to act as")
        return owner.strip()

    async def guest_thread(self, thread_id: str) -> JsonObject:
        """A thread this sandbox started; any other id is reported as missing."""
        try:
            thread = await langgraph_client().threads.get(thread_id)
        except Exception as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                raise HTTPException(404, "Conversation not found") from exc
            raise
        metadata = thread_metadata(thread)
        if metadata.get(SANDBOX_HOST_THREAD_KEY) != self.host_thread_id:
            raise HTTPException(404, "Conversation not found")
        return metadata

    async def resolve(self, body: CreateResponseRequest) -> Continuation:
        """Explicit ids first, then any echoed item id this endpoint minted."""
        items = body.items()
        minted = [
            (index, parsed)
            for index, item in enumerate(items)
            if item.id and (parsed := OpenSweId.parse(item.id))
        ]
        tail = items[minted[-1][0] + 1 :] if minted else items
        explicit = body.conversation_id()
        if explicit is None and body.previous_response_id:
            previous = OpenSweId.parse(body.previous_response_id)
            if previous is None or previous.run_id is None:
                raise HTTPException(404, "Previous response not found")
            explicit = previous.thread_id
        thread_id = explicit or (minted[-1][1].thread_id if minted else None)
        if thread_id is not None:
            await self.guest_thread(thread_id)
        return Continuation(thread_id=thread_id, items=tail)

    @asynccontextmanager
    async def reserve_capacity(self, thread_id: str | None) -> AsyncIterator[None]:
        """Hold the host's guest slots from the busy check until the body has started its run."""
        async with postgres.transaction() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": f"sandbox-openai:{self.host_thread_id}"},
            )
            busy = await langgraph_client().threads.search(
                metadata={SANDBOX_HOST_THREAD_KEY: self.host_thread_id},
                status="busy",
                limit=MAX_ACTIVE_GUESTS + 1,
            )
            busy_ids = {str(thread["thread_id"]) for thread in busy}
            if thread_id not in busy_ids and len(busy_ids) >= MAX_ACTIVE_GUESTS:
                raise HTTPException(429, f"At most {MAX_ACTIVE_GUESTS} responses may run at once")
            yield

    async def create_guest_thread(
        self, prompt: str, model: tuple[str, str] | None, client_name: str = ""
    ) -> str:
        thread_id = str(uuid.uuid4())
        visibility: Literal["public", "private"] = (
            "private" if self.host_metadata.get("visibility") == "private" else "public"
        )
        workspace = self.host_metadata.get("workspace")
        await create_dashboard_thread_record(
            thread_id,
            login=self.owner_login,
            repo_config=repo_config_from_metadata(self.host_metadata),
            repo_explicitly_none=self.host_metadata.get("repo_explicitly_none") is True,
            prompt=prompt,
            model_id=model[0] if model else None,
            effort=model[1] if model else None,
            model_selection="explicit" if model else "auto",
            visibility=visibility,
            workspace=workspace if isinstance(workspace, str) else None,
            extra_metadata={
                SANDBOX_HOST_THREAD_KEY: self.host_thread_id,
                "responses_client": client_name.strip()[:80],
                "sandbox_id": self.sandbox_id,
                SANDBOX_PROXY_CONFIG_METADATA_KEY: self.host_metadata.get(
                    SANDBOX_PROXY_CONFIG_METADATA_KEY
                ),
            },
        )
        await record_guest_thread(self.host_thread_id, thread_id)
        return thread_id

    async def start_run(
        self,
        thread_id: str,
        *,
        prompt: str,
        tool_results: list[dict[str, str]],
        model: tuple[str, str] | None,
        client_tools: list[ClientToolSpec],
    ) -> str:
        overrides: dict[str, object] = {
            "client_tools": [spec.model_dump(mode="json") for spec in client_tools]
        }
        if model:
            overrides.update(
                agent_model_id=model[0], agent_effort=model[1], model_selection="explicit"
            )
        return await start_sandbox_guest_run(
            thread_id,
            self.owner_login,
            prompt=prompt,
            tool_results=tool_results,
            overrides=overrides,
        )
