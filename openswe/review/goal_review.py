"""Goal mode reviews: an implementing agent's draft, reviewed with the findings sent back to it.

In Goal mode the person hears nothing until the work is ready, so the review an
implementing agent asks for on its draft posts nothing to GitHub or Slack. Its
findings go to that agent's thread to address; once a round leaves none open,
the PR's ``Open SWE Review`` check passes, marking the draft reviewed.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Final, Self

from pydantic import BaseModel, JsonValue, ValidationError

from openswe.github.checks import CheckConclusion
from openswe.input_messages import InputMessageContext, SystemIdentity
from openswe.prompts import prompt
from openswe.review.findings import (
    Finding,
    Severity,
    filter_findings_for_publish,
    list_findings,
    set_reviewer_thread_metadata,
)
from openswe.threads.store import THREADS
from openswe.utils.dashboard_links import dashboard_review_url

logger = logging.getLogger(__name__)

GOAL_REVIEW_KEY: Final = "goal_review"

_SENDER: Final[SystemIdentity] = {
    "id": "system:open-swe-review",
    "display_name": "Open SWE Review",
    "platform": "open-swe",
}
_CONTEXT: Final[InputMessageContext] = {
    "sender_id": _SENDER["id"],
    "surface": "automation",
    "kind": "system",
}


class GoalReview(BaseModel):
    """A Goal mode review, kept on its reviewer thread until the person marks the PR
    ready and its current head has passed: whose draft it is, and how far it got."""

    # The implementing thread that asked for it, where its findings go.
    thread_id: str
    # The head a round last left no open findings on.
    passed_sha: str | None = None
    # The person marked the PR ready before its head passed: once it does, the agent
    # puts it up for human review.
    handoff: bool = False

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        """The Goal mode review a reviewer thread's metadata records, if it is one."""
        raw = metadata.get(GOAL_REVIEW_KEY)
        if raw is None:
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Ignored an unreadable Goal mode review", exc_info=True)
            return None

    async def ready(
        self, *, reviewer_thread_id: str, owner: str, repo: str, pr_number: int, head_sha: str
    ) -> bool:
        """The person marked the PR ready: hand it to the agent to put up for human review
        if its head passed, the review over; otherwise review it again first. Whether
        the review is over."""
        if self.passed_sha == head_sha:
            await self._end(reviewer_thread_id)
            await self.undrafted(owner=owner, repo=repo, pr_number=pr_number)
            return True
        await self._keep(reviewer_thread_id, self.model_copy(update={"handoff": True}))
        return False

    async def publish(
        self,
        *,
        reviewer_thread_id: str,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        token: str,
        severity_threshold: Severity,
    ) -> dict[str, JsonValue]:
        """End a review round: its open findings go to the implementing thread, nothing to
        GitHub or Slack; a round that leaves none open passes the review check."""
        from openswe.github.pull_requests import PullRequest
        from openswe.review.publish import clear_review_started_comment, settle_review_check_run

        findings = await list_findings(reviewer_thread_id)
        open_findings = filter_findings_for_publish(
            [finding for finding in findings if finding.get("in_diff", True)],
            severity_threshold=severity_threshold,
        )
        if open_findings:
            conclusion: CheckConclusion = "neutral"
            title = f"{len(open_findings)} open findings sent to the implementing agent"
        else:
            conclusion, title = "success", "Open SWE reviewed"
            await PullRequest(owner=owner, repo=repo, number=pr_number).link_review(
                reviewer_thread_id=reviewer_thread_id,
                github_review_id=None,
                head_sha=head_sha,
                finding_count=0,
            )
        await set_reviewer_thread_metadata(reviewer_thread_id, last_reviewed_sha=head_sha)
        handed_off = not open_findings and self.handoff
        if handed_off:
            await self._end(reviewer_thread_id)
        elif not open_findings:
            await self._keep(reviewer_thread_id, self.model_copy(update={"passed_sha": head_sha}))
        await clear_review_started_comment(
            thread_id=reviewer_thread_id, owner=owner, repo=repo, token=token
        )
        await settle_review_check_run(
            thread_id=reviewer_thread_id,
            owner=owner,
            repo=repo,
            token=token,
            conclusion=conclusion,
            title=title,
            summary=title,
        )
        if handed_off:
            await self.undrafted(owner=owner, repo=repo, pr_number=pr_number)
        else:
            await self.report(
                pr_url=f"https://github.com/{owner}/{repo}/pull/{pr_number}",
                head_sha=head_sha,
                findings=open_findings,
                review_url=dashboard_review_url(owner, repo, pr_number),
            )
        return {
            "success": True,
            "goal_review": True,
            "sent_to_thread": self.thread_id,
            "open_count": len(open_findings),
        }

    async def report(
        self, *, pr_url: str, head_sha: str, findings: Sequence[Finding], review_url: str | None
    ) -> None:
        """Send the round's open findings, or that none are left, to the implementing thread."""
        await self._tell(
            prompt(
                "reviewer/goal-findings",
                pr_url=pr_url,
                head_sha=head_sha,
                review_url=review_url,
                findings=[
                    {
                        "id": finding["id"],
                        "severity": finding["severity"],
                        "title": finding["title"],
                        "location": finding["file"]
                        + (f":{finding['start_line']}" if finding.get("start_line") else ""),
                        "description": finding["description"],
                        "suggestion": finding.get("suggestion"),
                    }
                    for finding in findings
                ],
            )
        )

    async def undrafted(self, *, owner: str, repo: str, pr_number: int) -> None:
        """The person marked the reviewed draft ready: the agent puts it up for review."""
        await self._tell(
            prompt(
                "reviewer/goal-undrafted",
                pr_url=f"https://github.com/{owner}/{repo}/pull/{pr_number}",
                repository=f"{owner}/{repo}",
                review_url=dashboard_review_url(owner, repo, pr_number),
            )
        )

    async def _keep(self, reviewer_thread_id: str, review: Self) -> None:
        await set_reviewer_thread_metadata(
            reviewer_thread_id, extra={GOAL_REVIEW_KEY: review.model_dump(mode="json")}
        )

    async def _end(self, reviewer_thread_id: str) -> None:
        """The PR is ready and reviewed: later reviews of it are public again."""
        await set_reviewer_thread_metadata(reviewer_thread_id, extra={GOAL_REVIEW_KEY: None})

    async def _tell(self, text: str) -> None:
        from openswe.background_tasks import completion_configurable
        from openswe.dispatch import dispatch_agent_run
        from openswe.threads.goal import ThreadGoal

        await ThreadGoal(self.thread_id).review_returned()
        thread = await THREADS.get(self.thread_id)
        metadata = {} if thread is None else thread.metadata
        configurable = await completion_configurable(
            self.thread_id, metadata, background_task_completion=False
        )
        await dispatch_agent_run(
            self.thread_id,
            text,
            configurable,
            source=str(configurable.get("source") or "dashboard"),
            thread_title=None,
            context=_CONTEXT,
            systems=[_SENDER],
            metadata={},
            multitask_strategy="enqueue",
        )
