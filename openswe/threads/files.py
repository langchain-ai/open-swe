"""Browsing a thread's sandbox workspace: list a directory, read a file, or index files."""

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter

from openswe.sandboxes import scripts as sandbox_scripts
from openswe.sandboxes.ranges import FileTooLargeError, RangeReadable
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.diffs import _download_content, _response_output, connect_sandbox
from openswe.threads.summary import _assert_thread_promptable, _metadata_repo

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

_WORKSPACE_PATH_TIMEOUT_SECONDS = 30


class WorkspaceEntry(BaseModel):
    path: str
    kind: Literal["file", "directory"]
    ignored: bool


class WorkspaceDirectory(BaseModel):
    kind: Literal["directory"]
    entries: list[WorkspaceEntry]


class WorkspaceFile(BaseModel):
    kind: Literal["file"]
    contents: str
    binary: bool
    truncated: bool
    size: int


WorkspacePath = Annotated[WorkspaceDirectory | WorkspaceFile, Field(discriminator="kind")]


class WorkspaceFileIndex(BaseModel):
    """Every non-ignored file in the workspace, for search."""

    paths: list[str]


class WorkspaceFileStat(BaseModel):
    """A regular file in the workspace, by its path with every symlink resolved."""

    # The workspace root with every symlink resolved; `path` is relative to it.
    root: str
    path: str
    absolute: str
    size: int


_WORKSPACE_PATH_ADAPTER: TypeAdapter[WorkspaceDirectory | WorkspaceFile] = TypeAdapter(
    WorkspacePath
)

ScriptMode = Literal["path", "index", "stat"]


class ThreadWorkspace:
    """A thread's live sandbox, opened for someone who may prompt the thread.

    Reading the live sandbox is shell-equivalent access, so it follows the
    terminal's rule: admins who may view a private thread still cannot.
    """

    # Every read is buffered in memory, a whole file from sandboxes that cannot serve a range.
    _reads: ClassVar[asyncio.Semaphore] = asyncio.Semaphore(8)

    def __init__(
        self, thread_id: str, sandbox_id: str, sandbox: SandboxBackendProtocol, root: str
    ) -> None:
        self.thread_id = thread_id
        self.sandbox_id = sandbox_id
        self.sandbox = sandbox
        self.root = root

    @classmethod
    async def open(
        cls,
        thread_id: str,
        login: str,
        email: str | None,
        *,
        sandbox_id: str | None = None,
        root: str | None = None,
    ) -> Self:
        """Authorize ``login`` and connect; ``sandbox_id`` and ``root`` pin an earlier opening."""
        from openswe.sandboxes.paths import resolve_checkout_dir, resolve_sandbox_work_dir

        metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
        _assert_thread_promptable(metadata, login)
        current = metadata.get("sandbox_id")
        if not isinstance(current, str) or not current:
            raise HTTPException(404, "thread has no workspace")
        if sandbox_id is not None and current != sandbox_id:
            raise HTTPException(404, "The workspace changed. Open the file again.")
        try:
            sandbox = await connect_sandbox(current, thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not connect to sandbox for files", extra={"sandbox": current})
            raise HTTPException(503, "Could not connect to the workspace.") from exc
        if root is None:
            work_dir = await resolve_sandbox_work_dir(sandbox)
            _, repo_name, _ = _metadata_repo(metadata)
            root = (
                await resolve_checkout_dir(sandbox, work_dir, repo_name) if repo_name else work_dir
            )
        return cls(thread_id, current, sandbox, root)

    async def run_script(
        self, mode: ScriptMode, path: str, *, within: str | None = None
    ) -> dict[str, object]:
        payload = {
            "root": self.root,
            "mode": mode,
            "path": path,
            "within": within,
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        script = sandbox_scripts.source("workspace_path.py").replace("__PAYLOAD__", encoded)
        result = await self.sandbox.aexecute(
            f"python3 - <<'PY'\n{script}PY", timeout=_WORKSPACE_PATH_TIMEOUT_SECONDS
        )
        output = _response_output(result).strip()
        try:
            response = json.loads(output.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            logger.warning("Invalid workspace path response", extra={"thread_id": self.thread_id})
            raise HTTPException(502, "Could not read the workspace.") from exc
        if "error" in response:
            raise HTTPException(404, response["error"])
        return response

    async def stat(self, path: str, *, within: str | None = None) -> WorkspaceFileStat:
        """The regular file at ``path``; with ``within``, only if it resolves inside that folder."""
        return WorkspaceFileStat.model_validate(await self.run_script("stat", path, within=within))

    async def read(self, file: WorkspaceFileStat, start: int, end: int, *, limit: int) -> bytes:
        """Bytes ``start`` to ``end`` inclusive, fetching no more than that where the sandbox can.

        The file is held to ``limit`` bytes, in case it grew after it was stat'ed.
        """
        try:
            async with self._reads:
                if isinstance(self.sandbox, RangeReadable):
                    return await self.sandbox.aread_range(file.absolute, start, end, limit=limit)
                [download] = await self.sandbox.adownload_files([file.absolute])
        except FileNotFoundError as exc:
            raise HTTPException(404, "Path not found.") from exc
        except FileTooLargeError as exc:
            raise HTTPException(413, "File is too large to preview.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Workspace file read failed", extra={"thread_id": self.thread_id})
            raise HTTPException(502, "Could not read the workspace.") from exc
        content = _download_content(download)
        if content is None:
            raise HTTPException(404, "Path not found.")
        if len(content) > limit:
            raise HTTPException(413, "File is too large to preview.")
        return content[start : end + 1]


async def get_dashboard_thread_path(
    thread_id: str, login: str, path: str, *, email: str | None = None
) -> WorkspaceDirectory | WorkspaceFile:
    """List a directory or read a file relative to the thread's repository."""
    workspace = await ThreadWorkspace.open(thread_id, login, email)
    return _WORKSPACE_PATH_ADAPTER.validate_python(await workspace.run_script("path", path))


async def get_dashboard_thread_file_index(
    thread_id: str, login: str, *, email: str | None = None
) -> WorkspaceFileIndex:
    """List every non-ignored file in the thread's repository."""
    workspace = await ThreadWorkspace.open(thread_id, login, email)
    return WorkspaceFileIndex.model_validate(await workspace.run_script("index", ""))
