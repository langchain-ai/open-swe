"""The calls of a run's file and shell tools that reach the thread's files on the backend.

MDA runs those tools itself, in the thread's sandbox; a path the backend serves rather
than the sandbox, such as the run's skills, is answered here.
"""

import base64
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Protocol

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from runtime_api.files import (
    DeleteRequest,
    DownloadRequest,
    EditRequest,
    ExecuteReply,
    ExecuteRequest,
    FileReply,
    GlobReply,
    GlobRequest,
    GrepReply,
    GrepRequest,
    LsReply,
    PathRequest,
    ReadReply,
    ReadRequest,
    TransferReplies,
    TransferReply,
    UploadRequest,
    WriteRequest,
)


def _b64(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode()


class ReachesFiles(Protocol):
    """A run whose calls reach the thread's files."""

    async def thread_files(self) -> BackendProtocol: ...

    async def in_run[T](self, name: str, work: Callable[[], Awaitable[T]]) -> T: ...


async def files_ls(run: ReachesFiles, request: PathRequest) -> LsReply:
    files = await run.thread_files()
    result = await run.in_run("files.ls", lambda: files.als(request.path))
    return LsReply.model_validate(asdict(result))


async def files_read(run: ReachesFiles, request: ReadRequest) -> ReadReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.read", lambda: files.aread(request.file_path, request.offset, request.limit)
    )
    return ReadReply.model_validate(asdict(result))


async def files_write(run: ReachesFiles, request: WriteRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.write", lambda: files.awrite(request.file_path, request.content)
    )
    return FileReply.model_validate(asdict(result))


async def files_edit(run: ReachesFiles, request: EditRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.edit",
        lambda: files.aedit(
            request.file_path, request.old_string, request.new_string, request.replace_all
        ),
    )
    return FileReply.model_validate(asdict(result))


async def files_delete(run: ReachesFiles, request: DeleteRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run("files.delete", lambda: files.adelete(request.file_path))
    return FileReply.model_validate(asdict(result))


async def files_glob(run: ReachesFiles, request: GlobRequest) -> GlobReply:
    files = await run.thread_files()
    result = await run.in_run("files.glob", lambda: files.aglob(request.pattern, request.path))
    return GlobReply.model_validate(asdict(result))


async def files_grep(run: ReachesFiles, request: GrepRequest) -> GrepReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.grep",
        lambda: files.agrep(
            request.pattern, request.path, request.glob, max_count=request.max_count
        ),
    )
    return GrepReply.model_validate(asdict(result))


async def files_execute(run: ReachesFiles, request: ExecuteRequest) -> ExecuteReply:
    files = await run.thread_files()
    if not isinstance(files, SandboxBackendProtocol | CompositeBackend):
        return ExecuteReply(output="Error: this thread's files cannot run commands", exit_code=1)
    result = await run.in_run(
        "files.execute", lambda: files.aexecute(request.command, timeout=request.timeout)
    )
    return ExecuteReply(
        output=result.output, exit_code=result.exit_code, truncated=result.truncated
    )


async def files_upload(run: ReachesFiles, request: UploadRequest) -> TransferReplies:
    files = await run.thread_files()
    uploads = [(file.path, base64.b64decode(file.content_base64)) for file in request.files]
    uploaded = await run.in_run("files.upload", lambda: files.aupload_files(uploads))
    return TransferReplies(
        files=[TransferReply(path=reply.path, error=reply.error) for reply in uploaded]
    )


async def files_download(run: ReachesFiles, request: DownloadRequest) -> TransferReplies:
    files = await run.thread_files()
    downloaded = await run.in_run("files.download", lambda: files.adownload_files(request.paths))
    return TransferReplies(
        files=[
            TransferReply(path=reply.path, error=reply.error, content_base64=_b64(reply.content))
            for reply in downloaded
        ]
    )
