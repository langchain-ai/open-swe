"""Versioned approval policies and deterministic shadow decisions."""

import hashlib
import re
import tomllib
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.store import now_iso

CriterionStatus = Literal["pass", "fail", "unknown"]
ApprovalDecision = Literal["would_approve", "needs_human_review", "insufficient_evidence"]
PolicySource = Literal["default", "repository", "settings", "repository_settings"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PolicyRules(StrictModel):
    max_risk_score: int = Field(default=2, ge=1, le=5, strict=True)
    minimum_confidence: Literal["low", "medium", "high"] = "high"
    required_checks: list[str] = Field(default_factory=list, max_length=200)
    human_review_paths: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("required_checks", "human_review_paths")
    @classmethod
    def nonempty_entries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Policy entries cannot be blank")
        return list(dict.fromkeys(value.strip() for value in values))


class PolicyCriterion(StrictModel):
    id: str
    title: str
    requirement: str


class PolicySnapshot(StrictModel):
    source: PolicySource
    version: str
    base_sha: str
    head_sha: str
    content: str
    rules: PolicyRules
    criteria: list[PolicyCriterion]
    settings_revisions: dict[str, str] = Field(default_factory=dict)


class CriterionEvidence(StrictModel):
    id: str = Field(min_length=1, max_length=150)
    status: CriterionStatus
    evidence: str = Field(min_length=1, max_length=1500)


class ApprovalEvidence(StrictModel):
    policy_version: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    review_complete: bool
    criteria: list[CriterionEvidence] = Field(max_length=40)

    @field_validator("criteria")
    @classmethod
    def distinct_criteria(cls, values: list[CriterionEvidence]) -> list[CriterionEvidence]:
        if len({value.id for value in values}) != len(values):
            raise ValueError("Each policy criterion must have one assessment")
        return values


class Gate(StrictModel):
    id: str
    title: str
    status: CriterionStatus
    evidence: str
    requirement: str | None = None
    source: Literal["code", "reviewer"] = "code"


class ApprovalFacts(StrictModel):
    current_head_sha: str | None = None
    current_base_sha: str | None = None
    ready: bool | None = None
    changed_paths: list[str] | None = None
    ci: Gate = Field(
        default_factory=lambda: Gate(
            id="ci", title="Checks", status="unknown", evidence="Check results unavailable."
        )
    )
    change_requests: Gate = Field(
        default_factory=lambda: Gate(
            id="change_requests",
            title="Review state",
            status="unknown",
            evidence="Review state unavailable.",
        )
    )


class ApprovalEvaluation(StrictModel):
    mode: Literal["shadow"] = "shadow"
    evaluator_version: str = "2"
    evaluated_at: str = Field(default_factory=now_iso)
    policy: PolicySnapshot | None = None
    decision: ApprovalDecision
    criteria: list[Gate]


def parse_policy(
    content: str, *, source: PolicySource, base_sha: str, head_sha: str
) -> PolicySnapshot:
    if len(content.encode()) > 24_000:
        raise ValueError("Approval policy exceeds 24 KB")
    body = content.lstrip("\ufeff").replace("\r\n", "\n").strip()
    rules = PolicyRules()
    if body.startswith("+++"):
        match = re.match(r"\A\+\+\+[ \t]*\n(.*?)\n\+\+\+[ \t]*(?:\n|$)", body, re.DOTALL)
        if match is None:
            raise ValueError("Approval policy has unterminated TOML frontmatter")
        rules = PolicyRules.model_validate(tomllib.loads(match[1]))
        body = body[match.end() :]
    elif body.startswith("---"):
        raise ValueError("Approval policy uses TOML frontmatter delimited by +++, not YAML")
    headings = list(re.finditer(r"^## ([^\n]+)$", body, re.MULTILINE))
    criteria: list[PolicyCriterion] = []
    preamble = body[: headings[0].start()] if headings else ""
    general = re.sub(r"^# [^\n]+$", "", preamble, flags=re.MULTILINE).strip()
    if general:
        criteria.append(
            PolicyCriterion(
                id="general-requirements", title="General requirements", requirement=general
            )
        )
    for index, heading in enumerate(headings):
        title = heading[1].strip()
        criterion_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        requirement = body[heading.end() : end].strip()
        if not criterion_id or not requirement:
            raise ValueError("Approval policy headings must have a name and requirement")
        criteria.append(PolicyCriterion(id=criterion_id, title=title, requirement=requirement))
    if not 1 <= len(criteria) <= 20 or len({c.id for c in criteria}) != len(criteria):
        raise ValueError("Approval policy needs 1–20 uniquely named level-two sections")
    return PolicySnapshot(
        source=source,
        version=hashlib.sha256(content.encode()).hexdigest(),
        base_sha=base_sha,
        head_sha=head_sha,
        content=content,
        rules=rules,
        criteria=criteria,
    )


def unavailable_evaluation(reason: str) -> ApprovalEvaluation:
    return ApprovalEvaluation(
        decision="insufficient_evidence",
        criteria=[Gate(id="policy", title="Approval policy", status="unknown", evidence=reason)],
    )


def evaluate_policy(
    *,
    policy: PolicySnapshot,
    evidence: ApprovalEvidence | None,
    facts: ApprovalFacts,
    head_sha: str,
    risk_score: int | None,
    confidence: Literal["low", "medium", "high"],
    limitations: list[str],
    open_findings: int,
) -> ApprovalEvaluation:
    gates: list[Gate] = []

    def add(criterion_id: str, title: str, status: CriterionStatus, detail: str) -> None:
        gates.append(Gate(id=criterion_id, title=title, status=status, evidence=detail))

    current = (
        facts.current_head_sha == head_sha == policy.head_sha
        and facts.current_base_sha == policy.base_sha
    )
    add(
        "revision",
        "Current revision",
        "pass" if current else "unknown",
        "Head and base match the evaluated revision."
        if current
        else "The PR revision changed or could not be verified.",
    )
    add(
        "ready",
        "Open and ready for review",
        "unknown" if facts.ready is None else "pass" if facts.ready else "fail",
        "PR is open and not a draft."
        if facts.ready
        else "PR is closed, a draft, or its state is unavailable.",
    )
    bound = (
        evidence is not None
        and evidence.policy_version == policy.version
        and evidence.base_sha == policy.base_sha
        and evidence.head_sha == head_sha
        and {c.id for c in evidence.criteria} <= {c.id for c in policy.criteria}
    )
    add(
        "policy_binding",
        "Evidence matches policy",
        "pass" if bound else "unknown",
        "Evidence matches the current approval policy version."
        if bound
        else "Read the current policy and assess its criteria.",
    )
    complete = bound and evidence is not None and evidence.review_complete and not limitations
    add(
        "complete",
        "Complete review",
        "pass" if complete else "unknown",
        "Reviewer assessed the whole PR without limitations."
        if complete
        else "Review is incomplete or has limitations: " + "; ".join(limitations),
    )
    add(
        "risk",
        "Risk threshold",
        "unknown"
        if risk_score is None
        else "pass"
        if risk_score <= policy.rules.max_risk_score
        else "fail",
        f"Risk {risk_score if risk_score is not None else 'unknown'}; policy maximum {policy.rules.max_risk_score}.",
    )
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    enough_confidence = (
        confidence_rank[confidence] >= confidence_rank[policy.rules.minimum_confidence]
    )
    add(
        "confidence",
        "Assessment confidence",
        "pass" if enough_confidence else "unknown",
        f"Confidence {confidence}; policy minimum {policy.rules.minimum_confidence}.",
    )
    add(
        "findings",
        "No unresolved findings",
        "pass" if open_findings == 0 else "fail",
        f"{open_findings} unresolved findings across this PR.",
    )
    paths = facts.changed_paths
    protected = (
        []
        if paths is None
        else [
            path
            for path in paths
            if any(fnmatchcase(path, pattern) for pattern in policy.rules.human_review_paths)
        ]
    )
    add(
        "paths",
        "Human review paths",
        "unknown" if paths is None else "fail" if protected else "pass",
        "Changed files unavailable."
        if paths is None
        else "Human review required: " + ", ".join(protected[:20])
        if protected
        else "No paths requiring human review changed.",
    )
    gates.extend([facts.ci, facts.change_requests])
    by_id = {item.id: item for item in evidence.criteria} if bound and evidence else {}
    for criterion in policy.criteria:
        item = by_id.get(criterion.id)
        gates.append(
            Gate(
                id=f"policy:{criterion.id}",
                title=criterion.title,
                status=item.status if item else "unknown",
                evidence=item.evidence if item else "No evidence for this policy criterion.",
                requirement=criterion.requirement,
                source="reviewer",
            )
        )
    decision: ApprovalDecision = "would_approve"
    if any(gate.status == "fail" for gate in gates):
        decision = "needs_human_review"
    elif any(gate.status == "unknown" for gate in gates):
        decision = "insufficient_evidence"
    return ApprovalEvaluation(policy=policy, decision=decision, criteria=gates)
