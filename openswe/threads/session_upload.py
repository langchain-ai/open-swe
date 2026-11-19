"""Turning a local coding-agent session into an Open SWE thread that continues its work."""

import codecs
import logging
import uuid
import zlib
from collections.abc import AsyncIterator, Awaitable, Iterator, Mapping
from typing import Any, Literal, Self

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from openswe.claude_code.transcript import ClaudeTranscript
from openswe.codex.transcript import CodexTranscript
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
)
from openswe.threads.session_messages import LocalSession, Transcript, TranscriptError
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

type SessionType = Literal["claude", "codex"]

_AGENT_NAMES: dict[SessionType, str] = {"claude": "Claude Code", "codex": "Codex"}
_TRANSCRIPTS: dict[SessionType, type[Transcript]] = {
    "claude": ClaudeTranscript,
    "codex": CodexTranscript,
}


class SessionUploadHeader(BaseModel):
    """Which agent wrote the transcript, and where its work was pushed; nowhere imports past history."""

    model_config = ConfigDict(extra="forbid")

    type: SessionType
    repo: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    visibility: Literal["workspace", "private"] | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if self.pr_url is not None and (self.repo is not None or self.branch is not None):
            raise ValueError("pass either pr_url or repo and branch, not both")
        if (self.repo is None) != (self.branch is None):
            raise ValueError("pass repo and branch together")
        return self

    @property
    def is_import(self) -> bool:
        return self.pr_url is None and self.repo is None

    @property
    def private(self) -> bool:
        """Imported history defaults to private, since nobody reviewed what it holds."""
        return (self.visibility or ("private" if self.is_import else "workspace")) == "private"

    def import_thread_id(self, owner: str, session_key: str) -> str:
        """The thread an imported session lands in, the same for every repost of it."""
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"session-import:{owner}:{self.type}:{session_key}")
        )

    async def thread_metadata(
        self, target: _Target | None, login: str, email: str | None, owner_user_id: str | None
    ) -> tuple[str, dict[str, object]]:
        """The title and metadata of the thread this upload lands in, still waiting for its transcript."""
        profile = await get_profile(login) or {}
        resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, None, None)
        now_ms = _now_ms()
        title = (
            f"Uploaded session on {target.branch}"
            if target
            else f"Imported {_AGENT_NAMES[self.type]} session"
        )
        metadata: dict[str, object] = {
            "source": DASHBOARD_SOURCE,
            "origin": DASHBOARD_SOURCE,
            "owner_type": "user",
            "owner_login": login.strip(),
            "owner_user_id": owner_user_id,
            "visibility": "private" if self.private else "public",
            "thread_category": "interactive",
            "trigger_kind": "user",
            **await participant_metadata({}, login=login, email=email),
            "title": title,
            "base_branch": profile.get("base_branch") or "main",
            "branch_prefix": profile.get("branch_prefix"),
            "model": "Default",
            "effort": None,
            "resolved_model": resolved_model,
            "resolved_effort": resolved_effort,
            "uploaded_session_type": self.type,
            SESSION_UPLOAD_PENDING_KEY: True,
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            # update_state refuses a thread with no graph, and LangGraph only
            # stamps graph_id once a run has happened.
            "graph_id": _ASSISTANT_ID,
        }
        if target is None:
            metadata["repo_explicitly_none"] = True
        else:
            metadata |= {
                "repo_owner": target.owner,
                "repo_name": target.name,
                "branch_name": target.branch,
            }
        if target is not None and target.pr_url is not None:
            metadata["pr_url"] = target.pr_url
            metadata["pr_number"] = target.pr_number
            metadata["source_context"] = {"pr_number": target.pr_number}
        return title, metadata


UPLOAD_REQUEST_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "description": (
            "The session transcript as JSONL, verbatim, optionally with Content-Encoding: gzip. "
            "Authorized by the upload_token the upload_session MCP tool returned, as a bearer token."
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


async def _target(header: SessionUploadHeader, login: str) -> _Target | None:
    if header.pr_url is not None:
        return await _pull_request_target(header.pr_url, login)
    if header.repo is not None and header.branch is not None:
        return await _branch_target(header.repo, header.branch, login)
    return None


def _upload_note(target: _Target | None, session_type: SessionType) -> list[HumanMessage]:
    data: dict[str, str] = {}
    if target is not None:
        data = {"repository": target.full_name, "branch": target.branch}
        if target.pr_url is not None:
            data["pull_request"] = target.pr_url
    notes: list[HumanMessage] = []
    for message in build_input_messages(
        prompt(
            "runs/uploaded-session", agent=_AGENT_NAMES[session_type], pushed=target is not None
        ),
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
                content=content
                if isinstance(content, str)
                else list[str | dict[str, Any]](content),
                additional_kwargs=message.get("additional_kwargs", {}),
            )
        )
    return notes


class SessionReservation(BaseModel):
    """Where and with which token to post transcripts; a continued session's thread waits for its own."""

    thread_id: str | None
    url: str | None
    upload_url: str
    upload_token: str
    expires_in_seconds: int


