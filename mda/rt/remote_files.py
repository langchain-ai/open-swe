"""The thread's files on the Open SWE backend, as a deepagents sandbox backend.

The backend serves the paths it holds (skills, offloaded images) and the files of
a thread whose box is not MDA's, such as one bridged to a person's machine. This
backend reaches them through the typed files API (``runtime_api.files``), so the
file and shell tools run here, as they do in the box MDA owns.
"""

import base64
from collections.abc import Callable
from typing import Final

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    ContextLine,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from runtime_api import files as file_api
from runtime_api.endpoint import Endpoint, Transport, call
from runtime_api.operations import RunRequest

from rt.backend import LONG_CALL_SECONDS, TOOL_CALL_SLACK_SECONDS

_SYNC: Final = "The backend's files are reached asynchronously"
# deepagents' file and shell tools.
FILE_TOOLS: Final = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)


def _file_info(entry: file_api.FileInfo) -> FileInfo:
    info = FileInfo(path=entry.path)
    if entry.is_dir is not None:
        info["is_dir"] = entry.is_dir
    if entry.size is not None:
        info["size"] = entry.size
    if entry.modified_at is not None:
        info["modified_at"] = entry.modified_at
    return info


def _file_data(data: file_api.FileData) -> FileData:
    file_data = FileData(content=data.content, encoding=data.encoding)
    if data.created_at is not None:
        file_data["created_at"] = data.created_at
    if data.modified_at is not None:
        file_data["modified_at"] = data.modified_at
    return file_data


def _context(lines: list[file_api.ContextLine]) -> list[ContextLine]:
    return [ContextLine(line=line.line, text=line.text) for line in lines]


def _grep_match(match: file_api.GrepMatch) -> GrepMatch:
    found = GrepMatch(path=match.path, line=match.line, text=match.text)
    if match.context_before is not None:
        found["context_before"] = _context(match.context_before)
    if match.context_after is not None:
        found["context_after"] = _context(match.context_after)
    return found


class RemoteFiles(SandboxBackendProtocol):
    """The thread's files as the backend serves them, for this run."""

    def __init__(self, transport: Transport, run_id: Callable[[], str | None]) -> None:
        self._transport = transport
        self._run_id = run_id

    @property
    def id(self) -> str:
        return "open-swe-backend"

    async def _call[Request: RunRequest, Reply: BaseModel](
        self, endpoint: Endpoint[Request, Reply], request: Request, *, timeout: float | None = None
    ) -> Reply:
        return await call(
            self._transport,
            endpoint,
            request.model_copy(update={"run_id": self._run_id()}),
            timeout=timeout,
        )

    async def als(self, path: str) -> LsResult:
        reply = await self._call(file_api.LS, file_api.PathRequest(path=path))
        return LsResult(
            error=reply.error,
            entries=None
            if reply.entries is None
            else [_file_info(entry) for entry in reply.entries],
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        reply = await self._call(
            file_api.READ, file_api.ReadRequest(file_path=file_path, offset=offset, limit=limit)
        )
        return ReadResult(
            error=reply.error,
            file_data=None if reply.file_data is None else _file_data(reply.file_data),
            total_lines=reply.total_lines,
            start_line=reply.start_line,
            end_line=reply.end_line,
            next_offset=reply.next_offset,
            no_lines_requested=reply.no_lines_requested,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        reply = await self._call(
            file_api.WRITE, file_api.WriteRequest(file_path=file_path, content=content)
        )
        return WriteResult(error=reply.error, path=reply.path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        reply = await self._call(
            file_api.EDIT,
            file_api.EditRequest(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            ),
        )
        return EditResult(error=reply.error, path=reply.path, occurrences=reply.occurrences)

    async def adelete(self, file_path: str) -> DeleteResult:
        reply = await self._call(file_api.DELETE, file_api.DeleteRequest(file_path=file_path))
        return DeleteResult(error=reply.error, path=reply.path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        reply = await self._call(file_api.GLOB, file_api.GlobRequest(pattern=pattern, path=path))
        return GlobResult(
            error=reply.error,
            matches=None
            if reply.matches is None
            else [_file_info(match) for match in reply.matches],
            truncated=reply.truncated,
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        reply = await self._call(
            file_api.GREP,
            file_api.GrepRequest(pattern=pattern, path=path, glob=glob, max_count=max_count),
        )
        return GrepResult(
            error=reply.error,
            matches=None
            if reply.matches is None
            else [_grep_match(match) for match in reply.matches],
            truncated=reply.truncated,
        )

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: ASYNC109
        # A command may run out its own timeout and answer that it did, past the
        # call's usual deadline.
        reply = await self._call(
            file_api.EXECUTE,
            file_api.ExecuteRequest(command=command, timeout=timeout),
            timeout=None
            if timeout is None
            else max(LONG_CALL_SECONDS, timeout + TOOL_CALL_SLACK_SECONDS),
        )
        return ExecuteResponse(
            output=reply.output, exit_code=reply.exit_code, truncated=reply.truncated
        )

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        reply = await self._call(
            file_api.UPLOAD,
            file_api.UploadRequest(
                files=[
                    file_api.UploadedFile(path=path, content_base64=base64.b64encode(data).decode())
                    for path, data in files
                ]
            ),
        )
        return [FileUploadResponse(path=item.path, error=item.error) for item in reply.files]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        reply = await self._call(file_api.DOWNLOAD, file_api.DownloadRequest(paths=paths))
        return [
            FileDownloadResponse(
                path=item.path,
                error=item.error,
                content=None
                if item.content_base64 is None
                else base64.b64decode(item.content_base64),
            )
            for item in reply.files
        ]


def remote_file_tools(remote: RemoteFiles, artifacts_root: str) -> dict[str, BaseTool]:
    """deepagents' own file and shell tools, against the backend's files.

    Large results go under the thread's artifacts root, as on the backend, not the
    root of a person's machine; the tools read it only from a composite backend.
    """
    files = CompositeBackend(default=remote, routes={}, artifacts_root=artifacts_root)
    return {tool.name: tool for tool in FilesystemMiddleware(backend=files).tools}
