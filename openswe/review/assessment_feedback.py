"""Published assessments and each reviewer's explicit feedback."""

import logging
from datetime import datetime
from typing import Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.database.store_imports import StoreImport, run_store_import
from openswe.review.publish import ReviewAssessment
from openswe.store import delete_value, now_iso, search_all_entries, search_entries
from openswe.users.records import UnknownUser, UserRecords
from openswe.utils.json_types import JsonObject
from openswe.utils.langsmith import create_langsmith_feedback

logger = logging.getLogger(__name__)

_ASSESSMENTS_NAMESPACE = ["review_assessments"]
_FEEDBACK_NAMESPACE = ["review_assessment_feedback"]


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

    @classmethod
    async def import_store(cls) -> StoreImport:
        """Move feedback, kept per assessment and keyed by login; a login with no user waits."""
        moved = waiting = 0
        for entry in await search_all_entries(_FEEDBACK_NAMESPACE):
            namespace = entry.namespace or []
            if len(namespace) != 2:
                continue
            review_id = namespace[1]
            try:
                if await _FEEDBACK.get(entry.key, review_id) is None:
                    await _FEEDBACK.put(entry.key, entry.value, review_id)
            except UnknownUser:
                logger.warning(
                    "Store review feedback waits for its users row",
                    extra={"github_login": entry.key},
                )
                waiting += 1
                continue
            await delete_value(namespace, entry.key)
            moved += 1
        return StoreImport(moved, waiting)


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

    @classmethod
    async def import_store(cls) -> StoreImport:
        """Drain published assessments from the Store a page at a time; PostgreSQL wins."""
        moved = 0
        while page := await search_entries(_ASSESSMENTS_NAMESPACE):
            for entry in page:
                try:
                    assessment = cls.model_validate(entry.value)
                except ValidationError:
                    logger.warning(
                        "Dropping unreadable review assessment from the Store",
                        extra={"store_key": entry.key},
                        exc_info=True,
                    )
                else:
                    async with postgres.session() as session:
                        await session.execute(
                            insert(AssessmentRow)
                            .values(
                                github_review_id=assessment.review_id,
                                value=assessment.model_dump(mode="json"),
                            )
                            .on_conflict_do_nothing()
                        )
                await delete_value(_ASSESSMENTS_NAMESPACE, entry.key)
            moved += len(page)
        return StoreImport(moved=moved)


async def run_assessment_import() -> None:
    """Drain assessments from the Store in the background, so a backlog never delays startup."""
    try:
        await run_store_import("review_assessments", PublishedAssessment.import_store)
    except Exception:
        logger.exception("Importing review assessments from the LangGraph Store failed")
