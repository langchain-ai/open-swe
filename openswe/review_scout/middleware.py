"""Plans a pull request's shared walkthrough inside the coding agent, when someone asks for one.

The run places every changed line in a chunk or in Other, and records a summary of
the human input behind the change. It runs on its own thread per PR. On a new head
it only places the lines the carried-over plan does not already cover, and when
there are none it finishes without a model call.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NotRequired

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
    hook_config,
)
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from openswe.github.pull_request_key import PullRequestKey
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.review.author_guidance import SteeringHistory
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import get_cached_sandbox_backend
from openswe.sandboxes.repo_prep import prepare_review_repo
from openswe.tools.plan_walkthrough import (
    walkthrough_describe_other,
    walkthrough_move_to_other,
    walkthrough_plan_chunk,
    walkthrough_split_hunks,
)
from openswe.tools.record_human_input import record_human_input
from openswe.ui_invalidations import Topic
from openswe.walkthrough.checkout import CheckoutError, PinnedCheckout
from openswe.walkthrough.planner import PlannerUnavailableError, PlanWorkspace
from openswe.walkthrough.record import PlanMovedError, Walkthrough

logger = logging.getLogger(__name__)

REVIEW_SCOUT_TOOLS = (
    walkthrough_plan_chunk,
    walkthrough_split_hunks,
    walkthrough_move_to_other,
    walkthrough_describe_other,
    record_human_input,
)
_CLOSING_TITLE_TAG_RE = re.compile(r"</\s*pr_title\s*>", re.IGNORECASE)
_HUMAN_INPUT_TOOL = record_human_input.__name__


class ReviewScoutState(AgentState):
    # Checkpointed, so a run resumed after its before-agent step still has the rules.
    review_scout_rules: NotRequired[Annotated[str, OmitFromOutput]]
    scout_nothing_to_plan: NotRequired[Annotated[bool, OmitFromOutput]]
    has_human_input: NotRequired[Annotated[bool, OmitFromOutput]]
    human_input_summary: NotRequired[Annotated[str, OmitFromOutput]]


async def check_out_pull_request(backend: SandboxBackendProtocol, cfg: RunConfig) -> PinnedCheckout:
    """Check the PR's head out in ``backend`` and pin its base and head for planning."""
    if cfg.repo is None or cfg.pr_number is None or not cfg.base_sha or not cfg.head_sha:
        raise RuntimeError("review scout run is missing its pull request")
    checkout = await PinnedCheckout.locate(backend, cfg.repo.name)
    ready = await prepare_review_repo(
        backend,
        work_dir=checkout.repo_dir.rpartition("/")[0],
        repo_owner=cfg.repo.owner,
        repo_name=cfg.repo.name,
        head_sha=cfg.head_sha,
        pr_number=cfg.pr_number,
        base_sha=cfg.base_sha,
    )
    if not ready:
        raise RuntimeError("review scout could not check out the pull request")
    await checkout.pin(
        full_name=cfg.repo.full_name,
        pr_number=cfg.pr_number,
        base_sha=cfg.base_sha,
        head_sha=cfg.head_sha,
    )
    return checkout


class ReviewScoutMiddleware(OpenSWEMiddleware[ReviewScoutState]):
    """Gives a scout run its plan's rules, and sends whatever it left unplanned to Other."""

    state_schema = ReviewScoutState

    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        super().__init__()
        self._thread_id = thread_id
        self._cfg = RunConfig.from_config(config)

    @property
    def _log_extra(self) -> dict[str, object]:
        return {
            "pr_repo_full_name": self._cfg.repo.full_name if self._cfg.repo else None,
            "pr_number": self._cfg.pr_number,
            "scout_head_sha": self._cfg.head_sha,
        }

    async def _workspace(self) -> PlanWorkspace:
        repo, number = self._cfg.repo, self._cfg.pr_number
        if repo is None or number is None:
            raise PlannerUnavailableError("review scout run is missing its pull request")
        return await PlanWorkspace.locate(
            get_cached_sandbox_backend(self._thread_id),
            owner=repo.owner,
            repo=repo.name,
            number=number,
        )

    async def abefore_agent(
        self,
        state: ReviewScoutState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        cfg = self._cfg
        checkout = await check_out_pull_request(get_cached_sandbox_backend(self._thread_id), cfg)
        try:
            workspace = await self._workspace()
        except PlanMovedError:
            logger.info("Review scout head is stale; nothing to plan", extra=self._log_extra)
            workspace = None
        if workspace is None or workspace.complete or cfg.repo is None or cfg.pr_number is None:
            return {"review_scout_rules": "", "scout_nothing_to_plan": True}
        rules = prompt(
            "review-scout/main",
            pr_number=cfg.pr_number,
            repo_full_name=cfg.repo.full_name,
            pr_title=_CLOSING_TITLE_TAG_RE.sub("</pr_title_>", cfg.pr_title or ""),
            repo_dir=checkout.repo_dir,
            carried=bool(workspace.plan.chunks),
            plan=workspace.status().model_dump(),
        )
        history = await SteeringHistory.load(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        if history is not None:
            human_input = prompt("review-scout/human-input", messages=history.messages_block())
            rules = f"{rules}\n\n{human_input}"
        return {
            "review_scout_rules": rules,
            "scout_nothing_to_plan": False,
            "has_human_input": history is not None,
            "human_input_summary": "",
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: ReviewScoutState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return {"jump_to": "end"} if state.get("scout_nothing_to_plan") else None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        rules = request.state.get("review_scout_rules")
        if isinstance(rules, str) and rules:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{existing}\n\n{rules}" if existing else rules
            request = request.override(system_message=SystemMessage(content=content))
        # Without messages from the people who asked for the PR, the model would summarize its description instead.
        if not request.state.get("has_human_input"):
            request = request.override(
                tools=[t for t in request.tools if getattr(t, "name", None) != _HUMAN_INPUT_TOOL]
            )
        return await handler(request)

    async def aafter_agent(self, state: ReviewScoutState, runtime: Runtime) -> None:  # noqa: ARG002
        cfg = self._cfg
        if cfg.repo is None or cfg.pr_number is None or not cfg.head_sha:
            return
        try:
            workspace = await self._workspace()
            settled = await workspace.settle()
        except PlanMovedError:
            logger.info(
                "Review scout head is stale; leaving the plan to the newer head",
                extra=self._log_extra,
            )
            return
        except CheckoutError, PlannerUnavailableError:
            logger.exception("Review scout could not finish its plan", extra=self._log_extra)
            return
        if summary := state.get("human_input_summary", ""):
            await Walkthrough.set_human_input(workspace.pull_request.id, summary)
        await Topic.PULL_REQUESTS.invalidate(
            key=PullRequestKey.of(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        )
        logger.info(
            "Review scout finished its plan",
            extra={
                **self._log_extra,
                "scout_chunks": len(workspace.plan.chunks),
                "scout_settled_lines": settled,
            },
        )
