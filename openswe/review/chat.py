"""The review page's chat: one thread per pull request that everyone reviewing it shares.

It is not the thread that implements the PR. It names that thread and joins its
task as an observer, so it can read the implementer's work and relay requests.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import TypedDict

from fastapi import HTTPException
from langgraph_sdk.errors import ConflictError, NotFoundError
from pydantic import BaseModel

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest
from openswe.prompts import prompt
from openswe.tasks.schemas import ThreadMetadata
from openswe.tasks.store import Task
from openswe.thread_ids import pr_chat_thread_id
from openswe.threads.handlers import get_dashboard_thread_state
from openswe.threads.proxy import (
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_stream_events,
    require_json_content_type,
)
from openswe.threads.runs import create_dashboard_thread_record
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

IMPLEMENTER_KEY = "review_chat_implementer_thread_id"
PR_URL_KEY = "review_chat_pr_url"


class ReviewChat(TypedDict):
    available: bool
    assistant_id: str
    thread_id: str


class _LinkedThread(BaseModel):
    kind: str | None = None
    graph_id: str | None = None


class _ChatMetadata(BaseModel):
    review_chat_implementer_thread_id: str | None = None


class PullRequestChat(BaseModel):
    owner: str
    repo: str
    pr_number: int

    @property
    def thread_id(self) -> str:
        return pr_chat_thread_id(self.owner, self.repo, self.pr_number)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.pr_number}"

    @property
    def log_extra(self) -> dict[str, object]:
        return {"pr_repo_full_name": f"{self.owner}/{self.repo}", "pr_number": self.pr_number}

    async def open(self, login: str, email: str | None) -> ReviewChat:
        """The shared chat, created on first open and pointed at the PR's current implementer."""
        implementer = await self.implementer()
        client = langgraph_client()
        try:
            chat = _ChatMetadata.model_validate(
                thread_metadata(await client.threads.get(self.thread_id))
            )
        except NotFoundError:
            await self._create(login, email, implementer)
            chat = _ChatMetadata()
        if implementer is not None:
            implementer_id, metadata = implementer
            if chat.review_chat_implementer_thread_id != implementer_id:
                await client.threads.update(
                    thread_id=self.thread_id, metadata={IMPLEMENTER_KEY: implementer_id}
                )
            await self._observe(implementer_id, metadata)
        return {"available": True, "assistant_id": "agent", "thread_id": self.thread_id}

    async def implementer(self) -> tuple[str, ThreadMetadata] | None:
        """The agent thread that opened the PR, or else the first one linked to it."""
        if not postgres.configured():
            return None
        client = langgraph_client()
        try:
            linked = await (
                await PullRequest.load(self.owner, self.repo, self.pr_number)
            ).linked_threads()
        except Exception:
            logger.warning(
                "Pull request registry unavailable; review chat has no implementer",
                extra=self.log_extra,
                exc_info=True,
            )
            return None
        for thread_id in linked:
            try:
                metadata = thread_metadata(await client.threads.get(thread_id))
            except NotFoundError:
                continue
            link = _LinkedThread.model_validate(metadata)
            if link.kind is None and link.graph_id in (None, "agent"):
                return thread_id, ThreadMetadata.model_validate(metadata)
        return None

    async def _create(
        self, login: str, email: str | None, implementer: tuple[str, ThreadMetadata] | None
    ) -> None:
        workspace = implementer[1].workspace or implementer[1].environment if implementer else None
        if workspace is None:
            workspace = (await resolve_workspace(repo=(self.owner, self.repo), login=login)).slug
        try:
            await create_dashboard_thread_record(
                self.thread_id,
                login=login,
                email=email,
                repo_config={"owner": self.owner, "name": self.repo},
                prompt=prompt(
                    "runs/pull-request-review-chat",
                    url=self.url,
                    implementer_thread_id=implementer[0] if implementer else None,
                ),
                title=f"Discuss {self.owner}/{self.repo}#{self.pr_number}",
                workspace=workspace,
                extra_metadata={"unlisted": True, PR_URL_KEY: self.url},
            )
        except ConflictError:
            logger.info("Review chat was created concurrently", extra=self.log_extra)

    async def _observe(self, implementer_id: str, implementer: ThreadMetadata) -> None:
        workspace = implementer.workspace or implementer.environment
        if workspace is None:
            logger.info(
                "Implementer has no workspace; review chat joins no task", extra=self.log_extra
            )
            return
        try:
            await Task.add_observer(
                implementer_id,
                self.thread_id,
                title=implementer.title or f"{self.owner}/{self.repo}#{self.pr_number}",
                workspace=workspace,
            )
        except PermissionError, ValueError:
            logger.warning(
                "Review chat could not join the implementer's task",
                extra={**self.log_extra, "implementer_thread_id": implementer_id},
                exc_info=True,
            )

    def _require(self, thread_id: str) -> None:
        if thread_id != self.thread_id:
            raise HTTPException(404, "chat not found")

    async def commands(
        self, login: str, thread_id: str, body: bytes, *, content_type: str = "application/json"
    ) -> tuple[int, bytes, str | None]:
        self._require(thread_id)
        require_json_content_type(content_type)
        try:
            command = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "command body must be a JSON object") from exc
        if not isinstance(command, dict):
            raise HTTPException(400, "command body must be a JSON object")
        return await proxy_dashboard_thread_commands(
            thread_id, login, json.dumps(command).encode(), content_type=content_type
        )

    async def stream_events(
        self, login: str, thread_id: str, body: bytes, *, content_type: str = "application/json"
    ) -> AsyncIterator[bytes]:
        self._require(thread_id)
        return await proxy_dashboard_thread_stream_events(
            thread_id, login, body, content_type=content_type
        )

    async def state(self, login: str, thread_id: str) -> tuple[int, bytes, str | None]:
        self._require(thread_id)
        state = await get_dashboard_thread_state(thread_id, login)
        return 200, json.dumps(state).encode(), "application/json"

    async def history(
        self, login: str, thread_id: str, body: bytes, *, content_type: str = "application/json"
    ) -> tuple[int, bytes, str | None]:
        self._require(thread_id)
        return await proxy_dashboard_thread_history(
            thread_id, login, body, content_type=content_type
        )
