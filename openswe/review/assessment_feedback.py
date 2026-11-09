"""Published assessments and each reviewer's explicit feedback."""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Literal, Self

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe import event_claims
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.http import GitHubAppUnavailable, GitHubClient, RepoClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.review.approvals import ApprovalPolicy, approval_mode_for
from openswe.review.findings import list_findings
from openswe.review.publish import ReviewAssessment, render_review_body
from openswe.users.records import UserRecords
from openswe.utils.dashboard_links import dashboard_review_url
from openswe.utils.json_types import JsonObject
from openswe.utils.langsmith import create_langsmith_feedback
from openswe.utils.time import now_iso

logger = logging.getLogger(__name__)

_APPROVAL_CLAIM_SCOPE = "withheld_approval"
_APPROVAL_CLAIM_TTL = timedelta(days=1)


class AssessmentRow(Base):
    __tablename__ = "review_assessment"

    github_review_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class FeedbackSubmission(BaseModel):
    rating: Literal["helpful", "unhelpful"]
    comment: str = Field(default="", max_length=3000)


class AssessmentFeedback(FeedbackSubmission):
    login: str
    updated_at: str


# One record per person and assessment, keyed by the GitHub review id.
_FEEDBACK = UserRecords("review_assessment_feedback")


class PublishedAssessment(ReviewAssessment):
    review_id: int
    owner: str
    repo: str
    pr_number: int
    approved: bool = False
    dry_run: bool = False
    run_id: str | None = None
    policy_sha256: str | None = None

    @classmethod
    async def get(cls, github_review_id: int) -> Self | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(AssessmentRow.value).where(
                    AssessmentRow.github_review_id == github_review_id
                )
            )
        return None if value is None else cls.model_validate(value)

    @classmethod
    async def latest(cls, owner: str, repo: str, pr_number: int) -> Self | None:
        """The pull request's most recently published assessment."""
        value = AssessmentRow.value
        async with postgres.session() as session:
            latest = await session.scalar(
                select(value)
                .where(
                    func.lower(value["owner"].astext) == owner.lower(),
                    func.lower(value["repo"].astext) == repo.lower(),
                    value["pr_number"].astext == str(pr_number),
                )
                .order_by(AssessmentRow.published_at.desc())
                .limit(1)
            )
        return None if latest is None else cls.model_validate(latest)

    def conditional_for_head(self, head_sha: str) -> bool:
        """Whether Open SWE would approve ``head_sha`` subject to human approval of the condition."""
        return (
            not self.dry_run
            and self.head_sha == head_sha
            and self.decision == "would_approve_conditionally"
        )

    @classmethod
    async def withheld(cls, owner: str, repo: str, pr_number: int, head_sha: str) -> Self | None:
        """The latest assessment if it would approve ``head_sha`` but only advised it, as on a draft.

        ``None`` too when the repository no longer approves automatically.
        """
        latest = await cls.latest(owner, repo, pr_number)
        if (
            latest is None
            or latest.dry_run
            or latest.approved
            or latest.head_sha != head_sha
            or latest.decision != "would_approve"
            or await approval_mode_for(owner, repo) != "approve"
        ):
            return None
        return latest

    @staticmethod
    def policy_digest(policy: str | None) -> str | None:
        """A fingerprint of the rendered approval policy an assessment was judged against."""
        return hashlib.sha256(policy.encode()).hexdigest() if policy else None

    async def approve(self, *, reviewer_thread_id: str) -> int | None:
        """Submit the approval this assessment withheld; the approving review's id, if posted.

        ``None`` when the verdict may no longer hold or GitHub refused it; review again then.
        """
        owner, repo, number = self.owner, self.repo, self.pr_number
        log_extra = {"repository": f"{owner}/{repo}", "pr_number": number}
        try:
            async with GitHubClient.as_app(
                owner, repo, permissions={"contents": "read", "pull_requests": "write"}
            ) as github:
                review_id = await self._submit_approval(
                    github.repo(owner, repo), reviewer_thread_id
                )
        except GitHubAppUnavailable:
            logger.warning("No GitHub App to submit Robin's withheld approval", extra=log_extra)
            return None
        if review_id is None:
            return None
        await self.model_copy(update={"review_id": review_id, "approved": True}).save()
        try:
            await PullRequest(owner=owner, repo=repo, number=number).link_review(
                reviewer_thread_id=reviewer_thread_id,
                github_review_id=review_id,
                head_sha=self.head_sha,
                finding_count=0,
            )
        except Exception:
            logger.exception("Failed to link Robin's approval to its pull request", extra=log_extra)
        return review_id

    async def _submit_approval(self, repo: RepoClient, reviewer_thread_id: str) -> int | None:
        """Post the approval if the draft's verdict still holds for the ready PR at its head."""
        number = self.pr_number
        pull = repo.pull_request(number)
        pr = PullRequestPayload.model_validate(await pull.pull())
        if pr.state != "open" or pr.draft or pr.head_sha != self.head_sha:
            return None
        policy = await (await ApprovalPolicy.fetch(repo)).for_pull(repo, number)
        if self.policy_sha256 is None or self.policy_digest(policy.render()) != self.policy_sha256:
            return None
        findings = await list_findings(reviewer_thread_id, strict=True)
        if self.settled(findings).decision != "would_approve":
            return None
        claim_key = f"{repo.full_name}#{number}@{self.head_sha}".lower()
        if not await event_claims.claim(_APPROVAL_CLAIM_SCOPE, claim_key, ttl=_APPROVAL_CLAIM_TTL):
            return None
        body = render_review_body(
            pr_number=number,
            surfaced_count=0,
            ui_url=dashboard_review_url(self.owner, self.repo, number),
            assessment=self,
            approved=True,
        )
        try:
            review = await pull.create_review(
                {"commit_id": self.head_sha, "event": "APPROVE", "body": body}
            )
        except httpx2.HTTPStatusError as exc:
            # A 5xx may still have created the review, so keep the claim then.
            if exc.response.status_code < 500:
                await event_claims.release(_APPROVAL_CLAIM_SCOPE, claim_key)
            logger.warning(
                "GitHub refused the approval Robin withheld on a draft",
                extra={"repository": repo.full_name, "pr_number": number},
                exc_info=True,
            )
            return None
        review_id = review.get("id") if isinstance(review, dict) else None
        if not isinstance(review_id, int):
            logger.error(
                "GitHub returned no id for Robin's approval",
                extra={"repository": repo.full_name, "pr_number": number},
            )
            return None
        return review_id

    @classmethod
    async def for_viewer(
        cls, owner: str, repo: str, pr_number: int, github_review_id: int, login: str
    ) -> Self:
        """The assessment on this pull request, if ``login`` may see the repository."""
        await require_repo_access_for_user(login, f"{owner}/{repo}")
        assessment = await cls.get(github_review_id)
        if (
            assessment is None
            or assessment.owner.lower() != owner.lower()
            or assessment.repo.lower() != repo.lower()
            or assessment.pr_number != pr_number
        ):
            raise HTTPException(404, "Assessment not found")
        return assessment

    async def save(self) -> None:
        value = self.model_dump(mode="json")
        upsert = insert(AssessmentRow).values(github_review_id=self.review_id, value=value)
        async with postgres.session() as session:
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[AssessmentRow.github_review_id], set_={"value": value}
                )
            )

    async def feedback_from(self, login: str) -> AssessmentFeedback | None:
        value = await _FEEDBACK.get(login, str(self.review_id))
        return None if value is None else AssessmentFeedback.model_validate(value)

    async def record_feedback(
        self, login: str, submission: FeedbackSubmission
    ) -> AssessmentFeedback:
        """Save ``login``'s feedback, and mirror it onto the review's trace when there is one."""
        feedback = AssessmentFeedback(
            rating=submission.rating,
            comment=submission.comment.strip(),
            login=login.lower(),
            updated_at=now_iso(),
        )
        await _FEEDBACK.put(login, feedback.model_dump(mode="json"), str(self.review_id))
        if self.run_id:
            try:
                saved = await create_langsmith_feedback(
                    self.run_id,
                    f"review_assessment:{self.review_id}:{feedback.login}",
                    score=1.0 if feedback.rating == "helpful" else 0.0,
                    comment=feedback.comment or None,
                    source_info={
                        "source": "review_assessment",
                        "review_id": self.review_id,
                        "user_login": feedback.login,
                        "owner": self.owner,
                        "repo": self.repo,
                        "pr_number": self.pr_number,
                    },
                )
                if not saved:
                    logger.warning(
                        "Review assessment trace feedback was not saved",
                        extra={"review_id": self.review_id},
                    )
            except Exception:
                logger.exception(
                    "Failed to save review assessment trace feedback",
                    extra={"review_id": self.review_id},
                )
        return feedback
