"""Run-scoped, approved local actions alongside a cloud sandbox."""

import base64
import posixpath
import shlex
import time
from dataclasses import dataclass
from typing import Self

from openswe.bridge.connection import BridgeConnection
from openswe.bridge.local_access_policy import LocalAccess, LocalReviews
from openswe.bridge.protocol import JsonObject, LocalActionParams
from openswe.run_config import RunConfig
from openswe.sandboxes.state import get_or_create_sandbox_backend_proxy
from openswe.threads.store import THREADS
from openswe.tools.access import Policy, access
from openswe.transcript.attachments import MAX_ATTACHMENT_BYTES

# Where an import lands when the agent names no sandbox path.
IMPORT_DIR = "/workspace/local-files"
# The desktop shows its dialog for this long before the action expires.
APPROVAL_SECONDS = 120


@dataclass(frozen=True)
class LocalAccessRun:
    """The current run's offered connection to its owner's desktop."""

    thread_id: str
    thread_title: str | None
    bridge_id: str

    @classmethod
    async def current(cls) -> Self:
        cfg = RunConfig.from_runtime()
        # A person who never linked GitHub runs as their Robin user, `user:<id>`.
        login = cfg.github_login or (f"user:{cfg.user_id}" if cfg.user_id else None)
        if not cfg.thread_id or not login or not cfg.sender_id:
            raise PermissionError("Local access requires an authenticated owner run")
        thread = await THREADS.get(cfg.thread_id)
        if thread is None:
            raise PermissionError("Local access thread is unavailable")
        bridge_id = await LocalAccess.offer(
            cfg.local_access_bridge_id,
            metadata=thread.metadata,
            login=login,
            owner_id=cfg.sender_id,
        )
        if bridge_id is None:
            raise PermissionError("No local access was offered for this run")
        title = thread.metadata.get("title")
        return cls(
            thread_id=cfg.thread_id,
            thread_title=title[:200] if isinstance(title, str) else None,
            bridge_id=bridge_id,
        )

    async def perform(self, params: LocalActionParams) -> JsonObject:
        review = LocalReviews.current()
        connection = await BridgeConnection.connect(self.bridge_id)
        return await connection.local_action(
            params.model_copy(
                update={
                    "expires_at": time.time() + APPROVAL_SECONDS,
                    "thread_id": self.thread_id,
                    "thread_title": self.thread_title,
                    "cleared": review is not None and review.cleared,
                    "concern": review.concern if review is not None else None,
                }
            )
        )

    async def import_file(self, path: str, sandbox_path: str | None) -> JsonObject:
        """Copy one approved local file into this thread's cloud sandbox."""
        result = await self.perform(LocalActionParams(operation="import", path=path))
        encoded = result.get("content_base64")
        local_path = result.get("path")
        if not isinstance(encoded, str) or not isinstance(local_path, str):
            raise ValueError("The desktop returned no file contents")
        content = base64.b64decode(encoded, validate=True)
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Local imports are limited to 10 MiB")
        destination = sandbox_path or posixpath.join(IMPORT_DIR, posixpath.basename(local_path))
        if not posixpath.isabs(destination):
            raise ValueError("sandbox_path must be absolute")
        sandbox = get_or_create_sandbox_backend_proxy(self.thread_id)
        await sandbox.aexecute(f"mkdir -p {shlex.quote(posixpath.dirname(destination))}")
        [upload] = await sandbox.aupload_files([(destination, content)])
        if upload.error:
            raise RuntimeError(f"Could not write {destination} in the sandbox: {upload.error}")
        return {"local_path": local_path, "sandbox_path": destination, "bytes": len(content)}


@access(Policy("private", actor="owner"))
async def local_read_file(path: str) -> JsonObject:
    """Read one local file after the desktop owner approves its exact path."""
    run = await LocalAccessRun.current()
    return await run.perform(LocalActionParams(operation="read", path=path))


@access(Policy("private", actor="owner"))
async def local_write_file(path: str, content: str) -> JsonObject:
    """Write one local file after the desktop owner approves its path and contents."""
    run = await LocalAccessRun.current()
    return await run.perform(LocalActionParams(operation="write", path=path, content=content))


@access(Policy("private", actor="owner"))
async def local_execute(command: str, cwd: str, timeout: int = 120) -> JsonObject:
    """Run an explicitly requested local command after exact native approval."""
    run = await LocalAccessRun.current()
    return await run.perform(
        LocalActionParams(operation="execute", command=command, cwd=cwd, timeout=timeout)
    )


@access(Policy("private", actor="owner"))
async def local_import_file(path: str, sandbox_path: str | None = None) -> JsonObject:
    """Copy one local file into the cloud sandbox after the desktop owner approves it."""
    run = await LocalAccessRun.current()
    return await run.import_file(path, sandbox_path)
