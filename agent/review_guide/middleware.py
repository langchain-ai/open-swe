"""Runs a review walkthrough inside the coding agent, on a thread forked into a code channel.

Every run first pins the pull request's base and head as refs in the shared
sandbox, without touching its checkout, attaches the diff to the channel when
the head moved, carries the walkthrough over to that head, records a "Looks
good" that landed while the previous turn ran, and tells the agent where the
walkthrough stands. Every model call gets the walkthrough's rules.
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

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.review.walkthrough import Walkthrough
from agent.review_guide import git
from agent.review_guide.advance import approve_click
from agent.review_guide.context import guide_repo_dir
from agent.review_guide.diff import parse
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import resume, retire
from agent.review_guide.sessions import ReviewGuideSession
from agent.review_guide.walk import Walk
from agent.sandboxes.lifecycle import get_cached_sandbox_backend
from agent.slack.code_channels import repo_context_bar_items, set_context_bar, set_view
from agent.slack.http import SlackRequestError
from agent.utils.dashboard_links import dashboard_thread_url

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
        backend = get_cached_sandbox_backend(self._thread_id)
        repo_dir = await guide_repo_dir(backend, pr.repo)
        author = session.mode == "author"
        rules = prompt(
            "review-guide/main",
            pr_number=pr.number,
            repo_full_name=pr.repo_full_name,
            repo_dir=repo_dir,
            author=author,
            draft=head.draft,
            human_input=(await Walkthrough.human_input_for(pr.id) or "") if author else "",
        )
        if await git.built_for(backend, repo_dir) != (head.base.sha, head.head.sha):
            await git.fetch(
                backend,
                repo_dir,
                full_name=pr.repo_full_name,
                pr_number=pr.number,
                base_sha=head.base.sha,
                head_sha=head.head.sha,
            )
            await git.pin(backend, repo_dir, base_sha=head.base.sha, head_sha=head.head.sha)
            try:
                await set_view(
                    session.slack_channel_id,
                    "diff",
                    content=await git.pr_diff(backend, repo_dir, zero=False),
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
        changes = parse(await git.pr_diff(backend, repo_dir))
        walk = session.walk
        moved = walk is not None and walk.head_sha != head.head.sha
        if walk is not None and moved:
            walk, gone = walk.moved_to(head.head.sha, changes)
            await session.save_walk(walk)
            for group in gone:
                if group.status == "shown":
                    await retire(
                        session.slack_channel_id,
                        group.message_ts,
                        group.message_text,
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
            walk = Walk.start(head.head.sha, changes)
        approved = (
            await approve_click(session, walk, self._approve_ts) if self._approve_ts else None
        )
        status = walk.status(walk.unseen(changes, await session.seen_lines()))
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
                        status=status.model_dump(),
                    )
                )
            ],
        }

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
