import pytest

from agent.review.approval import (
    ApprovalEvaluation,
    ApprovalEvidence,
    ApprovalFacts,
    CriterionEvidence,
    Gate,
    evaluate_policy,
    parse_policy,
)

POLICY = """+++
max_risk_score = 2
required_checks = ["tests"]
human_review_paths = ["auth/**"]
+++
# Approval policy

## Bounded change
The change has limited impact and is well understood.

## Verification
Relevant behavior has been verified.
"""


def evaluation(
    *,
    risk_score: int | None = 2,
    open_findings: int = 0,
    criteria: list[CriterionEvidence] | None = None,
    evidence_version: str | None = None,
    facts: ApprovalFacts | None = None,
) -> ApprovalEvaluation:
    policy = parse_policy(POLICY, source="repository", base_sha="b" * 40, head_sha="a" * 40)
    evidence = ApprovalEvidence(
        policy_version=evidence_version or policy.version,
        base_sha=policy.base_sha,
        head_sha="a" * 40,
        review_complete=True,
        criteria=criteria
        if criteria is not None
        else [
            CriterionEvidence(
                id="bounded-change", status="pass", evidence="Only the parser changes."
            ),
            CriterionEvidence(
                id="verification", status="pass", evidence="Parser regression test passes."
            ),
        ],
    )
    return evaluate_policy(
        policy=policy,
        evidence=evidence,
        facts=facts
        or ApprovalFacts(
            current_head_sha="a" * 40,
            current_base_sha="b" * 40,
            ready=True,
            changed_paths=["parser.py"],
            ci=Gate(id="ci", title="Checks", status="pass", evidence="tests: success"),
            change_requests=Gate(
                id="change_requests",
                title="Review state",
                status="pass",
                evidence="No change requests.",
            ),
        ),
        head_sha="a" * 40,
        risk_score=risk_score,
        confidence="high",
        limitations=[],
        open_findings=open_findings,
    )


def test_complete_low_risk_review_would_approve_without_granting_approval() -> None:
    result = evaluation()
    assert result.decision == "would_approve"
    assert result.mode == "shadow"
    assert all(gate.status == "pass" for gate in result.criteria)
    assert result.policy is not None and result.policy.base_sha == "b" * 40


@pytest.mark.parametrize(("score", "findings"), [(4, 0), (1, 1)])
def test_passing_model_assessment_cannot_override_hard_blocks(score: int, findings: int) -> None:
    result = evaluation(risk_score=score, open_findings=findings)
    assert result.decision == "needs_human_review"


def test_missing_criterion_is_unknown_not_implicitly_satisfied() -> None:
    result = evaluation(
        criteria=[
            CriterionEvidence(id="bounded-change", status="pass", evidence="Small parser change.")
        ]
    )
    assert result.decision == "insufficient_evidence"
    assert next(g for g in result.criteria if g.id == "policy:verification").status == "unknown"


def test_old_policy_evidence_cannot_approve_under_a_new_policy() -> None:
    result = evaluation(evidence_version="previous-policy")
    assert result.decision == "insufficient_evidence"


def test_failed_criterion_routes_to_human_even_when_another_is_unknown() -> None:
    result = evaluation(
        criteria=[
            CriterionEvidence(
                id="bounded-change", status="fail", evidence="Changes an authorization boundary."
            )
        ]
    )
    assert result.decision == "needs_human_review"


@pytest.mark.parametrize(
    "path", ["auth/session.py", "APPROVAL_POLICY.md", "subdir/APPROVAL_POLICY.md"]
)
def test_protected_paths_require_humans_even_with_passing_model_evidence(path: str) -> None:
    result = evaluation(
        facts=ApprovalFacts(
            current_head_sha="a" * 40,
            current_base_sha="b" * 40,
            ready=True,
            changed_paths=[path],
        )
    )
    assert result.decision == "needs_human_review"


def test_live_commit_change_invalidates_approval_evidence() -> None:
    result = evaluation(
        facts=ApprovalFacts(
            current_head_sha="c" * 40,
            current_base_sha="b" * 40,
            ready=True,
            changed_paths=["parser.py"],
        )
    )
    assert result.decision == "insufficient_evidence"


def test_repository_threshold_is_applied_and_content_version_changes() -> None:
    strict = parse_policy(
        POLICY.replace("max_risk_score = 2", "max_risk_score = 1"),
        source="repository",
        base_sha="b" * 40,
        head_sha="a" * 40,
    )
    original = parse_policy(POLICY, source="repository", base_sha="b" * 40, head_sha="a" * 40)
    assert strict.version != original.version
    result = evaluate_policy(
        policy=strict,
        evidence=None,
        facts=ApprovalFacts(),
        head_sha="a" * 40,
        risk_score=2,
        confidence="high",
        limitations=[],
        open_findings=0,
    )
    assert result.decision == "needs_human_review"
    assert next(g for g in result.criteria if g.id == "risk").status == "fail"


@pytest.mark.parametrize(
    "content",
    [
        "No criteria here",
        "## Same\nOne\n## Same\nTwo",
        "+++\nmax_risk_score = 9\n+++\n## Scope\nSmall changes",
        "+++\nunknown_setting = true\n+++\n## Scope\nSmall changes",
    ],
)
def test_invalid_policy_does_not_silently_use_defaults(content: str) -> None:
    with pytest.raises(ValueError):
        parse_policy(content, source="repository", base_sha="b" * 40, head_sha="a" * 40)


@pytest.mark.parametrize(
    "content", [POLICY.replace("\n", "\r\n"), "\ufeff" + POLICY, POLICY.replace("+++\n", "+++  \n")]
)
def test_supported_policy_formatting_preserves_restrictive_rules(content: str) -> None:
    policy = parse_policy(content, source="repository", base_sha="b" * 40, head_sha="a" * 40)
    assert policy.rules.required_checks == ["tests"]
    assert policy.rules.human_review_paths == ["auth/**"]


def test_general_requirements_outside_sections_are_not_ignored() -> None:
    policy = parse_policy(
        "# Policy\nAlways require an owner.\n## Scope\nSmall changes.",
        source="repository",
        base_sha="b" * 40,
        head_sha="a" * 40,
    )
    assert any("Always require an owner." in c.requirement for c in policy.criteria)
