import logging
from datetime import datetime
from typing import ClassVar, Self

import httpx2
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, ValidationError

from openswe.dispatch import create_durable_run, dispatch_client
from openswe.github.http import GitHubError
from openswe.github.pull_request_status import PullRequestClient
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.thread_ids import review_scout_thread_id
from openswe.threads import index, succession
from openswe.threads.creation import ensure_titled_thread
from openswe.threads.status import thread_run_error
from openswe.walkthrough.record import WalkthroughView
from openswe.webhooks.common import is_repo_allowed

logger = logging.getLogger(__name__)

ASSISTANT_ID = "review-scout"
_FAILED_STATUSES = frozenset({"error", "timeout"})
_SENDER_ID = "system:review-scout"
_HEAD_METADATA_KEY = "scout_head_sha"


class RepositoryNotAllowed(Exception):
    """A scout asked for a repository outside Open SWE's allowlist."""


class _ScoutRun(BaseModel):
    run_id: str
    status: str = ""
    metadata: dict[str, object] = {}


_PLAN_CHUNK_TOOL = "walkthrough_plan_chunk"


_RECENT_ACTIONS = 5
_ACTION_TARGET_KEYS = ("file_path", "path", "pattern", "title", "command")
_ACTION_TARGET_CHARS = 80


class _ToolCall(BaseModel):
    id: str = ""
    name: str = ""
    args: dict[str, object] = {}

    def action(self) -> ScoutAction:
        target = next(
            (
                value
                for key in _ACTION_TARGET_KEYS
                if isinstance(value := self.args.get(key), str) and value
            ),
            None,
        )
        return ScoutAction(tool=self.name, target=target[:_ACTION_TARGET_CHARS] if target else None)


class _PlanResult(BaseModel):
    success: bool = False


class _ScoutMessage(BaseModel):
    type: str = ""
    name: str | None = None
    content: str | list[object] = ""
    tool_call_id: str | None = None
    tool_calls: list[_ToolCall] = []

    def planned(self) -> bool:
        if (
            self.type != "tool"
            or self.name != _PLAN_CHUNK_TOOL
            or not isinstance(self.content, str)
        ):
            return False
        try:
            return _PlanResult.model_validate_json(self.content).success
        except ValidationError:
            logger.warning("Unparseable review scout plan result", exc_info=True)
            return False


class _ScoutValues(BaseModel):
    messages: list[_ScoutMessage] = []


class _ScoutState(BaseModel):
    values: _ScoutValues = _ScoutValues()


class ScoutAction(BaseModel):
    tool: str
    target: str | None = None


class ScoutProgress(BaseModel):
    """What a running scout is doing, for the review page's status line."""

    steps: int
    running: bool
    recent: list[ScoutAction]


