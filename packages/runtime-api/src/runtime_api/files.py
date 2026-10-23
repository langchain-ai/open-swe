"""The thread's files on the backend: paths it serves, and boxes the runtime does not own.

One endpoint per operation of deepagents' sandbox backend, carrying what that
backend takes and returns, so the runtime's own file and shell tools can work
against it. Bytes travel base64-encoded.
"""

from pydantic import BaseModel

from runtime_api.endpoint import Endpoint
from runtime_api.operations import RunRequest


class FileInfo(BaseModel):
    path: str
    is_dir: bool | None = None
    size: int | None = None
    modified_at: str | None = None


class FileData(BaseModel):
    content: str
    encoding: str
    created_at: str | None = None
    modified_at: str | None = None


class ContextLine(BaseModel):
    line: int
    text: str


class GrepMatch(BaseModel):
    path: str
    line: int
    text: str
    context_before: list[ContextLine] | None = None
    context_after: list[ContextLine] | None = None


class PathRequest(RunRequest):
    path: str


class LsReply(BaseModel):
    error: str | None = None
    entries: list[FileInfo] | None = None


class ReadRequest(RunRequest):
    file_path: str
    offset: int = 0
    limit: int = 2000


class ReadReply(BaseModel):
    error: str | None = None
    file_data: FileData | None = None
    total_lines: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    next_offset: int | None = None
    no_lines_requested: bool = False


class WriteRequest(RunRequest):
    file_path: str
    content: str


class EditRequest(RunRequest):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class FileReply(BaseModel):
    """What a write, edit or delete answered."""

    error: str | None = None
    path: str | None = None
    occurrences: int | None = None


class DeleteRequest(RunRequest):
    file_path: str


class GlobRequest(RunRequest):
    pattern: str
    path: str | None = None


class GlobReply(BaseModel):
    error: str | None = None
    matches: list[FileInfo] | None = None
    truncated: bool = False
    truncation_reason: str | None = None


class GrepRequest(RunRequest):
    pattern: str
    path: str | None = None
    glob: str | None = None
    max_count: int | None = None


class GrepReply(BaseModel):
    error: str | None = None
    matches: list[GrepMatch] | None = None
    truncated: bool = False


class ExecuteRequest(RunRequest):
    command: str
    timeout: int | None = None


class ExecuteReply(BaseModel):
    output: str
    exit_code: int | None = None
    truncated: bool = False


class UploadedFile(BaseModel):
    path: str
    content_base64: str


class UploadRequest(RunRequest):
    files: list[UploadedFile]


class TransferReply(BaseModel):
    path: str
    error: str | None = None
    # A download's content, base64-encoded.
    content_base64: str | None = None


class TransferReplies(BaseModel):
    files: list[TransferReply]


class DownloadRequest(RunRequest):
    paths: list[str]


LS = Endpoint("files.ls", PathRequest, LsReply)
READ = Endpoint("files.read", ReadRequest, ReadReply)
WRITE = Endpoint("files.write", WriteRequest, FileReply)
EDIT = Endpoint("files.edit", EditRequest, FileReply)
DELETE = Endpoint("files.delete", DeleteRequest, FileReply)
GLOB = Endpoint("files.glob", GlobRequest, GlobReply)
GREP = Endpoint("files.grep", GrepRequest, GrepReply)
EXECUTE = Endpoint("files.execute", ExecuteRequest, ExecuteReply)
UPLOAD = Endpoint("files.upload", UploadRequest, TransferReplies)
DOWNLOAD = Endpoint("files.download", DownloadRequest, TransferReplies)
