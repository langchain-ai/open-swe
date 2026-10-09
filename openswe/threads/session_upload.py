"""Turning a local coding-agent session into an Open SWE thread that continues its work."""

import codecs
import logging
import uuid
import zlib
from collections.abc import AsyncIterator, Awaitable, Iterator
from typing import Any, Literal, Self

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from openswe.claude_code.transcript import ClaudeTranscript, TranscriptError
from openswe.dashboard.oauth import UPLOAD_TICKET_TTL_SECONDS, UploadTicket, issue_upload_ticket
from openswe.dashboard.profiles import get_profile
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, GitHubError
from openswe.github.pull_requests import PullRequest
from openswe.input_messages import SystemIdentity, build_input_messages
from openswe.prompts import prompt
from openswe.slack.client import parse_github_pr_url
from openswe.threads.creation import create_thread
from openswe.threads.diffs import _safe_git_ref
from openswe.threads.runs import (
    _ASSISTANT_ID,
    _resolve_agent_model_choice,
    _resolve_requested_workspace,
)
from openswe.threads.summary import (
    DASHBOARD_SOURCE,
    SESSION_UPLOAD_PENDING_KEY,
    _now_ms,
    _parse_repo,
    _thread_summary,
)
from openswe.transcript.mirror import mirror_thread_metadata
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_api_base_url, dashboard_thread_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_participants import participant_metadata

logger = logging.getLogger(__name__)

_INFLATE_CHUNK_BYTES = 1024 * 1024
_PR_LINK_SOURCE = "session_upload"
_UPLOAD_SENDER: SystemIdentity = {
    "id": "system:session-upload",
    "display_name": "Session upload",
    "platform": "open-swe",
}

type SessionType = Literal["claude"]


class SessionUploadHeader(BaseModel):
    """The upload's first line: which agent wrote the transcript that follows, and where its work was pushed."""

    model_config = ConfigDict(extra="forbid")

    type: SessionType
    repo: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    visibility: Literal["workspace", "private"] = "workspace"

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if self.pr_url is not None:
            if self.repo is not None or self.branch is not None:
                raise ValueError("pass either pr_url or repo and branch, not both")
        elif self.repo is None or self.branch is None:
            raise ValueError("pass repo and branch, or pr_url")
        return self


UPLOAD_REQUEST_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "description": (
            "The session transcript as JSONL, verbatim, optionally with Content-Encoding: gzip. "
            "Authorized by the upload code in the upload_url the upload_session MCP tool returned."
        ),
        "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
    }
}


class UploadStream:
    """The request body as JSONL lines, inflated and decoded chunk by chunk, never held whole.

    Clients gzip the body because Vercel caps a request at 4.5 MB before the backend proxy.
    """

    def __init__(self, request: Request) -> None:
        encoding = request.headers.get("content-encoding", "").strip().lower()
        if encoding not in {"", "identity", "gzip"}:
            raise HTTPException(415, "session uploads must be gzip or uncompressed")
        self._request = request
        self._inflater = (
            zlib.decompressobj(wbits=zlib.MAX_WBITS | 16) if encoding == "gzip" else None
        )
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._partial: list[str] = []

    async def lines(self) -> AsyncIterator[str]:
        try:
            async for chunk in self._request.stream():
                for text in self._decoded(chunk):
                    for line in self._split(text):
                        yield line
            for line in self._split(self._finish()):
                yield line
        except (zlib.error, UnicodeDecodeError) as exc:
            raise HTTPException(400, "session upload is not valid gzipped UTF-8") from exc
        if self._partial:
            yield "".join(self._partial)

    def _decoded(self, chunk: bytes) -> Iterator[str]:
        if self._inflater is None:
            yield self._decoder.decode(chunk)
            return
        yield self._decoder.decode(self._inflater.decompress(chunk, _INFLATE_CHUNK_BYTES))
        while self._inflater.unconsumed_tail:
            data = self._inflater.decompress(self._inflater.unconsumed_tail, _INFLATE_CHUNK_BYTES)
            yield self._decoder.decode(data)

    def _finish(self) -> str:
        tail = b""
        if self._inflater is not None:
            tail = self._inflater.flush()
            if not self._inflater.eof:
                raise HTTPException(400, "session upload ended partway through the gzip stream")
            if self._inflater.unused_data:
                raise HTTPException(400, "session upload has data after the gzip stream")
        return self._decoder.decode(tail, final=True)

    def _split(self, text: str) -> Iterator[str]:
        start = 0
        while (end := text.find("\n", start)) != -1:
            self._partial.append(text[start:end])
            yield "".join(self._partial)
            self._partial.clear()
            start = end + 1
        if start < len(text):
            self._partial.append(text[start:])


