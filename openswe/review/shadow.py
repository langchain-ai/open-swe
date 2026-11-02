"""Shadow reviews: a second reviewer on a sample of pull requests, kept for offline comparison.

When a pull request's first review starts, the workspace's ``review_shadow_*``
settings decide whether it is sampled and pin the model its shadow runs on. A
sampled pull request's shadow then follows each review its reviewer runs there,
first reviews and re-reviews alike, on its own thread with the same prompt and
context but the shadow's model. It never reaches GitHub or Slack: its findings
live on its ``review_shadow`` row, and each ``publish_review`` appends what the
review would have posted, to compare with the pull request's own findings.
"""

import copy
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TypedDict
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.dispatch import dispatch_agent_run, dispatch_client
from openswe.github.pull_requests import PullRequest
from openswe.input_messages import build_run_input
from openswe.review.findings import (
    REVIEWER_SHADOW_THREAD_KIND,
    Finding,
    Severity,
    mark_surfaced,
)
from openswe.review.publish import PublishSelection, ReviewAssessment
from openswe.run_config import RunConfig
from openswe.thread_ids import review_shadow_thread_id
from openswe.threads.creation import ensure_titled_thread
from openswe.users.models import User
from openswe.utils.time import now_iso

logger = logging.getLogger(__name__)

_SENDER_ID = "system:review-shadow"
# Keys of the control run's configurable a shadow run must not inherit.
_CONTROL_ONLY_KEYS = ("slack_thread", "re_review", "last_reviewed_sha")


class ShadowPublication(TypedDict):
    """What one shadow review would have posted, in the reviewer's ranked order."""

    head_sha: str
    reviewed_from_sha: str | None
    severity_threshold: Severity
    finding_ids: list[str]
    assessment: dict[str, object] | None
    published_at: str