async def _create_upload_thread(
    thread_id: str,
    header: SessionUploadHeader,
    target: _Target | None,
    login: str,
    email: str | None,
    owner_user_id: str | None,
) -> None:
    title, metadata = await header.thread_metadata(target, login, email, owner_user_id)
    await create_thread(
        langgraph_client(), thread_id, title=title, metadata=metadata, if_exists="raise"
    )


async def reserve_session_upload(
    header: SessionUploadHeader, login: str, *, email: str | None = None
) -> SessionReservation:
    """Create the thread a continued session lands in, or a token that imports any number of sessions."""
    user = await User.for_login("github", login)
    user_id = str(user.id) if user else None
    thread_id: str | None = None
    if header.is_import:
        code = issue_upload_ticket(
            login=login, email=email, user_id=user_id, import_header=header.model_dump()
        )
    else:
        thread_id = str(uuid.uuid4())
        target = await _target(header, login)
        await _create_upload_thread(thread_id, header, target, login, email, user_id)
        code = issue_upload_ticket(login=login, email=email, user_id=user_id, thread_id=thread_id)
    return SessionReservation(
        thread_id=thread_id,
        url=dashboard_thread_url(thread_id) if thread_id else None,
        upload_url=f"{dashboard_api_base_url()}/dashboard/api/threads/uploads",
        upload_token=code,
        expires_in_seconds=UPLOAD_TICKET_TTL_SECONDS,
    )


class _ReservedThread(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_login: str
    owner_user_id: str | None = None
    uploaded_session_type: SessionType = "claude"
    repo_owner: str | None = None
    repo_name: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    session_upload_pending: bool = False

    @classmethod
    def claimed(cls, metadata: Mapping[str, object], ticket: UploadTicket) -> Self:
        """The reservation ``ticket`` may fill, or the HTTP error saying why it may not."""
        try:
            reserved = cls.model_validate(metadata)
        except ValidationError as exc:
            raise HTTPException(409, "this thread was not reserved for a session upload") from exc
        if reserved.owner_user_id and ticket.user_id:
            if reserved.owner_user_id != ticket.user_id:
                raise HTTPException(409, "this upload code is for another person's thread")
        elif reserved.owner_login.lower() != ticket.sub.lower():
            raise HTTPException(409, "this upload code is for another person's thread")
        return reserved

    def seed_messages(self, thread_id: str, session: LocalSession) -> list[BaseMessage]:
        messages: list[BaseMessage] = [
            *session.messages,
            *_upload_note(self.target, self.uploaded_session_type),
        ]
        # Stable ids let a retry after a partial seed replace its messages rather than append them.
        for index, message in enumerate(messages):
            message.id = message.id or str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"session-upload:{thread_id}:{index}")
            )
        return messages

    def seeded_metadata(self, session: LocalSession, ticket: UploadTicket) -> dict[str, object]:
        update: dict[str, object] = {
            SESSION_UPLOAD_PENDING_KEY: False,
            "updated_at_ms": _now_ms(),
            "owner_login": ticket.sub.strip(),
        }
        if session.title:
            update["title"] = session.title
        return update

    @property
    def target(self) -> _Target | None:
        if self.repo_owner is None or self.repo_name is None or self.branch_name is None:
            return None
        return _Target(
            owner=self.repo_owner,
            name=self.repo_name,
            branch=self.branch_name,
            pr_url=self.pr_url,
            pr_number=self.pr_number,
        )


async def upload_session(stream: UploadStream, ticket: UploadTicket) -> dict[str, Any] | None:
    """Seed the ticket's thread with the session's history once; ``None`` when it already was.

    An import ticket brings any number of sessions, each into a thread keyed by its session id,
    so a repost is a no-op. No run starts until the person sends one.
    """
    client = langgraph_client()
    session: LocalSession | None = None
    thread_id = ticket.thread_id
    if thread_id is None:
        header = SessionUploadHeader.model_validate(ticket.import_header)
        session = await _read_session(stream, header.type)
        owner = ticket.user_id or ticket.sub.lower()
        thread_id = header.import_thread_id(owner, session.key)
        title, metadata = await header.thread_metadata(
            None, ticket.sub, ticket.email, ticket.user_id
        )
        await create_thread(
            client, thread_id, title=title, metadata=metadata, if_exists="do_nothing"
        )
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "the reserved thread no longer exists") from exc
    reserved = _ReservedThread.claimed(thread_metadata(thread), ticket)
    if not reserved.session_upload_pending:
        return None
    if session is None:
        session = await _read_session(stream, reserved.uploaded_session_type)
    messages = reserved.seed_messages(thread_id, session)
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
    update = reserved.seeded_metadata(session, ticket)
    await client.threads.update(thread_id=thread_id, metadata=update)
    await mirror_thread_metadata(thread_id, update)
    # Linked only once the thread holds the session, so an abandoned reservation stays off the PR.
    if reserved.pr_number is not None and reserved.target is not None:
        await _link_pull_request(reserved.target, reserved.pr_number, thread_id)
    return await _thread_summary(await client.threads.get(thread_id))


async def _read_session(stream: UploadStream, session_type: SessionType) -> LocalSession:
    transcript = _TRANSCRIPTS[session_type]()
    async for line in stream.lines():
        transcript.add(line)
    try:
        session = transcript.session()
    except TranscriptError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not session.messages:
        raise HTTPException(422, "the transcript has no messages")
    return session


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
