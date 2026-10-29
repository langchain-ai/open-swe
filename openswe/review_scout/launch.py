import logging

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, ValidationError

from openswe.database import postgres
from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.message_queue import REVIEW_WALKTHROUGH_SENDER, QueuedMessage
from openswe.prompts import prompt
from openswe.thread_ids import review_scout_thread_id, reviewer_thread_id
from openswe.threads import index, succession
from openswe.threads.creation import ensure_titled_thread
from openswe.threads.status import thread_run_error
from openswe.utils.thread_ops import queue_system_note_for_thread
from openswe.walkthrough.record import WalkthroughView

logger = logging.getLogger(__name__)

ASSISTANT_ID = "review-scout"
_FAILED_STATUSES = frozenset({"error", "timeout"})
_SENDER_ID = "system:review-scout"
_HEAD_METADATA_KEY = "scout_head_sha"
# Queue ids of the walkthroughs handed to a running reviewer, one per head.
_REVIEWER_NOTE_PREFIX = "review-walkthrough:"


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

    @property
    def plannable(self) -> bool:
        """Whether a scout can plan this head: it needs both ends of the diff and a database."""
        return bool(self.base_sha and self.head_sha) and postgres.configured()

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

    async def walkthrough_for_reviewer(self, reviewer_thread_id: str) -> WalkthroughView | None:
        """This head's walkthrough when it is ready; else its scout starts and hands it over on finishing.

        Walkthroughs queued for the reviewer's earlier runs are dropped: the one this run
        renders, or a stale head's, would only repeat or mislead.
        """
        walkthrough = await self.walkthrough()
        if walkthrough is None:
            await self.start()
        await discard_reviewer_walkthroughs(
            reviewer_thread_id, keep_head=None if walkthrough else self.head_sha
        )
        return walkthrough

    async def hand_to_reviewer(self) -> None:
        """Queue this head's walkthrough for the PR's reviewer while a review is running.

        A review that ends first, or never wanted one, goes without: the walkthrough
        is advisory, and the next review reads it in its run context.
        """
        walkthrough = await self.walkthrough()
        if walkthrough is None:
            return
        thread_id = await index.current_thread_id(
            reviewer_thread_id(self.owner, self.repo, self.pr_number)
        )
        thread = await index.get(thread_id)
        if thread is None or thread.status != "busy":
            return
        note = prompt(
            "reviewer/walkthrough-arrived",
            head_sha=self.head_sha,
            blocks=walkthrough.reviewer_blocks(),
        )
        if await queue_system_note_for_thread(
            thread_id,
            note,
            sender_id=REVIEW_WALKTHROUGH_SENDER,
            queue_id=f"{_REVIEWER_NOTE_PREFIX}{self.head_sha}",
        ):
            logger.info(
                "Handed the walkthrough to the running reviewer",
                extra={**self.log_extra, "agent_thread_id": thread_id},
            )


async def discard_reviewer_walkthroughs(thread_id: str, *, keep_head: str | None = None) -> None:
    """Drop the walkthroughs queued for a reviewer thread, but ``keep_head``'s."""
    if not postgres.configured():
        return
    await QueuedMessage.discard(
        thread_id,
        prefix=_REVIEWER_NOTE_PREFIX,
        keep=f"{_REVIEWER_NOTE_PREFIX}{keep_head}" if keep_head else None,
    )
