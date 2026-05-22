from __future__ import annotations

import copy

from docs.hermes.pseudo_router.next_action_policy import decide_next_action

BASE_REVIEW = {
    "schema_version": "hermes.independent_review.v1",
    "owner_agent_role": "independent_review_agent",
    "reviewer_identity": "claude-reviewer-1",
    "verdict": "NEEDS_FOLLOWUP",
    "confidence": "HIGH",
    "summary": "Missing runtime evidence; bounded QA follow-up is required.",
    "evidence_checked": [
        {"name": "quick baseline", "status": "PASS", "details": "./b.sh --quick PASS"},
        {"name": "runtime smoke", "status": "MISSING", "details": "missing UAT/API smoke"},
    ],
    "findings": [
        {
            "severity": "MEDIUM",
            "area": "runtime evidence",
            "claim": "Runtime claim lacks UAT/API smoke.",
            "required_followup": "Run bounded QA smoke.",
        }
    ],
    "stop_conditions_hit": ["runtime PASS claim without UAT/API smoke evidence"],
    "next_agent_role_recommendation": "qa_agent",
    "verification_required_before_pass": ["focused UAT/API smoke"],
}

BASE_CONTEXT = {
    "TASK_ID": "GH-REVIEW-42-9001",
    "requested_final_gate": "WARN",
    "allowed_next_actions": [
        "MARK_TASK_DONE",
        "CREATE_FOLLOWUP_TASK",
        "START_BUILDER_FIX_AGENT",
        "START_QA_AGENT",
        "START_SECURITY_AGENT",
        "START_ACCOUNTING_DOMAIN_AGENT",
        "BLOCK_AND_ESCALATE",
    ],
}


def review(**overrides: object) -> dict:
    item = copy.deepcopy(BASE_REVIEW)
    item.update(overrides)
    return item


def context(**overrides: object) -> dict:
    item = copy.deepcopy(BASE_CONTEXT)
    item.update(overrides)
    return item


def test_needs_followup_qa_review_selects_bounded_qa_agent_without_side_effects() -> None:
    result = decide_next_action(review(), context())

    assert result["status"] == "OK"
    assert result["gate"] == "WARN"
    assert result["TASK_ID"] == "GH-REVIEW-42-9001"
    assert result["decision"]["next_action"] == "START_QA_AGENT"
    assert result["decision"]["final_pass_eligible"] is False
    assert result["decision"]["reviewer_identity"] == "claude-reviewer-1"
    assert result["side_effects"] == []


def test_reviewer_pass_allows_mark_task_done_for_requested_final_pass() -> None:
    result = decide_next_action(
        review(verdict="PASS", findings=[], stop_conditions_hit=[]),
        context(requested_final_gate="PASS"),
    )

    assert result["status"] == "OK"
    assert result["gate"] == "PASS"
    assert result["decision"]["next_action"] == "MARK_TASK_DONE"
    assert result["decision"]["final_pass_eligible"] is True


def test_warn_no_blockers_allows_followup_packet_without_blocking_final_gate() -> None:
    result = decide_next_action(
        review(verdict="WARN_NO_BLOCKERS", stop_conditions_hit=[]),
        context(requested_final_gate="PASS"),
    )

    assert result["status"] == "OK"
    assert result["gate"] == "WARN"
    assert result["decision"]["next_action"] == "CREATE_FOLLOWUP_TASK"
    assert result["decision"]["final_pass_eligible"] is True


def test_missing_reviewer_identity_blocks_before_next_action() -> None:
    item = review()
    del item["reviewer_identity"]

    result = decide_next_action(item, context())

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "missing_review_fields"
    assert result["missing_fields"] == ["reviewer_identity"]


def test_missing_evidence_blocks_before_next_action() -> None:
    result = decide_next_action(review(evidence_checked=[]), context())

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "missing_evidence_checked"


def test_requested_final_pass_is_blocked_when_review_verdict_needs_followup() -> None:
    result = decide_next_action(
        review(stop_conditions_hit=[]), context(requested_final_gate="PASS")
    )

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "reviewer_not_final_pass"
    assert result["verdict"] == "NEEDS_FOLLOWUP"


def test_secret_stop_condition_blocks_and_escalates() -> None:
    result = decide_next_action(review(stop_conditions_hit=["secrets detected in diff"]), context())

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "stop_condition_hit"
    assert result["decision"]["next_action"] == "BLOCK_AND_ESCALATE"


def test_unknown_recommended_agent_role_blocks_by_default() -> None:
    result = decide_next_action(review(next_agent_role_recommendation="slack_agent"), context())

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "unsupported_next_agent_role"
    assert result["recommended_role"] == "slack_agent"
