import logging
from typing import Literal

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, ValidationError

from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.prompts import prompt
from openswe.thread_ids import review_scout_thread_id
from openswe.threads import index, succession
from openswe.threads.creation import ensure_titled_thread
from openswe.threads.status import thread_run_error
from openswe.walkthrough.record import WalkthroughView

logger = logging.getLogger(__name__)

ASSISTANT_ID = "review-scout"
_ACTIVE_STATUSES = frozenset({"running", "pending"})
_FAILED_STATUSES = frozenset({"error", "timeout"})
_SENDER_ID = "system:review-scout"
_HEAD_METADATA_KEY = "scout_head_sha"


class _ScoutRun(BaseModel):
    run_id: str
    status: str = ""
    metadata: dict[str, object] = {}

    @property
    def head_sha(self) -> str | None:
        head = self.metadata.get(_HEAD_METADATA_KEY)
        return head if isinstance(head, str) else None


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


class ScoutStatus(BaseModel):
    """The latest scout run, read against the head the review page shows.

    ``outdated`` is a run still working on an older head, which will not plan this one;
    ``finished`` is a run on this head that ended without failing.
    """

    state: Literal["idle", "running", "outdated", "failed", "finished"]
    head_sha: str | None = None
    error: str | None = None


class ReviewScoutTarget(BaseModel):
    """The pull request head a scout run describes."""

    owner: str
    repo: str
    pr_number: int
    pr_title: str
    base_sha: str
    head_sha: str
    workspace_slug: str | None

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
                if run.head_sha == self.head_sha:
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

    async def status(self) -> ScoutStatus:
        """Where the latest scout run stands for this head."""
        client = dispatch_client()
        thread_id = await self.current_thread_id()
        try:
            runs = await client.runs.list(thread_id, limit=1)
        except NotFoundError:
            return ScoutStatus(state="idle")
        if not runs:
            return ScoutStatus(state="idle")
        run = _ScoutRun.model_validate(runs[0])
        if run.status in _ACTIVE_STATUSES:
            state = "running" if run.head_sha == self.head_sha else "outdated"
            return ScoutStatus(state=state, head_sha=run.head_sha)
        if run.head_sha != self.head_sha:
            return ScoutStatus(state="idle", head_sha=run.head_sha)
        if run.status not in _FAILED_STATUSES:
            return ScoutStatus(state="finished", head_sha=run.head_sha)
        error = (
            "The review scout run timed out."
            if run.status == "timeout"
            else await thread_run_error(thread_id) or "The review scout run ended with an error."
        )
        return ScoutStatus(state="failed", head_sha=run.head_sha, error=error)

    async def start(self) -> str:
        """The scout run for this head, started when none is already running.

        A newer head interrupts a scout still working on an older one, so only
        the latest head's walkthrough is ever written.
        """
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
            "workspace": self.workspace_slug,
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
            thread_title=None,
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            metadata={_HEAD_METADATA_KEY: self.head_sha},
        )
        logger.info("Started review scout", extra=self.log_extra)
        return _ScoutRun.model_validate(run).run_id
