"""Continuing an Open SWE thread in a local coding-agent session."""

import logging
import uuid
import zlib
from collections.abc import Iterator
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import HTTPException
from langchain_core.messages import convert_to_messages
from pydantic import BaseModel, ConfigDict

from openswe.claude_code.session_file import ClaudeSessionFile
from openswe.dashboard.oauth import DownloadTicket
from openswe.prompts import prompt
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.summary import SESSION_UPLOAD_PENDING_KEY
from openswe.utils.dashboard_links import (
    dashboard_api_base_url,
    dashboard_thread_id,
    dashboard_thread_url,
)
from openswe.utils.json_types import as_json_object
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_GZIP_WBITS = zlib.MAX_WBITS | 16

DOWNLOAD_RESPONSES: dict[str, object] = {
    "responses": {
        "200": {
            "description": (
                "The thread as a gzipped Claude Code session transcript (JSONL). "
                "Authorized by the download_token the download_session MCP tool returned, "
                "as a bearer token."
            ),
            "content": {"application/gzip": {"schema": {"type": "string", "format": "binary"}}},
        }
    }
}


class _ThreadWork(BaseModel):
    """Where a thread's agent pushed its work, as its metadata records it."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None

    @property
    def repo(self) -> str | None:
        if self.repo_owner and self.repo_name:
            return f"{self.repo_owner}/{self.repo_name}"
        return None


class SessionDownload(BaseModel):
    """A thread ready to fetch as a local session, and where and with which token to fetch it."""

    thread_id: str
    url: str | None
    session_id: str
    transcript_path: str
    repo: str | None
    branch: str | None
    pr_url: str | None
    download_url: str
    download_token: str
    expires_in_seconds: int

    @property
    def next_step(self) -> str:
        return prompt("tools/download-session-next-step", download=self)


async def _work(thread_id: str, login: str, email: str | None) -> _ThreadWork:
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    if metadata.get(SESSION_UPLOAD_PENDING_KEY) is True:
        raise HTTPException(409, "this thread is waiting for its session upload")
    return _ThreadWork.model_validate(metadata)


async def prepare_session_download(
    locator: str, cwd: str, login: str, *, email: str | None = None
) -> SessionDownload:
    """Check the caller may read the thread, then sign the token its transcript is fetched with."""
    thread_id = dashboard_thread_id(locator)
    if thread_id is None:
        raise HTTPException(422, "thread_id must be a thread ID or an Open SWE dashboard URL")
    if not (PurePosixPath(cwd).is_absolute() or PureWindowsPath(cwd).is_absolute()):
        raise HTTPException(422, "cwd must be an absolute path")
    work = await _work(thread_id, login, email)
    ticket = DownloadTicket(
        sub=login, email=email, thread_id=thread_id, session_id=str(uuid.uuid4()), cwd=cwd
    )
    return SessionDownload(
        thread_id=thread_id,
        url=dashboard_thread_url(thread_id),
        session_id=ticket.session_id,
        transcript_path=_session_file(ticket, work).path,
        repo=work.repo,
        branch=work.branch_name,
        pr_url=work.pr_url,
        download_url=f"{dashboard_api_base_url()}/dashboard/api/threads/downloads",
        download_token=ticket.issue(),
        expires_in_seconds=DownloadTicket.ttl_seconds,
    )


def _session_file(ticket: DownloadTicket, work: _ThreadWork) -> ClaudeSessionFile:
    return ClaudeSessionFile(
        session_id=ticket.session_id, cwd=ticket.cwd, git_branch=work.branch_name
    )


async def download_session(ticket: DownloadTicket) -> Iterator[bytes]:
    """The thread's conversation, as of now, as a gzipped Claude Code transcript."""
    work = await _work(ticket.thread_id, ticket.sub, ticket.email)
    state = as_json_object(await langgraph_client().threads.get_state(ticket.thread_id))
    values = as_json_object(state.get("values"))
    raw_messages = values.get("messages")
    messages = convert_to_messages(raw_messages) if isinstance(raw_messages, list) else []
    if not messages:
        raise HTTPException(422, "the thread has no messages")
    note = prompt(
        "threads/downloaded-session",
        url=dashboard_thread_url(ticket.thread_id),
        repo=work.repo,
        branch=work.branch_name,
        pr_url=work.pr_url,
    )
    lines = _session_file(ticket, work).lines(messages, title=work.title, note=note)
    return _gzipped(lines)


def _gzipped(lines: Iterator[str]) -> Iterator[bytes]:
    compressor = zlib.compressobj(wbits=_GZIP_WBITS)
    for line in lines:
        if chunk := compressor.compress(f"{line}\n".encode()):
            yield chunk
    yield compressor.flush()
