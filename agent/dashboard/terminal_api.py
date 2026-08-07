from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..utils.json_types import thread_metadata
from .thread_api import _authorized_thread, _metadata_repo

_TERMINAL_INPUT_LIMIT = 64_000
_TERMINAL_QUEUE_SIZE = 256
_EOF = None


class TerminalInputBody(BaseModel):
    data: str = Field(min_length=1, max_length=_TERMINAL_INPUT_LIMIT)


@dataclass
class TerminalSession:
    id: str
    thread_id: str
    sandbox_id: str
    cwd: str
    handle: Any
    iterator: Any
    queue: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_TERMINAL_QUEUE_SIZE)
    )
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pump_task: asyncio.Task[None] | None = None


class TerminalManager:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._thread_sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, thread_id: str, login: str, *, email: str | None = None
    ) -> dict[str, Any]:
        # The sandbox stack pulls in deepagents/anthropic, and this module sits in
        # `agent.webapp`'s import closure, where those imports cost pod readiness.
        from ..integrations.langsmith import TimeoutLangSmithSandbox
        from ..utils.sandbox_paths import aresolve_repo_dir, aresolve_sandbox_work_dir
        from ..utils.sandbox_state import get_sandbox_backend, unwrap_sandbox_backend

        thread = await _authorized_thread(thread_id, login, email=email)
        metadata = thread_metadata(thread)
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str) or not sandbox_id or sandbox_id == "__creating__":
            raise HTTPException(409, "sandbox is not ready")

        async with self._lock:
            existing_id = self._thread_sessions.get(thread_id)
            existing = self._sessions.get(existing_id) if existing_id else None
            if existing:
                return self._info(existing)

        try:
            proxy = await get_sandbox_backend(thread_id)
        except Exception as exc:
            raise HTTPException(503, "sandbox is unavailable") from exc
        backend = unwrap_sandbox_backend(proxy)
        if not isinstance(backend, TimeoutLangSmithSandbox):
            raise HTTPException(501, "terminal unavailable for this sandbox provider")

        _, repo_name, _ = _metadata_repo(metadata)
        cwd = (
            await aresolve_repo_dir(proxy, repo_name)
            if repo_name
            else await aresolve_sandbox_work_dir(proxy)
        )
        if repo_name:
            check = await proxy.aexecute(f"test -d {shlex.quote(cwd)}", timeout=30)
            if check.exit_code != 0:
                cwd = await aresolve_sandbox_work_dir(proxy)

        try:
            handle = await asyncio.to_thread(
                backend.sandbox.run,
                'exec "${SHELL:-/bin/bash}" -l',
                cwd=cwd,
                shell="/bin/bash",
                timeout=0,
                idle_timeout=300,
                kill_on_disconnect=False,
                ttl_seconds=600,
                pty=True,
                wait=False,
            )
        except Exception as exc:
            raise HTTPException(503, "terminal failed to start") from exc
        session = TerminalSession(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            sandbox_id=sandbox_id,
            cwd=cwd,
            handle=handle,
            iterator=iter(handle),
        )
        async with self._lock:
            existing_id = self._thread_sessions.get(thread_id)
            existing = self._sessions.get(existing_id) if existing_id else None
            if existing:
                await asyncio.to_thread(handle.kill)
                return self._info(existing)
            self._sessions[session.id] = session
            self._thread_sessions[thread_id] = session.id
        session.pump_task = asyncio.create_task(self._pump(session))
        return self._info(session)

    async def input(
        self, thread_id: str, terminal_id: str, login: str, data: str, *, email: str | None = None
    ) -> None:
        thread = await _authorized_thread(thread_id, login, email=email)
        session = self._session(thread_id, terminal_id)
        if thread_metadata(thread).get("sandbox_id") != session.sandbox_id:
            raise HTTPException(409, "sandbox changed; reopen the terminal")
        async with session.input_lock:
            await asyncio.to_thread(session.handle.send_input, data)

    async def stream(
        self, thread_id: str, terminal_id: str, login: str, *, email: str | None = None
    ) -> AsyncIterator[bytes]:
        await _authorized_thread(thread_id, login, email=email)
        session = self._session(thread_id, terminal_id)
        while True:
            event = await session.queue.get()
            if event is _EOF:
                return
            payload = json.dumps(event, separators=(",", ":"))
            yield f"data: {payload}\n\n".encode()

    async def close(
        self, thread_id: str, terminal_id: str, login: str, *, email: str | None = None
    ) -> None:
        await _authorized_thread(thread_id, login, email=email)
        session = self._session(thread_id, terminal_id)
        await self._stop(session)

    async def aclose(self) -> None:
        for session in list(self._sessions.values()):
            await self._stop(session)

    async def _pump(self, session: TerminalSession) -> None:
        exit_code: int | None = None
        try:
            while True:
                chunk = await asyncio.to_thread(next, session.iterator, _EOF)
                if chunk is None:
                    break
                await self._publish(
                    session, {"type": "output", "stream": chunk.stream, "data": chunk.data}
                )
            exit_code = session.handle.result.exit_code
        except Exception:
            await self._publish(session, {"type": "error", "detail": "terminal connection lost"})
        finally:
            await self._publish(session, {"type": "exit", "exitCode": exit_code})
            await self._publish(session, _EOF)
            async with self._lock:
                self._sessions.pop(session.id, None)
                if self._thread_sessions.get(session.thread_id) == session.id:
                    self._thread_sessions.pop(session.thread_id, None)

    async def _stop(self, session: TerminalSession) -> None:
        await asyncio.to_thread(session.handle.kill)
        task = session.pump_task
        if task and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2)
            except TimeoutError:
                task.cancel()
        async with self._lock:
            self._sessions.pop(session.id, None)
            if self._thread_sessions.get(session.thread_id) == session.id:
                self._thread_sessions.pop(session.thread_id, None)

    async def _publish(self, session: TerminalSession, event: dict[str, Any] | None) -> None:
        if session.queue.full():
            try:
                session.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        session.queue.put_nowait(event)

    def _session(self, thread_id: str, terminal_id: str) -> TerminalSession:
        session = self._sessions.get(terminal_id)
        if not session or session.thread_id != thread_id:
            raise HTTPException(404, "terminal not found")
        return session

    @staticmethod
    def _info(session: TerminalSession) -> dict[str, Any]:
        return {"id": session.id, "cwd": session.cwd}


terminal_manager = TerminalManager()