class ReviewShadow(Base):
    __tablename__ = "review_shadow"

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    reviewer_thread_id: Mapped[str]
    model_id: Mapped[str]
    reasoning_effort: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    last_reviewed_sha: Mapped[str | None] = mapped_column(default=None)
    findings: Mapped[list[Finding]] = mapped_column(JSONB, default_factory=list)
    publications: Mapped[list[ShadowPublication]] = mapped_column(JSONB, default_factory=list)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @classmethod
    async def follow(cls, control: RunConfig, *, kickoff: str, pr_title: str) -> None:
        """Start the shadow of a control review run, sampling the pull request on its first review.

        Never raises: a shadow must not stand in the way of the review it follows.
        """
        try:
            await cls._follow(control, kickoff=kickoff, pr_title=pr_title)
        except Exception:
            logger.exception(
                "Could not start the shadow review",
                extra={"pr_repo_full_name": control.repo_full_name, "pr_number": control.pr_number},
            )

    @classmethod
    async def _follow(cls, control: RunConfig, *, kickoff: str, pr_title: str) -> None:
        if control.repo is None or control.pr_number is None:
            return
        pull = await PullRequest(
            owner=control.repo.owner, repo=control.repo.name, number=control.pr_number
        ).ensure()
        shadow = await cls._of_pull_request(pull.id)
        if shadow is None:
            if control.re_review:
                return
            shadow = await cls._sample(pull, workspace=control.workspace_slug)
            if shadow is None:
                return
        await shadow.start(control, pull, kickoff=kickoff, pr_title=pr_title)

    @classmethod
    async def _of_pull_request(cls, pull_request_id: UUID) -> ReviewShadow | None:
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.pull_request_id == pull_request_id))

    @classmethod
    async def _sample(cls, pull: PullRequest, *, workspace: str | None) -> ReviewShadow | None:
        """The pull request's new shadow, when the experiment is on and samples it."""
        arm = (await cached_workspace_settings(workspace)).review_shadow
        # uuid7's trailing bits are random, so this samples evenly and stays put on a retry.
        if arm is None or pull.id.int % 100 >= arm.percent:
            return None
        async with postgres.session() as session:
            await session.execute(
                insert(cls)
                .values(
                    id=uuid7(),
                    pull_request_id=pull.id,
                    reviewer_thread_id=review_shadow_thread_id(pull.owner, pull.repo, pull.number),
                    model_id=arm.model_id,
                    reasoning_effort=arm.reasoning_effort,
                )
                .on_conflict_do_nothing()
            )
        logger.info(
            "Sampled pull request for a shadow review",
            extra={
                "pr_repo_full_name": f"{pull.owner}/{pull.repo}",
                "pr_number": pull.number,
                "shadow_model": arm.model_id,
                "shadow_effort": arm.reasoning_effort,
            },
        )
        return await cls._of_pull_request(pull.id)

    async def start(
        self, control: RunConfig, pull: PullRequest, *, kickoff: str, pr_title: str
    ) -> None:
        client = dispatch_client()
        await ensure_titled_thread(
            client,
            self.reviewer_thread_id,
            title=f"Shadow review: #{pull.number} {pr_title}".strip(),
        )
        await client.threads.update(
            thread_id=self.reviewer_thread_id,
            metadata={
                "kind": REVIEWER_SHADOW_THREAD_KIND,
                "pr": {"owner": pull.owner, "name": pull.repo, "number": pull.number},
            },
        )
        configurable = {
            key: value for key, value in control.dump().items() if key not in _CONTROL_ONLY_KEYS
        }
        configurable |= {
            "review_shadow": True,
            "reviewer_model_id": self.model_id,
            "reviewer_reasoning_effort": self.reasoning_effort,
        }
        # A shadow whose first review never published reviews the whole pull request instead.
        if control.re_review and self.last_reviewed_sha:
            configurable |= {"re_review": True, "last_reviewed_sha": self.last_reviewed_sha}
        await dispatch_agent_run(
            self.reviewer_thread_id,
            None,
            configurable,
            source=control.source or "review_shadow",
            thread_title=None,
            input=build_run_input(
                kickoff,
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {"id": _SENDER_ID, "display_name": "Shadow review", "platform": "open-swe"}
                ],
            ),
            assistant_id="reviewer",
            client=client,
        )
        logger.info(
            "Started shadow review",
            extra={
                "pr_repo_full_name": f"{pull.owner}/{pull.repo}",
                "pr_number": pull.number,
                "shadow_model": self.model_id,
                "re_review": bool(configurable.get("re_review")),
            },
        )

    @classmethod
    async def findings_of(cls, thread_id: str) -> list[Finding] | None:
        """The findings of the shadow reviewing on ``thread_id``, or ``None`` when no shadow does."""
        async with postgres.session() as session:
            findings = await session.scalar(
                select(cls.findings).where(cls.reviewer_thread_id == thread_id)
            )
        return None if findings is None else copy.deepcopy(findings)

    @classmethod
    async def mutate_findings(
        cls, thread_id: str, mutator: Callable[[list[Finding]], bool]
    ) -> list[Finding] | None:
        """Apply ``mutator`` to the shadow's findings under its row lock; ``None`` when no shadow reviews here."""
        async with postgres.session() as session:
            shadow = await session.scalar(
                select(cls).where(cls.reviewer_thread_id == thread_id).with_for_update()
            )
            if shadow is None:
                return None
            findings = copy.deepcopy(shadow.findings)
            if mutator(findings):
                shadow.findings = findings
            return copy.deepcopy(findings)

    @classmethod
    async def publish(
        cls,
        thread_id: str,
        *,
        head_sha: str,
        re_review: bool,
        severity_threshold: Severity,
        assessment: ReviewAssessment | None,
    ) -> dict[str, object]:
        """Record what this review would post, by the rules a published review follows."""
        async with postgres.session() as session:
            shadow = await session.scalar(
                select(cls).where(cls.reviewer_thread_id == thread_id).with_for_update()
            )
            if shadow is None:
                return {"success": False, "error": "No shadow review records this thread"}
            findings = copy.deepcopy(shadow.findings)
            selection = PublishSelection(
                findings,
                destination="shadow",
                re_review=re_review,
                head_sha=head_sha,
                severity_threshold=severity_threshold,
            )
            surfaced = [finding for finding, _payload in selection.postable]
            for finding in surfaced:
                mark_surfaced(finding)
            publication: ShadowPublication = {
                "head_sha": head_sha,
                "reviewed_from_sha": shadow.last_reviewed_sha,
                "severity_threshold": severity_threshold,
                "finding_ids": [finding["id"] for finding in surfaced],
                "assessment": assessment.settled(findings).model_dump() if assessment else None,
                "published_at": now_iso(),
            }
            shadow.findings = findings
            shadow.publications = [*shadow.publications, publication]
            shadow.last_reviewed_sha = head_sha
        return {
            "success": True,
            "dry_run": True,
            "review_id": None,
            "surfaced_count": len(surfaced),
            "hidden_count": max(len(selection.open) - len(surfaced), 0),
            "resolved_thread_count": 0,
        }
