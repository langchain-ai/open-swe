import asyncio
import logging

from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel

from agent.database import postgres
from agent.dispatch import create_durable_run, dispatch_client
from agent.input_messages import build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import render_prompt
from agent.review.walkthrough import WalkthroughView
from agent.thread_ids import review_scout_thread_id

logger = logging.getLogger(__name__)

ASSISTANT_ID = "review-scout"
# The reviewer waits this long for the walkthrough before reviewing without it;
# the scout keeps running and still stores its result for the review page.
SCOUT_WAIT_SECONDS = 600
_SENDER_ID = "system:review-scout"
_HEAD_METADATA_KEY = "scout_head_sha"


class _ScoutRun(BaseModel):
    run_id: str
    metadata: dict[str, object] = {}


class ReviewScoutTarget(BaseModel):
    """The pull request head a scout run describes."""

    owner: str
    repo: str
    pr_number: int
    pr_title: str
    base_sha: str
    head_sha: str
    workspace_slug: str | None

    @property
    def thread_id(self) -> str:
        return review_scout_thread_id(self.owner, self.repo, self.pr_number)

    @property
    def log_extra(self) -> dict[str, object]:
        return {
            "pr_repo_full_name": f"{self.owner}/{self.repo}",
            "pr_number": self.pr_number,
            "scout_head_sha": self.head_sha,
        }

    async def walkthrough(self) -> WalkthroughView | None:
        return await WalkthroughView.for_head(self.owner, self.repo, self.pr_number, self.head_sha)

    async def _active_run(self) -> str | None:
        """A scout run already working on this head, so a retry joins it instead of restarting it."""
        client = dispatch_client()
        for status in ("running", "pending"):
            try:
                runs = await client.runs.list(self.thread_id, status=status, limit=5)
            except NotFoundError:
                return None
            for raw in runs:
                run = _ScoutRun.model_validate(raw)
                if run.metadata.get(_HEAD_METADATA_KEY) == self.head_sha:
                    return run.run_id
        return None

    async def _start(self) -> str:
        """The scout run for this head, started when none is already running.

        A newer head interrupts a scout still working on an older one, so only
        the latest head's walkthrough is ever written.
        """
        active = await self._active_run()
        if active is not None:
            return active
        configurable = {
            "thread_id": self.thread_id,
            "repo": {"owner": self.owner, "name": self.repo},
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "workspace": self.workspace_slug,
        }
        run = await create_durable_run(
            self.thread_id,
            ASSISTANT_ID,
            input=build_run_input(
                render_prompt("review-scout/kickoff.md", pr_number=self.pr_number),
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {"id": _SENDER_ID, "display_name": "Review scout", "platform": "open-swe"}
                ],
            ),
            source="review-scout",
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            metadata={_HEAD_METADATA_KEY: self.head_sha},
        )
        logger.info("Started review scout", extra=self.log_extra)
        return _ScoutRun.model_validate(run).run_id

    async def await_walkthrough(self) -> WalkthroughView | None:
        """This head's walkthrough, running the scout first when it has none yet.

        ``None`` when the scout cannot run or does not finish within
        ``SCOUT_WAIT_SECONDS``; the reviewer then reviews without it.
        """
        if not postgres.configured() or not self.base_sha or not self.head_sha:
            return None
        existing = await self.walkthrough()
        if existing is not None:
            return existing
        run_id = await self._start()
        try:
            await asyncio.wait_for(
                dispatch_client().runs.join(self.thread_id, run_id), SCOUT_WAIT_SECONDS
            )
        except TimeoutError:
            logger.warning(
                "Review scout did not finish in time for the reviewer", extra=self.log_extra
            )
            return None
        return await self.walkthrough()
