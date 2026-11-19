"""Durable results of an app's review work, separate from the app's document schema."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from openswe.github.pull_requests import PullRequest
from openswe.plugins.records import DocumentScope, PluginDocuments
from openswe.threads.store import THREADS

REVIEW_OUTCOMES = "review_outcomes"
OutcomeStatus = Literal["queued", "complete", "blocked", "failed", "skipped"]
PASSED_REASON = "The pull request changes no file this app reviews, so Robin skipped it."
UNREAD_REASON = "GitHub wouldn't list the pull request's files, so Robin couldn't tell whether this app applies. Retry once GitHub answers."


class ReviewOutcome(BaseModel):
    id: str
    head_sha: str
    status: OutcomeStatus
    reason: str = ""
    check: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    collection: str | None = None
    document_key: str | None = None


@dataclass(frozen=True)
class ReviewOutcomes:
    app: UUID
    repository: str
    number: int

    def key(self, run_id: str) -> str:
        return f"pulls/{self.number}/{run_id}"

    async def get(self, run_id: str) -> ReviewOutcome | None:
        found = await PluginDocuments(self.app).get(
            REVIEW_OUTCOMES, DocumentScope(repository=self.repository), self.key(run_id)
        )
        return None if found is None else ReviewOutcome.model_validate(found.document)

    async def put(self, outcome: ReviewOutcome, *, create: bool = False) -> ReviewOutcome:
        documents = PluginDocuments(self.app)
        write = documents.create if create else documents.put
        stored = await write(
            REVIEW_OUTCOMES,
            DocumentScope(repository=self.repository),
            self.key(outcome.id),
            outcome.model_dump(mode="json"),
            by=None,
        )
        if await self.closed():
            await self.delete(outcome.id)
            raise ValueError("The pull request closed, so its review outcome was not kept")
        return ReviewOutcome.model_validate(stored.document)

    async def closed(self) -> bool:
        owner, repo = self.repository.split("/", 1)
        pull = await PullRequest.get(owner, repo, self.number)
        return pull is not None and pull.state in ("closed", "merged")

    async def delete(self, run_id: str) -> None:
        await PluginDocuments(self.app).delete(
            REVIEW_OUTCOMES, DocumentScope(repository=self.repository), self.key(run_id)
        )

    async def queued(self, run_id: str, head_sha: str, check: str) -> None:
        # A fast run can save its result before dispatch returns. Never replace it.
        await self.put(
            ReviewOutcome(id=run_id, head_sha=head_sha, check=check, status="queued"),
            create=True,
        )

    async def passed(self, run_id: str, head_sha: str, *, files_read: bool) -> None:
        """The review left the app out: the pull request changes none of its ``paths``, or,
        without ``files_read``, GitHub wouldn't list its files, which a retry may fix."""
        await self.put(
            ReviewOutcome(
                id=run_id,
                head_sha=head_sha,
                status="skipped" if files_read else "failed",
                reason=PASSED_REASON if files_read else UNREAD_REASON,
            ),
            create=True,
        )

    async def saved(self, run_id: str, head_sha: str, collection: str, key: str) -> None:
        previous = await self.get(run_id)
        await self.put(
            ReviewOutcome(
                id=run_id,
                head_sha=head_sha,
                status="queued",
                check=previous.check if previous else "",
                created_at=previous.created_at if previous else datetime.now(UTC).isoformat(),
                collection=collection,
                document_key=key,
            )
        )

    async def has_result(self, outcome: ReviewOutcome) -> bool:
        if not outcome.collection or not outcome.document_key:
            return False
        saved = await PluginDocuments(self.app).get(
            outcome.collection, DocumentScope(repository=self.repository), outcome.document_key
        )
        return saved is not None

    async def finish(
        self,
        run_id: str,
        head_sha: str,
        status: Literal["complete", "blocked", "failed", "skipped"],
        reason: str,
        *,
        requires_result: bool = True,
    ) -> None:
        previous = await self.get(run_id)
        if (
            status == "complete"
            and requires_result
            and (
                previous is None
                or previous.head_sha != head_sha
                or not await self.has_result(previous)
            )
        ):
            raise ValueError("Save the app's result before marking its review complete")
        if status != "complete" and not reason.strip():
            raise ValueError("A blocked, failed or skipped review needs a reason")
        if status == "complete" and not requires_result and not reason.strip():
            raise ValueError("A read-only review needs a summary of its completed work")
        outcome = previous or ReviewOutcome(id=run_id, head_sha=head_sha, status=status)
        await self.put(outcome.model_copy(update={"status": status, "reason": reason.strip()}))

    async def settled(self, run_id: str, head_sha: str, check: str) -> bool:
        """A stopped run is done only if this app recorded an outcome for this commit."""
        outcome = await self.get(run_id)
        if outcome is not None and outcome.head_sha == head_sha:
            if outcome.status in ("blocked", "skipped"):
                return bool(outcome.reason.strip())
            if outcome.status == "complete":
                read_only = (
                    not outcome.collection and not outcome.document_key and bool(outcome.reason)
                )
                if read_only or await self.has_result(outcome):
                    return True
            if outcome.status == "failed":
                return False
        await self.put(
            ReviewOutcome(
                id=run_id,
                head_sha=head_sha,
                check=check,
                status="failed",
                reason="The review ended without saving an app result or a reason for skipping it.",
                created_at=outcome.created_at if outcome else datetime.now(UTC).isoformat(),
            )
        )
        return False

    @classmethod
    async def completed(cls, thread_id: str, run_id: str | None, status: object) -> None:
        """Settle app work on every review's terminal signal, including cancellation."""
        if not run_id or status not in ("success", "error", "timeout", "interrupted", "canceled"):
            return
        thread = await THREADS.get(thread_id)
        if thread is None or thread.metadata.get("kind") != "reviewer":
            return
        pr = thread.metadata.get("pr")
        if not isinstance(pr, dict):
            return
        owner, repo, number = pr.get("owner"), pr.get("name"), pr.get("number")
        if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
            return
        repository = f"{owner}/{repo}".lower()
        scope = DocumentScope(repository=repository)
        for app, collection in await PluginDocuments.collections_under(scope, f"pulls/{number}/"):
            if collection != REVIEW_OUTCOMES:
                continue
            outcomes = cls(app, repository, number)
            if await outcomes.closed():
                await outcomes.delete(run_id)
                continue
            outcome = await outcomes.get(run_id)
            if outcome is not None:
                await outcomes.settled(run_id, outcome.head_sha, outcome.check)
