"""Start PR repairs in an accessible coding thread."""

import logging
import uuid
from typing import Literal

from fastapi import HTTPException
from langgraph_sdk.schema import Thread
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.dispatch import dispatch_agent_run
from agent.github.pull_request_status import pull_request_identity
from agent.github.pull_requests import PullRequest
from agent.prompts import render_prompt
from agent.threads.access import _ensure_dashboard_github_token
from agent.threads.runs import (
    _build_dashboard_configurable,
    _create_dashboard_thread_record,
)
from agent.threads.summary import _assert_thread_postable
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

_FIX_THREAD_LINK_SOURCE = "dashboard_pr_fix"


class OpenPullRequestThreadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=1000)


class PullRequestFixContext(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=to_camel), populate_by_name=True
    )

    title: str = Field(max_length=1000)
    head_ref: str | None = Field(max_length=1000)
    head_sha: str | None = Field(max_length=100)
    mergeable: bool | None
    merge_state: str = Field(max_length=100)
    ci: Literal["passing", "failing", "pending", "unknown", "none"]
    failing_checks: list[str] = Field(max_length=1000)
    pending_checks: list[str] = Field(max_length=1000)
    status_available: bool
    updated_at: str | None = Field(max_length=100)
    review_decision: Literal["approved", "changes_requested", "none"] | None


class PullRequestThreadStatus(BaseModel):
    running: bool


class PullRequestThreadRef(BaseModel):
    thread_id: str


class PullRequestFixResult(BaseModel):
    thread_id: str
    already_running: bool | None = None


async def _pr_thread_ids(owner: str, repo: str, number: int) -> list[str]:
    """Thread ids the pull request record links, primary first.

    The record's own backfill covers PRs that predate the tables; a registry
    that cannot be reached falls back to the same legacy metadata scan.
    """
    pull_request = PullRequest(owner=owner, repo=repo, number=number)
    try:
        stored = await PullRequest.load(owner, repo, number)
        return list(await stored.linked_threads())
    except Exception:  # noqa: BLE001
        logger.warning(
            "Pull request registry unavailable; scanning thread metadata instead",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            exc_info=True,
        )
        return list(await pull_request.discover_threads() or [])


async def _find_pr_threads(
    owner: str, repo: str, number: int, login: str, email: str | None
) -> list[Thread]:
    client = langgraph_client()
    candidates: dict[str, Thread] = {}
    for thread_id in await _pr_thread_ids(owner, repo, number):
        if thread_id in candidates:
            continue
        try:
            thread = await client.threads.get(thread_id)
        except Exception:  # noqa: BLE001
            continue
        metadata = thread_metadata(thread)
        if metadata.get("kind") or metadata.get("graph_id") not in (None, "agent"):
            continue
        try:
            _assert_thread_postable(metadata, login, email)
        except HTTPException:
            continue
        candidates[thread_id] = thread
    return sorted(
        candidates.values(),
        key=lambda thread: (thread.get("status") == "busy", str(thread.get("updated_at", ""))),
        reverse=True,
    )


async def _find_or_create_pr_thread(
    owner: str,
    repo: str,
    number: int,
    login: str,
    email: str | None,
    *,
    prompt: str,
    title: str,
) -> str:
    client = langgraph_client()
    url = f"https://github.com/{owner}/{repo}/pull/{number}"
    candidates = await _find_pr_threads(owner, repo, number, login, email)
    if candidates:
        return candidates[0]["thread_id"]
    thread = await _create_dashboard_thread_record(
        str(uuid.uuid4()),
        login=login,
        email=email,
        repo_config={"owner": owner, "name": repo},
        prompt=prompt,
        title=title,
    )
    thread_id = str(thread["thread_id"])
    await client.threads.update(
        thread_id=thread_id,
        metadata={"pr_url": url, "pr_number": number, "source_context": {"pr_number": number}},
    )
    await _link_pr_thread(owner, repo, number, thread_id)
    return thread_id


async def _link_pr_thread(owner: str, repo: str, number: int, thread_id: str) -> None:
    """Register the new thread on the PR record so later lookups find it."""
    try:
        await PullRequest(owner=owner, repo=repo, number=number).link_thread(
            thread_id, source=_FIX_THREAD_LINK_SOURCE
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to link pull request fix thread to its pull request",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            exc_info=True,
        )


async def pull_request_thread_running(
    owner: str, repo: str, number: int, login: str, email: str | None = None
) -> PullRequestThreadStatus:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    await require_repo_access_for_user(login, f"{owner}/{repo}")
    threads = await _find_pr_threads(owner, repo, number, login, email)
    return PullRequestThreadStatus(
        running=any(thread.get("status") == "busy" for thread in threads)
    )


async def open_pull_request_thread(
    owner: str,
    repo: str,
    number: int,
    login: str,
    email: str | None = None,
    *,
    title: str,
) -> PullRequestThreadRef:
    full_name = f"{owner}/{repo}"
    if pull_request_identity({"repo_full_name": full_name, "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    await require_repo_access_for_user(login, full_name)
    await _ensure_dashboard_github_token(login)
    url = f"https://github.com/{full_name}/pull/{number}"
    client = langgraph_client()
    async with agent_thread_pr_state_lock(client, f"fix:{login}:{url}"):
        thread_id = await _find_or_create_pr_thread(
            owner,
            repo,
            number,
            login,
            email,
            prompt=render_prompt("runs/pull-request-thread.md", url=url),
            title=title,
        )
        current = await client.threads.get(thread_id)
        _assert_thread_postable(thread_metadata(current), login, email)
        return PullRequestThreadRef(thread_id=thread_id)


async def fix_pull_request(
    owner: str,
    repo: str,
    number: int,
    login: str,
    email: str | None = None,
    *,
    context: PullRequestFixContext | None = None,
) -> PullRequestFixResult:
    full_name = f"{owner}/{repo}"
    if pull_request_identity({"repo_full_name": full_name, "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    await require_repo_access_for_user(login, full_name)
    await _ensure_dashboard_github_token(login)
    url = f"https://github.com/{full_name}/pull/{number}"
    client = langgraph_client()
    prompt = render_prompt("runs/pull-request-fix.md", url=url)
    if context is not None:
        prompt += "\n\n" + render_prompt(
            "runs/pull-request-fix-context.md",
            snapshot=context.model_dump_json(indent=2),
        )
    async with agent_thread_pr_state_lock(client, f"fix:{login}:{url}"):
        thread_id = await _find_or_create_pr_thread(
            owner,
            repo,
            number,
            login,
            email,
            prompt=prompt,
            title=f"Fix {full_name}#{number}",
        )
        current = await client.threads.get(thread_id)
        _assert_thread_postable(thread_metadata(current), login, email)
        if current.get("status") == "busy":
            return PullRequestFixResult(thread_id=thread_id, already_running=True)
        async with agent_thread_pr_state_lock(client, thread_id):
            await client.threads.update(
                thread_id=thread_id,
                metadata={"resolved": False, "resolved_at_ms": None, "auto_resolved_by_prs": False},
            )
        current = await client.threads.get(thread_id)
        _assert_thread_postable(thread_metadata(current), login, email)
        configurable = await _build_dashboard_configurable(
            thread_id, login, thread_metadata(current)
        )
        await dispatch_agent_run(
            thread_id,
            prompt,
            configurable,
            source="dashboard",
            client=client,
            multitask_strategy="enqueue",
        )
        return PullRequestFixResult(thread_id=thread_id)