class ReviewScoutTarget(BaseModel):
    """The pull request head a scout run describes."""

    RETRY_JOB: ClassVar[str] = "review_scout_retry"

    owner: str
    repo: str
    pr_number: int
    pr_title: str
    base_sha: str
    head_sha: str

    @classmethod
    def of_run(cls, cfg: RunConfig) -> Self | None:
        """The head a scout run was started for, or ``None`` when its config names none."""
        if cfg.repo is None or cfg.pr_number is None or not cfg.base_sha or not cfg.head_sha:
            return None
        return cls(
            owner=cfg.repo.owner,
            repo=cfg.repo.name,
            pr_number=cfg.pr_number,
            pr_title=cfg.pr_title or "",
            base_sha=cfg.base_sha,
            head_sha=cfg.head_sha,
        )

    @property
    def _retry_key(self) -> str:
        return f"{self.RETRY_JOB}:{self.owner}/{self.repo}#{self.pr_number}@{self.head_sha}"

    async def retry_at(self, due_at: datetime) -> None:
        """Start this head's scout again at ``due_at``, after GitHub's rate limit clears."""
        await queue.schedule(
            self.RETRY_JOB, self.model_dump(mode="json"), due_at=due_at, key=self._retry_key
        )

    async def cancel_retry(self) -> None:
        """Drop this head's queued retry: a run got past GitHub's rate limit and plans it."""
        await queue.cancel_key(self._retry_key)

    async def delayed_until(self) -> datetime | None:
        """When this head's scout starts again, while it waits out a GitHub rate limit."""
        job = await queue.get(self._retry_key)
        return None if job is None or job.underway else job.due_at

    @classmethod
    async def retry(cls, job: Job) -> None:
        """Start a delayed scout, unless its pull request has moved to a newer head since.

        While GitHub still rate-limits Robin, it queues itself again for the time GitHub names,
        instead of spending the queue's few short backoffs. Other failures raise, so the queue
        retries them with backoff.
        """
        target = cls.model_validate(job.payload)
        try:
            async with PullRequestClient.as_app(
                target.owner, target.repo, target.pr_number
            ) as pull:
                head_sha = await pull.head_sha()
        except httpx2.HTTPStatusError as exc:
            error = exc if isinstance(exc, GitHubError) else GitHubError(exc.response)
            if not error.rate_limited:
                raise
            # Queued again under this job's key, so the replacement outlives this run.
            await target.retry_at(error.resume_at)
            logger.warning(
                "Delayed review scout is still rate-limited",
                extra={**target.log_extra, "scout_retry_at": error.resume_at.isoformat()},
            )
            return
        if head_sha != target.head_sha:
            logger.info("Delayed review scout head is stale", extra=target.log_extra)
            return
        await target.start()

    async def current_thread_id(self) -> str:
        """The scout thread to read, which follows its successor once one exists."""
        return await index.current_thread_id(
            review_scout_thread_id(self.owner, self.repo, self.pr_number)
        )

    @property
    def log_extra(self) -> dict[str, object]:
        return {
            "pr_repo_full_name": f"{self.owner}/{self.repo}",
            "pr_number": self.pr_number,
            "scout_head_sha": self.head_sha,
        }

    async def walkthrough(self) -> WalkthroughView | None:
        return await WalkthroughView.for_head(self.owner, self.repo, self.pr_number, self.head_sha)

    async def active_run(self) -> str | None:
        """A scout run already working on this head, so a retry joins it instead of restarting it."""
        client = dispatch_client()
        thread_id = await self.current_thread_id()
        for status in ("running", "pending"):
            try:
                runs = await client.runs.list(thread_id, status=status, limit=5)
            except NotFoundError:
                return None
            for raw in runs:
                run = _ScoutRun.model_validate(raw)
                if run.metadata.get(_HEAD_METADATA_KEY) == self.head_sha:
                    return run.run_id
        return None

    async def progress(self) -> ScoutProgress:
        """How far the running scout has got, from its latest checkpoint."""
        state = _ScoutState.model_validate(
            await dispatch_client().threads.get_state(await self.current_thread_id())
        )
        messages = state.values.messages
        run_start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
        messages = messages[run_start:]
        calls = [
            call for message in messages if message.type == "ai" for call in message.tool_calls
        ]
        steps = sum(1 for message in messages if message.planned())
        last = messages[-1] if messages else None
        return ScoutProgress(
            steps=steps,
            running=last is not None and last.type == "ai" and bool(last.tool_calls),
            recent=[call.action() for call in calls[-_RECENT_ACTIONS:]],
        )

    async def last_failure(self) -> str | None:
        """The error that ended the latest scout run on this head, when it failed."""
        client = dispatch_client()
        thread_id = await self.current_thread_id()
        try:
            runs = await client.runs.list(thread_id, limit=1)
        except NotFoundError:
            return None
        if not runs:
            return None
        run = _ScoutRun.model_validate(runs[0])
        if (
            run.status not in _FAILED_STATUSES
            or run.metadata.get(_HEAD_METADATA_KEY) != self.head_sha
        ):
            return None
        if run.status == "timeout":
            return "The review scout run timed out."
        return await thread_run_error(thread_id) or "The review scout run ended with an error."

    async def start(self, *, origin: str = "automation") -> str:
        """The scout run for this head, started when none is already running.

        A newer head interrupts a scout still working on an older one, so only
        the latest head's walkthrough is ever written.
        """
        if not is_repo_allowed({"owner": self.owner, "name": self.repo}):
            raise RepositoryNotAllowed(
                f"{self.owner}/{self.repo} is not in the repository allowlist"
            )
        client = dispatch_client()
        thread_id = await succession.succeed(
            review_scout_thread_id(self.owner, self.repo, self.pr_number)
        )
        await ensure_titled_thread(
            client, thread_id, title=f"Walkthrough: {self.pr_title} #{self.pr_number}"
        )
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                "kind": ASSISTANT_ID,
                "pr": {"owner": self.owner, "name": self.repo, "number": self.pr_number},
            },
        )
        active = await self.active_run()
        if active is not None:
            return active
        configurable = {
            "thread_id": thread_id,
            "repo": {"owner": self.owner, "name": self.repo},
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
        }
        run = await create_durable_run(
            thread_id,
            ASSISTANT_ID,
            input=build_run_input(
                prompt("review-scout/kickoff", pr_number=self.pr_number),
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {"id": _SENDER_ID, "display_name": "Review scout", "platform": "open-swe"}
                ],
            ),
            source="review-scout",
            origin=origin,
            thread_title=None,
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            metadata={_HEAD_METADATA_KEY: self.head_sha},
        )
        logger.info("Started review scout", extra=self.log_extra)
        return _ScoutRun.model_validate(run).run_id
