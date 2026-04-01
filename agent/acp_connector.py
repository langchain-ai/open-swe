"""ACP bridge for remotely controlling Open SWE LangGraph threads."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

from acp import RequestError, run_agent, session_notification
from acp.connection import Connection
from acp.helpers import (
    update_agent_message_text,
    update_agent_thought_text,
    update_user_message_text,
)
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    CloseSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
    SessionInfo,
    SessionInfoUpdate,
    SessionListCapabilities,
)
from langchain_core.messages.content import create_image_block, create_text_block
from langgraph_sdk import get_client

from .utils.messages import extract_text_content

DEFAULT_ASSISTANT_ID = "agent"
DEFAULT_PAGE_SIZE = 20
THREAD_POLL_INTERVAL_SECONDS = 1.0

_GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@|ssh://git@|https://|http://)?github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class LangGraphClientFactory(Protocol):
    def __call__(self, *, url: str, api_key: str | None) -> Any: ...


@dataclass(slots=True)
class SessionState:
    session_id: str
    cwd: str
    repo_config: dict[str, str]
    title: str
    last_message_count: int = 0


def _project_version() -> str:
    try:
        return version("open-swe-agent")
    except PackageNotFoundError:
        return "0.1.0"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _parse_repo_override(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    if "/" not in value:
        return None
    owner, name = value.split("/", 1)
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


def _extract_repo_from_remote(remote_url: str) -> dict[str, str] | None:
    match = _GITHUB_REMOTE_RE.match(remote_url.strip())
    if not match:
        return None
    return {"owner": match.group("owner"), "name": match.group("name")}


def _get_git_origin_url(cwd: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", cwd, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    remote_url = result.stdout.strip()
    return remote_url or None


def resolve_repo_config(cwd: str, repo_override: str | None = None) -> dict[str, str]:
    env_override = _parse_repo_override(repo_override) or _parse_repo_override(os.getenv("OPEN_SWE_REPO"))
    if env_override:
        return env_override

    remote_url = _get_git_origin_url(cwd)
    if remote_url:
        repo_config = _extract_repo_from_remote(remote_url)
        if repo_config:
            return repo_config

    raise RequestError.invalid_params(
        {
            "message": (
                "Could not determine the GitHub repository for this workspace. "
                "Set OPEN_SWE_REPO=owner/name or use a checkout with a GitHub origin remote."
            ),
            "cwd": cwd,
        }
    )


def _thread_repo_config(thread: dict[str, Any]) -> dict[str, str] | None:
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        return None
    repo = metadata.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}
    return None


def _thread_title(thread: dict[str, Any], repo_config: dict[str, str]) -> str:
    metadata = thread.get("metadata")
    if isinstance(metadata, dict):
        title = metadata.get("title")
        if isinstance(title, str) and title:
            return title

        linear_issue = metadata.get("linear_issue")
        if isinstance(linear_issue, dict):
            linear_title = linear_issue.get("title")
            if isinstance(linear_title, str) and linear_title:
                return linear_title

        github_issue = metadata.get("github_issue")
        if isinstance(github_issue, dict):
            github_title = github_issue.get("title")
            if isinstance(github_title, str) and github_title:
                return github_title

    return f"{repo_config['owner']}/{repo_config['name']}"


def _thread_metadata(thread: dict[str, Any]) -> dict[str, Any]:
    metadata = thread.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("type") or message.get("role") or ""
    return str(role).lower()


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    return extract_text_content(content) if isinstance(content, list | str) else ""


def _first_text_block(prompt: list[Any]) -> str:
    texts: list[str] = []
    for block in prompt:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "").strip()
            if text:
                texts.append(text)
    return "\n\n".join(texts).strip()


class OpenSWEAcpAgent:
    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
        repo_override: str | None = None,
        client_factory: LangGraphClientFactory | None = None,
    ) -> None:
        self.url = url or os.getenv("OPEN_SWE_LANGGRAPH_URL") or os.getenv("LANGGRAPH_URL")
        self.api_key = api_key or os.getenv("OPEN_SWE_LANGGRAPH_API_KEY") or os.getenv("LANGGRAPH_API_KEY")
        self.assistant_id = assistant_id
        self.repo_override = repo_override
        self._client_factory = client_factory or (lambda *, url, api_key: get_client(url=url, api_key=api_key))
        self._conn: Connection | None = None
        self._sessions: dict[str, SessionState] = {}
        self._active_runs: dict[str, str] = {}
        self._attach_tasks: dict[str, asyncio.Task[None]] = {}

    def on_connect(self, conn: Connection) -> None:
        self._conn = conn

    async def initialize(self, **_: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(
                name="open-swe-acp",
                title="Open SWE ACP",
                version=_project_version(),
            ),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(embedded_context=True, image=True),
                session_capabilities=SessionCapabilities(list=SessionListCapabilities()),
            ),
        )

    async def authenticate(self, **_: Any) -> AuthenticateResponse | None:
        return AuthenticateResponse()

    async def new_session(self, cwd: str, **_: Any) -> NewSessionResponse:
        client = self._client()
        repo_config = resolve_repo_config(cwd, repo_override=self.repo_override)
        thread = await client.threads.create(
            metadata={
                "repo": repo_config,
                "cwd": cwd,
                "title": f"{repo_config['owner']}/{repo_config['name']}",
                "source": "acp",
            }
        )
        session_id = thread["thread_id"]
        self._sessions[session_id] = SessionState(
            session_id=session_id,
            cwd=cwd,
            repo_config=repo_config,
            title=f"{repo_config['owner']}/{repo_config['name']}",
        )
        await self._notify_session_info(session_id)
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd: str, session_id: str, **_: Any) -> LoadSessionResponse | None:
        client = self._client()
        thread = await client.threads.get(session_id)
        repo_config = _thread_repo_config(thread) or resolve_repo_config(cwd, repo_override=self.repo_override)
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            repo_config=repo_config,
            title=_thread_title(thread, repo_config),
        )
        self._sessions[session_id] = session
        await self._notify_session_info(session_id)
        await self._emit_all_messages(session_id)
        if thread.get("status") == "busy":
            await self._notify_update(
                session_id,
                update_agent_thought_text("Attached to an active Open SWE run. Waiting for new output."),
            )
            self._start_attach_task(session_id)
        return LoadSessionResponse()

    async def resume_session(self, cwd: str, session_id: str, **kwargs: Any) -> LoadSessionResponse | None:
        return await self.load_session(cwd=cwd, session_id=session_id, **kwargs)

    async def list_sessions(self, cursor: str | None = None, cwd: str | None = None, **_: Any) -> ListSessionsResponse:
        client = self._client()
        offset = int(cursor or "0")
        repo_filter = resolve_repo_config(cwd, repo_override=self.repo_override) if cwd else None
        threads = await client.threads.search(limit=100, offset=0, sort_by="updated_at", sort_order="desc")

        filtered_threads: list[dict[str, Any]] = []
        for thread in threads:
            repo_config = _thread_repo_config(thread)
            if repo_filter and repo_config != repo_filter:
                continue
            if not repo_config:
                continue
            filtered_threads.append(thread)

        page = filtered_threads[offset : offset + DEFAULT_PAGE_SIZE + 1]
        next_cursor = None
        if len(page) > DEFAULT_PAGE_SIZE:
            next_cursor = str(offset + DEFAULT_PAGE_SIZE)
            page = page[:DEFAULT_PAGE_SIZE]

        sessions = [
            SessionInfo(
                session_id=thread["thread_id"],
                cwd=(cwd or _thread_metadata(thread).get("cwd") or str(Path.cwd())),
                title=_thread_title(thread, _thread_repo_config(thread) or repo_filter or {"owner": "", "name": ""}),
                updated_at=thread.get("updated_at"),
            )
            for thread in page
        ]
        return ListSessionsResponse(sessions=sessions, next_cursor=next_cursor)

    async def prompt(self, prompt: list[Any], session_id: str, message_id: str | None = None, **_: Any) -> PromptResponse:
        client = self._client()
        session = await self._ensure_session(session_id)
        await self._cancel_attach_task(session_id)

        before_messages = await self._get_thread_messages(session_id)
        session.last_message_count = len(before_messages)

        title_hint = _first_text_block(prompt).splitlines()[0][:80].strip()
        if title_hint:
            session.title = title_hint
            await client.threads.update(
                thread_id=session_id,
                metadata={
                    "repo": session.repo_config,
                    "cwd": session.cwd,
                    "title": session.title,
                    "source": "acp",
                },
            )
            await self._notify_session_info(session_id)

        for block in prompt:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "").strip()
                if text:
                    await self._notify_update(session_id, update_user_message_text(text))

        content = self._convert_prompt_blocks(prompt)
        run = await client.runs.create(
            session_id,
            self.assistant_id,
            input={"messages": [{"role": "user", "content": content}]},
            config={
                "configurable": {
                    "repo": session.repo_config,
                    "source": "acp",
                }
            },
            multitask_strategy="interrupt",
        )
        run_id = run["run_id"]
        self._active_runs[session_id] = run_id
        try:
            await client.runs.join(session_id, run_id)
            run_result = await client.runs.get(session_id, run_id)
        finally:
            self._active_runs.pop(session_id, None)

        if run_result["status"] == "interrupted":
            await self._emit_new_messages(session_id, skip_first_user=True)
            return PromptResponse(stop_reason="cancelled", user_message_id=message_id)

        if run_result["status"] not in {"success", "pending", "running"}:
            raise RequestError.internal_error(
                {
                    "message": "Open SWE run did not complete successfully",
                    "status": run_result["status"],
                    "run_id": run_id,
                }
            )

        await self._emit_new_messages(session_id, skip_first_user=True)
        return PromptResponse(stop_reason="end_turn", user_message_id=message_id)

    async def set_session_mode(self, **_: Any) -> None:
        return None

    async def set_session_model(self, **_: Any) -> None:
        return None

    async def set_config_option(self, **_: Any) -> None:
        return None

    async def fork_session(self, **_: Any) -> None:
        raise RequestError.method_not_found("session/fork")

    async def close_session(self, session_id: str, **_: Any) -> CloseSessionResponse | None:
        await self._cancel_attach_task(session_id)
        self._sessions.pop(session_id, None)
        self._active_runs.pop(session_id, None)
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **_: Any) -> None:
        await self._cancel_attach_task(session_id)
        run_id = self._active_runs.get(session_id)
        if not run_id:
            return
        await self._client().runs.cancel(session_id, run_id, wait=False, action="interrupt")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    def _client(self) -> Any:
        if not self.url:
            raise RequestError.invalid_request(
                {
                    "message": (
                        "Open SWE ACP requires a LangGraph deployment URL. "
                        "Set OPEN_SWE_LANGGRAPH_URL or pass --url."
                    )
                }
            )
        return self._client_factory(url=self.url, api_key=self.api_key)

    async def _ensure_session(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if session:
            return session
        thread = await self._client().threads.get(session_id)
        repo_config = _thread_repo_config(thread)
        if not repo_config:
            raise RequestError.resource_not_found(session_id)
        session = SessionState(
            session_id=session_id,
            cwd=_thread_metadata(thread).get("cwd") or str(Path.cwd()),
            repo_config=repo_config,
            title=_thread_title(thread, repo_config),
        )
        self._sessions[session_id] = session
        return session

    def _convert_prompt_blocks(self, prompt: list[Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in prompt:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                blocks.append(create_text_block(block.text))
                continue
            if block_type == "image":
                blocks.append(
                    create_image_block(
                        base64=getattr(block, "data", None),
                        mime_type=getattr(block, "mime_type", None),
                        url=getattr(block, "uri", None),
                    )
                )
                continue
            if block_type == "resource_link":
                label = getattr(block, "title", None) or getattr(block, "name", "Resource")
                blocks.append(create_text_block(f"{label}: {getattr(block, 'uri', '')}"))
                continue
            if block_type == "resource":
                resource = getattr(block, "resource", None)
                text = getattr(resource, "text", None)
                uri = getattr(resource, "uri", None)
                if text:
                    blocks.append(create_text_block(text))
                elif uri:
                    blocks.append(create_text_block(f"Embedded resource: {uri}"))
        if not blocks:
            blocks.append(create_text_block("(empty prompt)"))
        return blocks

    async def _get_thread_messages(self, session_id: str) -> list[dict[str, Any]]:
        state = await self._client().threads.get_state(session_id)
        values = state.get("values", {})
        if not isinstance(values, dict):
            return []
        messages = values.get("messages", [])
        return messages if isinstance(messages, list) else []

    async def _emit_all_messages(self, session_id: str) -> None:
        session = await self._ensure_session(session_id)
        messages = await self._get_thread_messages(session_id)
        await self._emit_messages(session_id, messages)
        session.last_message_count = len(messages)

    async def _emit_new_messages(self, session_id: str, *, skip_first_user: bool = False) -> None:
        session = await self._ensure_session(session_id)
        messages = await self._get_thread_messages(session_id)
        start_index = session.last_message_count
        if skip_first_user and start_index < len(messages):
            next_message = messages[start_index]
            if _message_role(next_message) in {"human", "user"}:
                start_index += 1
        await self._emit_messages(session_id, messages[start_index:])
        session.last_message_count = len(messages)

    async def _emit_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            text = _message_text(message)
            if not text:
                continue
            role = _message_role(message)
            if role in {"human", "user"}:
                update = update_user_message_text(text)
            elif role in {"ai", "assistant"}:
                update = update_agent_message_text(text)
            else:
                continue
            await self._notify_update(session_id, update)

    async def _notify_session_info(self, session_id: str) -> None:
        session = await self._ensure_session(session_id)
        await self._notify_update(
            session_id,
            SessionInfoUpdate(
                session_update="session_info_update",
                title=session.title,
                updated_at=_utcnow(),
            ),
        )

    async def _notify_update(self, session_id: str, update: Any) -> None:
        if not self._conn:
            return
        await self._conn.send_notification(
            "session/update",
            session_notification(session_id, update).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
                exclude_unset=True,
            ),
        )

    def _start_attach_task(self, session_id: str) -> None:
        self._attach_tasks[session_id] = asyncio.create_task(self._attach_when_idle(session_id))

    async def _cancel_attach_task(self, session_id: str) -> None:
        task = self._attach_tasks.pop(session_id, None)
        if not task:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _attach_when_idle(self, session_id: str) -> None:
        try:
            while True:
                thread = await self._client().threads.get(session_id)
                if thread.get("status") != "busy":
                    break
                await asyncio.sleep(THREAD_POLL_INTERVAL_SECONDS)
            await self._emit_new_messages(session_id)
        finally:
            self._attach_tasks.pop(session_id, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open SWE ACP connector")
    parser.add_argument("--url", help="LangGraph deployment URL for the remote Open SWE agent")
    parser.add_argument("--api-key", help="LangGraph API key", default=None)
    parser.add_argument("--assistant-id", default=DEFAULT_ASSISTANT_ID, help="LangGraph assistant ID")
    parser.add_argument("--repo", help="Override repo detection with owner/name", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(
        run_agent(
            OpenSWEAcpAgent(
                url=args.url,
                api_key=args.api_key,
                assistant_id=args.assistant_id,
                repo_override=args.repo,
            )
        )
    )


if __name__ == "__main__":
    main()