class _PullRequestRepo(BaseModel):
    full_name: str


class _PullRequestHead(BaseModel):
    ref: str
    repo: _PullRequestRepo | None


class _PullRequestPayload(BaseModel):
    head: _PullRequestHead


class _Target(BaseModel):
    owner: str
    name: str
    branch: str
    pr_url: str | None = None
    pr_number: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


async def _found[T](read: Awaitable[T]) -> T | None:
    """``read``'s answer, or ``None`` when GitHub reports the resource missing."""
    try:
        return await read
    except GitHubError as exc:
        if exc.response.status_code == 404:
            return None
        logger.warning(
            "GitHub read for a session upload failed",
            extra={"github_url": str(exc.request.url), "github_status": exc.response.status_code},
        )
        raise HTTPException(502, "could not read from GitHub") from exc


async def _pull_request_target(pr_url: str, login: str) -> _Target:
    ref = parse_github_pr_url(pr_url)
    if ref is None:
        raise HTTPException(422, "pr_url must be a github.com pull request URL")
    full_name = f"{ref.owner}/{ref.repo}"
    await require_repo_access_for_user(login, full_name)
    async with GitHubClient.as_user(login) as github:
        payload = await _found(github.repo(ref.owner, ref.repo).pull_request(ref.number).pull())
    if payload is None:
        raise HTTPException(404, "pull request not found")
    try:
        head = _PullRequestPayload.model_validate(payload).head
    except ValidationError as exc:
        raise HTTPException(502, "GitHub returned a malformed pull request") from exc
    if head.repo is None or head.repo.full_name.lower() != full_name.lower():
        raise HTTPException(
            422,
            f"pull requests from forks are not supported; push the work to a branch on "
            f"{full_name} and pass repo and branch",
        )
    return _Target(
        owner=ref.owner, name=ref.repo, branch=head.ref, pr_url=ref.url, pr_number=ref.number
    )


async def _branch_target(repo: str, branch: str, login: str) -> _Target:
    repo_config = _parse_repo(repo)
    if repo_config is None:
        raise HTTPException(422, "repo must be owner/name")
    safe_branch = _safe_git_ref(branch.strip())
    if safe_branch is None:
        raise HTTPException(422, "branch is not a valid git branch name")
    full_name = f"{repo_config['owner']}/{repo_config['name']}"
    await require_repo_access_for_user(login, full_name)
    async with GitHubClient.as_user(login) as github:
        found = await _found(
            github.repo(repo_config["owner"], repo_config["name"]).branch(safe_branch)
        )
    if found is None:
        raise HTTPException(422, f"branch {safe_branch} is not on {full_name}; push it first")
    return _Target(owner=repo_config["owner"], name=repo_config["name"], branch=safe_branch)


async def _target(header: SessionUploadHeader, login: str) -> _Target:
    if header.pr_url is not None:
        return await _pull_request_target(header.pr_url, login)
    if header.repo is not None and header.branch is not None:
        return await _branch_target(header.repo, header.branch, login)
    raise HTTPException(422, "pass repo and branch, or pr_url")


def _upload_note(target: _Target) -> list[HumanMessage]:
    data: dict[str, str] = {"repository": target.full_name, "branch": target.branch}
    if target.pr_url is not None:
        data["pull_request"] = target.pr_url
    notes: list[HumanMessage] = []
    for message in build_input_messages(
        prompt("runs/uploaded-session"),
        {
            "sender_id": _UPLOAD_SENDER["id"],
            "surface": "automation",
            "kind": "system",
            "data": data,
        },
        systems=[_UPLOAD_SENDER],
    ):
        content = message["content"]
        notes.append(
            HumanMessage(
                content=content if isinstance(content, str) else list[str | dict[str, Any]](content)
            )
        )
    return notes


class SessionReservation(BaseModel):
    """A thread waiting for its transcript, and the one-time URL the transcript is posted to."""

    thread_id: str
    url: str | None
    upload_url: str
    expires_in_seconds: int


