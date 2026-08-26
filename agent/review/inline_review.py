"""Self-review findings for a PR Open SWE authored, kept off the PR itself.

The record claims review ownership of a PR: while it exists, the webhook
auto-reviewer stands down (``agent.webhooks.common.inline_review_owns_pr``) and
the findings surface in the authoring thread instead of as GitHub review
comments. That keeps a machine reviewing its own machine-written PR out of the
human review surface, where it reads as noise the author never asked for.

Keyed by PR rather than by thread: the webhook gate is a hot path with only the
PR in hand, so it must be a direct ``get``. The dashboard reads by thread,
which is a filtered search.
"""

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

INLINE_REVIEW_NAMESPACE = ["inline_reviews"]

Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
Disposition = Literal["pending", "fixed", "deferred", "dismissed"]
ReviewStatus = Literal["claimed", "reviewing", "complete"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}

MAX_INLINE_FINDINGS = 12

_DISPOSITION_LABELS: dict[str, str] = {
    "pending": "not addressed yet",
    "fixed": "fixed in this PR",
    "deferred": "needs your call",
    "dismissed": "dismissed",
}


def review_key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}#{pr_number}"


class InlineFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    severity: Severity = "medium"
    confidence: Confidence = "medium"
    category: str = ""
    title: str = ""
    description: str = ""
    suggestion: str | None = None
    file: str = ""
    start_line: int | None = None
    end_line: int | None = None
    disposition: Disposition = "pending"
    disposition_note: str = ""
    created_at: str = Field(default_factory=now_iso)

    @property
    def anchor(self) -> str:
        if not self.file:
            return ""
        if self.start_line is None:
            return self.file
        if self.end_line is None or self.end_line == self.start_line:
            return f"{self.file}:{self.start_line}"
        return f"{self.file}:{self.start_line}-{self.end_line}"


class InlineReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    owner: str = ""
    repo: str = ""
    pr_number: int = 0
    pr_url: str = ""
    agent_thread_id: str = ""
    base_sha: str = ""
    head_sha: str = ""
    status: ReviewStatus = "claimed"
    findings: list[InlineFinding] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def finding(self, finding_id: str) -> InlineFinding | None:
        return next((f for f in self.findings if f.id == finding_id), None)

    def sorted_findings(self) -> list[InlineFinding]:
        return sorted(
            self.findings,
            key=lambda f: (SEVERITY_ORDER.get(f.severity, len(SEVERITY_ORDER)), f.created_at),
        )


class InlineReviewStore(TypedStore[InlineReview]):
    def __init__(self) -> None:
        super().__init__(INLINE_REVIEW_NAMESPACE, InlineReview)

    async def save(self, review: InlineReview) -> InlineReview:
        review.updated_at = now_iso()
        return await self.put(review.key, review)

    async def claim(
        self,
        *,
        owner: str,
        repo: str,
        pr_number: int,
        pr_url: str,
        agent_thread_id: str,
        base_sha: str = "",
        head_sha: str = "",
    ) -> InlineReview:
        """Register this PR as inline-reviewed, keeping any findings already filed.

        A claim from a different thread starts the record over: those findings
        belong to another run's PR, and carrying them forward would show the new
        author someone else's review.
        """
        key = review_key(owner, repo, pr_number)
        review = await self.get(key) or InlineReview(key=key)
        if agent_thread_id and review.agent_thread_id not in ("", agent_thread_id):
            review = InlineReview(key=key)
        review.owner = owner.strip().lower()
        review.repo = repo.strip().lower()
        review.pr_number = pr_number
        review.pr_url = pr_url or review.pr_url
        review.agent_thread_id = agent_thread_id or review.agent_thread_id
        review.base_sha = base_sha or review.base_sha
        review.head_sha = head_sha or review.head_sha
        return await self.save(review)

    async def for_thread(self, agent_thread_id: str) -> list[InlineReview]:
        if not agent_thread_id:
            return []
        return await self.search_all(filter={"agent_thread_id": agent_thread_id})


REVIEWS = InlineReviewStore()


def format_findings_markdown(review: InlineReview) -> str:
    """A compact findings block for surfaces without the dashboard panel."""
    findings = review.sorted_findings()
    if not findings:
        return "Self-review found no findings that met the bar."
    lines = [f"**Self-review of PR #{review.pr_number} — {len(findings)} finding(s)**", ""]
    for finding in findings:
        anchor = f" — `{finding.anchor}`" if finding.anchor else ""
        label = _DISPOSITION_LABELS.get(finding.disposition, finding.disposition)
        lines.append(f"- **{finding.severity}**: {finding.title}{anchor} _({label})_")
        if finding.disposition_note:
            lines.append(f"  - {finding.disposition_note}")
    return "\n".join(lines)
