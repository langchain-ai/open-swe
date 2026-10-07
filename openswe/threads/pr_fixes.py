"""Start PR repairs in an accessible coding thread."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, ClassVar, Literal

from fastapi import HTTPException
from langgraph_sdk.schema import Thread
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.dispatch import dispatch_agent_run
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.pull_requests import PullRequest
from openswe.prompts import prompt
from openswe.threads.access import _ensure_dashboard_github_token
from openswe.threads.runs import (
    _build_dashboard_configurable,
    create_dashboard_thread_record,
)
from openswe.threads.summary import _assert_thread_postable
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

_FIX_THREAD_LINK_SOURCE = "dashboard_pr_fix"


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


class PullRequestThreadRun(BaseModel):
    thread_id: str
    already_running: bool = False


class _PullRequestIntentBase(BaseModel):
    dispatches_run: ClassVar[bool]
    # Distinct content per request, so a busy thread gets it queued instead of dropped.
    queues_behind_running: ClassVar[bool] = False

    def prompt(self, url: str) -> str:
        raise NotImplementedError

    def thread_title(self, full_name: str, number: int) -> str:
        raise NotImplementedError


class OpenThreadIntent(_PullRequestIntentBase):
    intent: Literal["open"]
    title: str = Field(min_length=1, max_length=1000)

    dispatches_run: ClassVar[bool] = False

    def prompt(self, url: str) -> str:
        return prompt("runs/pull-request-thread", url=url)

    def thread_title(self, full_name: str, number: int) -> str:
        return self.title


class MessageIntent(_PullRequestIntentBase):
    intent: Literal["message"]
    title: str = Field(min_length=1, max_length=1000)
    message: str = Field(min_length=1, max_length=10_000)

    dispatches_run: ClassVar[bool] = True
    queues_behind_running: ClassVar[bool] = True

    def prompt(self, url: str) -> str:
        return prompt("runs/pull-request-message", url=url, message=self.message)

    def thread_title(self, full_name: str, number: int) -> str:
        return self.title


FixScope = Literal["conflicts", "checks"]

# The snapshot carries only the fields its scope concerns, so a conflict fix is
# never handed failing checks to chase, nor a check fix the merge state.
_SNAPSHOT_EXCLUDES: dict[FixScope, set[str]] = {
    "conflicts": {"ci", "failing_checks", "pending_checks", "review_decision"},
    "checks": {"mergeable", "merge_state", "review_decision"},
}


class FixIntent(_PullRequestIntentBase):
    intent: Literal["fix"]
    scope: FixScope
    context: PullRequestFixContext | None = None

    dispatches_run: ClassVar[bool] = True

    def prompt(self, url: str) -> str:
        text = prompt("runs/pull-request-fix", url=url, scope=self.scope)
        if self.context is None:
            return text
        snapshot = prompt(
            "runs/pull-request-fix-context",
            snapshot=self.context.model_dump_json(indent=2, exclude=_SNAPSHOT_EXCLUDES[self.scope]),
        )
        return f"{text}\n\n{snapshot}"

    def thread_title(self, full_name: str, number: int) -> str:
        return f"Fix {self.scope} on {full_name}#{number}"


class AddressCommentsIntent(_PullRequestIntentBase):
    intent: Literal["address-comments"]

    dispatches_run: ClassVar[bool] = True

    def prompt(self, url: str) -> str:
        return prompt("runs/pull-request-comments", url=url, comment_url="")

    def thread_title(self, full_name: str, number: int) -> str:
        return f"Address comments on {full_name}#{number}"


class AddressCommentIntent(_PullRequestIntentBase):
    intent: Literal["address-comment"]
    comment_url: str = Field(min_length=1, max_length=1000)
    instructions: str = Field(default="", max_length=10_000)

    dispatches_run: ClassVar[bool] = True

    def prompt(self, url: str) -> str:
        if not self.comment_url.startswith(f"{url}#"):
            raise HTTPException(422, "comment does not belong to this pull request")
        text = prompt("runs/pull-request-comments", url=url, comment_url=self.comment_url)
        instructions = self.instructions.strip()
        if not instructions:
            return text
        extra = prompt("runs/pull-request-comment-instructions", instructions=instructions)
        return f"{text}\n\n{extra}"

    def thread_title(self, full_name: str, number: int) -> str:
        return f"Address comment on {full_name}#{number}"


class LineComment(BaseModel):
    """A comment on diff lines that lives only in the agent's prompt, not on GitHub."""

    kind: Literal["line"]
    path: str = Field(min_length=1, max_length=1000)
    line: int = Field(ge=1)
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    start_line: int | None = Field(default=None, ge=1)
    body: str = Field(min_length=1, max_length=10_000)

    def prompt_values(self) -> dict[str, str]:
        low = min(self.start_line or self.line, self.line)
        return {
            "path": self.path,
            "lines": f"line {self.line}" if low == self.line else f"lines {low}-{self.line}",
            "side": "old" if self.side == "LEFT" else "new",
            "body": self.body.strip(),
        }


