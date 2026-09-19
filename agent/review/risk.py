"""Advisory PR risk assessments and revision-specific human feedback."""

from typing import Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.github.http import github_client, github_request
from agent.github.thread_token import GitHubAuthError
from agent.review.approval import ApprovalEvaluation, ApprovalEvidence
from agent.review.findings import Finding, Severity
from agent.store import TypedStore, now_iso

RiskScore = Literal[1, 2, 3, 4, 5]
ApprovalFeedback = Literal["safe", "needs_review", "unsure"]


class RiskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    score: RiskScore | None = Field(description="1 is lowest risk, 5 highest; null if not assessed")
    confidence: Literal["low", "medium", "high"]
    rationale: str = Field(min_length=1, max_length=1500)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    approval: ApprovalEvidence | None = None


class RiskAssessment(RiskInput):
    id: str
    repo_full_name: str
    pr_number: int
    run_id: str
    rubric_version: str = "1"
    created_at: str = Field(default_factory=now_iso)
    proposed_score: RiskScore | None
    open_findings: int
    github_review_id: int | None = None
    publication_complete: bool = False
    approval_evaluation: ApprovalEvaluation | None = None


class RiskFeedbackSubmission(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    decision: ApprovalFeedback | None = None
    comment: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def require_feedback(self) -> Self:
        if self.decision is None and not self.comment:
            raise ValueError("Provide an explanation or a suggested decision")
        return self


class RiskFeedback(RiskFeedbackSubmission):
    assessment_id: str
    login: str
    updated_at: str = Field(default_factory=now_iso)


class ReactionSummary(BaseModel):
    helpful: int = 0
    unhelpful: int = 0
    viewer_rating: Literal["helpful", "unhelpful", "conflicting"] | None = None
    synced_at: str | None = None


class RiskResponse(BaseModel):
    assessment: RiskAssessment | None
    feedback: RiskFeedback | None = None
    reactions: ReactionSummary = Field(default_factory=ReactionSummary)


ASSESSMENTS = TypedStore(["review_risk_assessments"], RiskAssessment)
FEEDBACK = TypedStore(["review_risk_feedback"], RiskFeedback)
_FINDING_RISK: dict[Severity, RiskScore] = {"low": 2, "medium": 3, "high": 4, "critical": 5}


def assessment_key(owner: str, repo: str, pr_number: int, head_sha: str, run_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"review-risk:{owner.lower()}/{repo.lower()}:{pr_number}:{head_sha}:{run_id}",
        )
    )


async def find_published_assessment(record: RiskAssessment, token: str) -> int | None:
    marker = f"<!-- open-swe-review-risk id={record.id} -->"
    url = f"https://api.github.com/repos/{record.repo_full_name}/pulls/{record.pr_number}/reviews"
    async with github_client(token=token) as client:
        for page in range(1, 21):
            response = await github_request(
                client, "GET", url, params={"per_page": 100, "page": page}
            )
            if response.status_code == 401:
                raise GitHubAuthError("GitHub returned 401 while recovering a risk assessment")
            response.raise_for_status()
            reviews: object = response.json()
            if not isinstance(reviews, list):
                raise ValueError("Cannot determine whether the risk assessment was published")
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                body = review.get("body")
                review_id = review.get("id")
                if (
                    isinstance(body, str)
                    and marker in body
                    and isinstance(review_id, int)
                    and review.get("commit_id") == record.head_sha
                ):
                    return review_id
            if len(reviews) < 100:
                return None
    raise ValueError(
        "Too many reviews to verify risk assessment publication; retry requires reconciliation"
    )


async def prepare_assessment(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    run_id: str,
    assessment: RiskInput,
    findings: list[Finding],
) -> RiskAssessment:
    if assessment.head_sha != head_sha:
        raise ValueError("Risk assessment commit differs from the current review commit.")
    repo_full_name = f"{owner}/{repo}".lower()
    assessment_id = assessment_key(owner, repo, pr_number, head_sha, run_id)
    existing = await ASSESSMENTS.get(assessment_id)
    if existing is not None and existing.github_review_id is not None:
        return existing
    open_findings = [finding for finding in findings if finding.get("status", "open") == "open"]
    score = assessment.score
    if score is not None:
        for finding in open_findings:
            score = max(score, _FINDING_RISK[finding["severity"]])
    record = RiskAssessment(
        **assessment.model_dump(exclude={"score"}),
        score=score,
        proposed_score=assessment.score,
        id=assessment_id,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        run_id=run_id,
        open_findings=len(open_findings),
    )
    return await ASSESSMENTS.put(record.id, record)


async def get_assessment(
    owner: str, repo: str, pr_number: int, assessment_id: str
) -> RiskAssessment | None:
    record = await ASSESSMENTS.get(assessment_id)
    if (
        record is None
        or record.repo_full_name != f"{owner}/{repo}".lower()
        or record.pr_number != pr_number
    ):
        return None
    return record


async def get_feedback(assessment_id: str, login: str) -> RiskFeedback | None:
    return await FEEDBACK.get(f"{assessment_id}:{login.lower()}")


async def save_feedback(
    record: RiskAssessment, *, login: str, submission: RiskFeedbackSubmission
) -> RiskFeedback:
    feedback = RiskFeedback(**submission.model_dump(), assessment_id=record.id, login=login.lower())
    return await FEEDBACK.put(f"{record.id}:{feedback.login}", feedback)


def render_risk_assessment(record: RiskAssessment, ui_url: str | None) -> str:
    score = f"{record.score}/5" if record.score is not None else "Not assessed"
    summary = f"**PR risk: {score}**"
    details = [
        record.rationale,
        f"Confidence: **{record.confidence}** · Reviewed commit: `{record.head_sha[:12]}` · "
        f"Open findings: **{record.open_findings}**",
    ]
    if record.approval_evaluation is not None:
        evaluation = record.approval_evaluation
        label = {
            "would_approve": "Would approve",
            "needs_human_review": "Needs human review",
            "insufficient_evidence": "Insufficient evidence",
        }[evaluation.decision]
        policy = evaluation.policy
        summary = f"**{label}** · PR risk: **{score}** · Shadow mode"
        if policy is not None:
            details.append(
                f"Policy: **{policy.source}** · Version `{policy.version[:12]}` · Base `{policy.base_sha[:12]}`"
            )
        details.append(
            "\n".join(
                f"- **{gate.status.upper()} — {gate.title}:** {gate.evidence}"
                for gate in evaluation.criteria
            )
        )
    if record.score != record.proposed_score:
        details.append("The score was raised to reflect unresolved review findings.")
    if record.limitations:
        details.append("Limitations: " + "; ".join(record.limitations))
    details.append("Risk scale: 1 = lowest risk; 5 = highest risk.")
    parts = [
        summary,
        f"Commit `{record.head_sha[:12]}` · Advisory only; no approval or merge.",
    ]
    parts.append(
        "<details>\n<summary>Why this decision?</summary>\n\n"
        + "\n\n".join(details)
        + "\n\n</details>"
    )
    parts.append("Was this assessment useful? React with 👍 or 👎 on this comment.")
    if ui_url:
        parts.append(
            f"[Add context or suggest a different decision]({ui_url}#review-risk-{record.id})"
        )
    parts.append(f"<!-- open-swe-review-risk id={record.id} -->")
    return "\n\n".join(parts)