async def reserve_session_upload(
    header: SessionUploadHeader, login: str, *, email: str | None = None
) -> SessionReservation:
    """Create the thread an uploaded session continues in, before its transcript arrives."""
    target = await _target(header, login)
    repo_config = {"owner": target.owner, "name": target.name}
    workspace = await _resolve_requested_workspace(None, repo_config, login=login)
    profile = await get_profile(login) or {}
    resolved_model, resolved_effort = await _resolve_agent_model_choice(
        profile, None, None, workspace
    )
    now_ms = _now_ms()
    title = f"Uploaded session on {target.branch}"
    metadata: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        "origin": DASHBOARD_SOURCE,
        "owner_type": "user",
        "owner_login": login.strip(),
        "visibility": "private" if header.visibility == "private" else "public",
        "thread_category": "interactive",
        "trigger_kind": "user",
        **await participant_metadata({}, login=login, email=email),
        "title": title,
        "workspace": workspace,
        "repo_owner": target.owner,
        "repo_name": target.name,
        "branch_name": target.branch,
        "base_branch": profile.get("base_branch") or "main",
        "branch_prefix": profile.get("branch_prefix"),
        "model": profile.get("default_model") or "Default",
        "effort": profile.get("reasoning_effort"),
        "resolved_model": resolved_model,
        "resolved_effort": resolved_effort,
        "uploaded_session_type": header.type,
        SESSION_UPLOAD_PENDING_KEY: True,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        # update_state refuses a thread with no graph, and LangGraph only
        # stamps graph_id once a run has happened.
        "graph_id": _ASSISTANT_ID,
    }
    if target.pr_url is not None:
        metadata["pr_url"] = target.pr_url
        metadata["pr_number"] = target.pr_number
        metadata["source_context"] = {"pr_number": target.pr_number}

    thread_id = str(uuid.uuid4())
    await create_thread(
        langgraph_client(), thread_id, title=title, metadata=metadata, if_exists="raise"
    )
    user = await User.for_login("github", login)
    code = issue_upload_ticket(
        login=login, email=email, user_id=str(user.id) if user else None, thread_id=thread_id
    )
    return SessionReservation(
        thread_id=thread_id,
        url=dashboard_thread_url(thread_id),
        upload_url=f"{dashboard_api_base_url()}/dashboard/api/threads/uploads/{code}",
        expires_in_seconds=UPLOAD_TICKET_TTL_SECONDS,
    )


class _ReservedThread(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_login: str
    repo_owner: str
    repo_name: str
    branch_name: str
    pr_url: str | None = None
    pr_number: int | None = None
    session_upload_pending: bool = False

    @property
    def target(self) -> _Target:
        return _Target(
            owner=self.repo_owner,
            name=self.repo_name,
            branch=self.branch_name,
            pr_url=self.pr_url,
            pr_number=self.pr_number,
        )


async def upload_session(stream: UploadStream, ticket: UploadTicket) -> dict[str, Any]:
    """Seed a reserved thread with the session's history; no run starts until the person sends one."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(ticket.thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "the reserved thread no longer exists") from exc
    try:
        reserved = _ReservedThread.model_validate(thread_metadata(thread))
    except ValidationError as exc:
        raise HTTPException(409, "this thread was not reserved for a session upload") from exc
    if reserved.owner_login.lower() != ticket.sub.lower() or not reserved.session_upload_pending:
        raise HTTPException(409, "this upload code was already used")
    transcript = ClaudeTranscript()
    async for line in stream.lines():
        transcript.add(line)
    try:
        session = transcript.session()
    except TranscriptError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not session.messages:
        raise HTTPException(422, "the transcript has no messages")

    thread_id = ticket.thread_id
    messages: list[BaseMessage] = [*session.messages, *_upload_note(reserved.target)]
    try:
        await client.threads.update_state(thread_id, values={"messages": messages})
        # Seeding leaves the graph's first node pending, which reads as a live run.
        await client.threads.update_state(thread_id, values=None, as_node="__end__")
    except Exception as exc:
        logger.warning(
            "Could not seed an uploaded session's thread",
            extra={"thread_id": thread_id, "message_count": len(messages)},
            exc_info=True,
        )
        raise HTTPException(502, "failed to store the session transcript") from exc
    update: dict[str, Any] = {SESSION_UPLOAD_PENDING_KEY: False, "updated_at_ms": _now_ms()}
    if session.title:
        update["title"] = session.title
    await client.threads.update(thread_id=thread_id, metadata=update)
    await mirror_thread_metadata(thread_id, update)
    # Linked only once the thread holds the session, so an abandoned reservation stays off the PR.
    if reserved.pr_number is not None:
        await _link_pull_request(reserved.target, reserved.pr_number, thread_id)
    return await _thread_summary(await client.threads.get(thread_id))


async def _link_pull_request(target: _Target, number: int, thread_id: str) -> None:
    try:
        await PullRequest(owner=target.owner, repo=target.name, number=number).link_thread(
            thread_id, source=_PR_LINK_SOURCE
        )
    except Exception:
        logger.warning(
            "Failed to link an uploaded session's thread to its pull request",
            extra={"pr_repo_full_name": target.full_name, "pr_number": target.pr_number},
            exc_info=True,
        )