class ThreadComment(BaseModel):
    kind: Literal["thread"]
    comment_url: str = Field(min_length=1, max_length=1000)
    instructions: str = Field(default="", max_length=10_000)


class CommentBatchIntent(_PullRequestIntentBase):
    intent: Literal["comments"]
    comments: list[Annotated[LineComment | ThreadComment, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=100
    )

    dispatches_run: ClassVar[bool] = True
    queues_behind_running: ClassVar[bool] = True

    def prompt(self, url: str) -> str:
        threads = [c for c in self.comments if isinstance(c, ThreadComment)]
        if any(not thread.comment_url.startswith(f"{url}#") for thread in threads):
            raise HTTPException(422, "comment does not belong to this pull request")
        return prompt(
            "runs/pull-request-comment-batch",
            url=url,
            threads=[
                {"comment_url": t.comment_url, "instructions": t.instructions.strip()}
                for t in threads
            ],
            lines=[c.prompt_values() for c in self.comments if isinstance(c, LineComment)],
        )

    def thread_title(self, full_name: str, number: int) -> str:
        return f"Address comments on {full_name}#{number}"


PullRequestThreadIntent = Annotated[
    OpenThreadIntent
    | MessageIntent
    | FixIntent
    | AddressCommentsIntent
    | AddressCommentIntent
    | CommentBatchIntent,
    Field(discriminator="intent"),
]


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


async def find_pr_threads(
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


def _pr_thread_lock_key(login: str, url: str) -> str:
    """One key for every dispatcher, because they all create-or-find the same thread.

    Separate keys would let two of them read an empty lookup at once and dispatch
    competing agents onto the same branch; the per-thread lock taken afterwards
    cannot catch that, since by then the thread ids differ.
    """
    return f"pr-thread:{login}:{url}"


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
    candidates = await find_pr_threads(owner, repo, number, login, email)
    if candidates:
        return candidates[0]["thread_id"]
    thread = await create_dashboard_thread_record(
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
    threads = await find_pr_threads(owner, repo, number, login, email)
    return PullRequestThreadStatus(
        running=any(thread.get("status") == "busy" for thread in threads)
    )


async def start_pull_request_thread(
    owner: str,
    repo: str,
    number: int,
    login: str,
    email: str | None = None,
    *,
    intent: PullRequestThreadIntent,
) -> PullRequestThreadRun:
    full_name = f"{owner}/{repo}"
    if pull_request_identity({"repo_full_name": full_name, "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    await require_repo_access_for_user(login, full_name)
    await _ensure_dashboard_github_token(login)
    url = f"https://github.com/{full_name}/pull/{number}"
    prompt = intent.prompt(url)
    client = langgraph_client()
    async with agent_thread_pr_state_lock(client, _pr_thread_lock_key(login, url)):
        thread_id = await _find_or_create_pr_thread(
            owner,
            repo,
            number,
            login,
            email,
            prompt=prompt,
            title=intent.thread_title(full_name, number),
        )
        current = await client.threads.get(thread_id)
        _assert_thread_postable(thread_metadata(current), login, email)
        if not intent.dispatches_run:
            return PullRequestThreadRun(thread_id=thread_id)
        busy = current.get("status") == "busy"
        if busy and not intent.queues_behind_running:
            return PullRequestThreadRun(thread_id=thread_id, already_running=True)
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
            thread_title=None,
            client=client,
            multitask_strategy="enqueue",
        )
        return PullRequestThreadRun(thread_id=thread_id, already_running=busy)


async def dispatch_pull_request_prompt(
    owner: str,
    repo: str,
    number: int,
    login: str,
    prompt: str,
    *,
    title: str,
    before_dispatch: Callable[[str], Awaitable[None]],
) -> str:
    """Enqueue ``prompt`` on ``login``'s thread for a pull request, creating it if needed; its id.

    ``before_dispatch`` receives the thread id before the run is queued, so the caller
    can record which thread it woke before that thread can act.
    """
    url = f"https://github.com/{owner}/{repo}/pull/{number}"
    client = langgraph_client()
    async with agent_thread_pr_state_lock(client, _pr_thread_lock_key(login, url)):
        thread_id = await _find_or_create_pr_thread(
            owner, repo, number, login, None, prompt=prompt, title=title
        )
        await before_dispatch(thread_id)
        current = await client.threads.get(thread_id)
        configurable = await _build_dashboard_configurable(
            thread_id, login, thread_metadata(current)
        )
        await dispatch_agent_run(
            thread_id,
            prompt,
            configurable,
            source="dashboard",
            thread_title=None,
            client=client,
            multitask_strategy="enqueue",
        )
    return thread_id
