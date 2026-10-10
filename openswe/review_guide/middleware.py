"""Runs a review walkthrough inside the coding agent, on a thread forked into a code channel.

Every run first pins the pull request's base and head as refs in the shared
sandbox, without touching its checkout, attaches the diff to the channel when
the head moved, carries the shared plan and the reader's walk over to that
head, starts the review scout when the plan is missing lines, records a "Looks
good" that landed while the previous turn ran, and tells the agent where the
reader stands. Every model call gets the walkthrough's rules.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    OmitFromOutput,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.review_guide.advance import approve_click
from openswe.review_guide.github import PullRequestHead, fetch_head
from openswe.review_guide.messages import resume, retire
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import Reader, Walk
from openswe.review_scout.launch import ReviewScoutTarget
from openswe.sandboxes.lifecycle import get_cached_sandbox_backend
from openswe.slack.code_channels import repo_context_bar_items, set_context_bar, set_view
from openswe.slack.http import SlackRequestError
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.walkthrough.checkout import PinnedCheckout
from openswe.walkthrough.plan import LineRef
from openswe.walkthrough.planner import PlanWorkspace
from openswe.walkthrough.record import PlanMovedError, Walkthrough

logger = logging.getLogger(__name__)


class ReviewGuideState(AgentState):
    # Checkpointed, so a run resumed after its before-agent step still has the rules.
    review_guide_rules: NotRequired[Annotated[str, OmitFromOutput]]


class ReviewGuideMiddleware(OpenSWEMiddleware[ReviewGuideState]):
    state_schema = ReviewGuideState

    def __init__(self, *, thread_id: str, approve_ts: str) -> None:
        super().__init__()
        self._thread_id = thread_id
        self._approve_ts = approve_ts

    async def abefore_agent(
        self,
        state: ReviewGuideState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        session = await ReviewGuideSession.get(self._thread_id)
        if session is None or session.closed:
            return {"review_guide_rules": ""}
        pr = session.pull_request
        head = await fetch_head(pr.owner, pr.repo, pr.number)
        if head is None:
            raise RuntimeError("review walkthrough could not read the pull request")
        checkout = await PinnedCheckout.locate(get_cached_sandbox_backend(self._thread_id), pr.repo)
        author = session.mode == "author"
        rules = prompt(
            "review-guide/main",
            pr_number=pr.number,
            repo_full_name=pr.repo_full_name,
            repo_dir=checkout.repo_dir,
            author=author,
            draft=head.draft,
            human_input=(await Walkthrough.human_input_for(pr.id) or "") if author else "",
        )
        if await checkout.pin(
            full_name=pr.repo_full_name,
            pr_number=pr.number,
            base_sha=head.base.sha,
            head_sha=head.head.sha,
        ):
            try:
                await set_view(
                    session.slack_channel_id,
                    "diff",
                    content=await checkout.diff(zero=False),
                    base_branch=head.base.ref,
                    head_branch=head.head.ref,
                )
            except SlackRequestError as exc:
                logger.warning(
                    "Could not attach the pull request diff to a review walkthrough channel",
                    extra={"agent_thread_id": self._thread_id, "slack_error": str(exc)},
                )
        # Refreshed every run so channels opened before a link was added still get it.
        try:
            await set_context_bar(
                session.slack_channel_id,
                repo_context_bar_items(
                    {"owner": pr.owner, "name": pr.repo},
                    pr_url=pr.url,
                    dashboard_url=dashboard_thread_url(self._thread_id) or "",
                ),
            )
        except SlackRequestError as exc:
            logger.warning(
                "Could not set a review walkthrough channel's context bar",
                extra={"agent_thread_id": self._thread_id, "slack_error": str(exc)},
            )
        await resume(session)
        try:
            workspace = await PlanWorkspace.open(pr, checkout)
        except PlanMovedError:
            # A push landed after the head was read; the update pauses the walkthrough.
            logger.info(
                "Review walkthrough head moved while its turn started",
                extra={"agent_thread_id": self._thread_id, "pr_number": pr.number},
            )
            return {
                "review_guide_rules": rules,
                "messages": [HumanMessage(content=prompt("review-guide/moved-mid-turn"))],
            }
        if not workspace.complete:
            await self._plan_in_background(session, head)
        walk = session.walk
        moved = walk is not None and walk.head_sha != head.head.sha
        if walk is not None and moved:
            walk, gone = walk.moved_to(head.head.sha, workspace.changes)
            await session.save_walk(walk)
            if gone is not None:
                await retire(
                    session.slack_channel_id,
                    gone.message_ts,
                    gone.message_text,
                    "The pull request changed these lines",
                )
            logger.info(
                "Review walkthrough carried to a newer head",
                extra={
                    "agent_thread_id": self._thread_id,
                    "pr_number": pr.number,
                    "guide_head_sha": head.head.sha,
                },
            )
        if walk is None:
            walk = Walk(head_sha=head.head.sha)
            await session.save_walk(walk)
        approved = (
            await approve_click(session, walk, self._approve_ts) if self._approve_ts else None
        )
        unplanned = [LineRef.of(line) for line in workspace.plan.unplanned(workspace.changes)]
        reader = Reader.of(walk, workspace.plan, await session.seen_lines(), unplanned)
        return {
            "review_guide_rules": rules,
            "messages": [
                HumanMessage(
                    content=prompt(
                        "review-guide/state",
                        moved=moved,
                        head_sha=head.head.sha,
                        clicked=bool(self._approve_ts),
                        approved=approved.title if approved else "",
                        status=reader.status(unplanned).model_dump(),
                    )
                )
            ],
        }

    async def _plan_in_background(self, session: ReviewGuideSession, head: PullRequestHead) -> None:
        """Have the review scout place what the plan is missing, while the guide talks."""
        pr = session.pull_request
        target = ReviewScoutTarget(
            owner=pr.owner,
            repo=pr.repo,
            pr_number=pr.number,
            pr_title=head.title,
            base_sha=head.base.sha,
            head_sha=head.head.sha,
            workspace_slug=session.workspace_slug,
        )
        try:
            await target.start()
        except Exception:
            logger.warning(
                "Could not start the review scout for a review walkthrough",
                exc_info=True,
                extra={"agent_thread_id": self._thread_id, **target.log_extra},
            )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        rules = request.state.get("review_guide_rules")
        if isinstance(rules, str) and rules:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{existing}\n\n{rules}" if existing else rules
            request = request.override(system_message=SystemMessage(content=content))
        return await handler(request)
