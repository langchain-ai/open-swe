"""Turning a local coding-agent session into an Open SWE thread that continues its work."""

import logging
import uuid
from typing import Any, Literal, Self
from urllib.parse import quote

import httpx2
from fastapi import HTTPException
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent.claude_code.transcript import TranscriptError, parse_claude_transcript
from agent.dashboard.profiles import get_profile
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.http import GITHUB_API_BASE, github_client
from agent.github.pull_requests import PullRequest
from agent.input_messages import SystemIdentity, build_input_messages
from agent.prompts import prompt
from agent.slack.client import parse_github_pr_url
from agent.threads.access import _github_token_for_login
from agent.threads.creation import create_thread
from agent.threads.diffs import _safe_git_ref
from agent.threads.runs import (
    _ASSISTANT_ID,
    _resolve_agent_model_choice,
    _resolve_requested_workspace,
)
from agent.threads.summary import DASHBOARD_SOURCE, _now_ms, _parse_repo, _thread_summary
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    merge_participants,
)

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 32 * 1024 * 1024
_PR_LINK_SOURCE = "session_upload"
_UPLOAD_SENDER: SystemIdentity = {
    "id": "system:session-upload",
    "display_name": "Session upload",
    "platform": "open-swe",
}

type SessionType = Literal["claude"]


class SessionUploadBody(BaseModel):
    """A local session's transcript, verbatim, and where its working directory was pushed."""

    model_config = ConfigDict(extra="forbid")

    type: SessionType
    transcript: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
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


async def _github_get(login: str, path: str) -> httpx2.Response | None:
    """The GitHub response, or ``None`` when GitHub reports the resource missing."""
    async with github_client(token=await _github_token_for_login(login)) as client:
        response = await client.get(f"{GITHUB_API_BASE}{path}")
    if response.status_code == 404:
        return None
    if response.is_error:
        logger.warning(
            "GitHub read for a session upload failed",
            extra={"github_path": path, "github_status": response.status_code},
        )
        raise HTTPException(502, "could not read from GitHub")
    return response


async def _pull_request_target(pr_url: str, login: str) -> _Target:
    ref = parse_github_pr_url(pr_url)
    if ref is None:
        raise HTTPException(422, "pr_url must be a github.com pull request URL")
    full_name = f"{ref.owner}/{ref.repo}"
    await require_repo_access_for_user(login, full_name)
    response = await _github_get(login, f"/repos/{full_name}/pulls/{ref.number}")
    if response is None:
        raise HTTPException(404, "pull request not found")
    try:
        head = _PullRequestPayload.model_validate(response.json()).head
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
    if (
        await _github_get(login, f"/repos/{full_name}/branches/{quote(safe_branch, safe='')}")
        is None
    ):
        raise HTTPException(422, f"branch {safe_branch} is not on {full_name}; push it first")
    return _Target(owner=repo_config["owner"], name=repo_config["name"], branch=safe_branch)


async def _target(body: SessionUploadBody, login: str) -> _Target:
    if body.pr_url is not None:
        return await _pull_request_target(body.pr_url, login)
    if body.repo is not None and body.branch is not None:
        return await _branch_target(body.repo, body.branch, login)
    raise HTTPException(422, "pass repo and branch, or pr_url")


def _upload_note(target: _Target) -> list[HumanMessage]:
    data: dict[str, object] = {"repository": target.full_name, "branch": target.branch}
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


async def upload_session(
    body: SessionUploadBody, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Create a thread seeded with the session's history; no run starts until the person sends one."""
    target = await _target(body, login)
    try:
        session = parse_claude_transcript(body.transcript)
    except TranscriptError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not session.messages:
        raise HTTPException(422, "the transcript has no messages")

    repo_config = {"owner": target.owner, "name": target.name}
    workspace = await _resolve_requested_workspace(None, repo_config, login=login)
    profile = await get_profile(login) or {}
    resolved_model, resolved_effort = await _resolve_agent_model_choice(
        profile, None, None, workspace
    )
    now_ms = _now_ms()
    title = session.title or f"Uploaded session on {target.branch}"
    metadata: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        "origin": DASHBOARD_SOURCE,
        "owner_type": "user",
        "owner_login": login.strip(),
        "visibility": "private" if body.visibility == "private" else "public",
        "thread_category": "interactive",
        "trigger_kind": "user",
        PARTICIPANT_LOGINS_KEY: merge_participants(None, login),
        PARTICIPANT_EMAILS_KEY: merge_participants(None, email),
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
        "uploaded_session_type": body.type,
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
    client = langgraph_client()
    await create_thread(client, thread_id, title=title, metadata=metadata, if_exists="raise")
    messages: list[BaseMessage] = [*session.messages, *_upload_note(target)]
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
        try:
            await client.threads.delete(thread_id)
        finally:
            raise HTTPException(502, "failed to store the session transcript") from exc
    if target.pr_number is not None:
        await _link_pull_request(target, target.pr_number, thread_id)
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
