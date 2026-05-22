from __future__ import annotations

import copy

from docs.hermes.pseudo_router import review_trigger_router as router

BASE_PAYLOAD = {
    "event": {
        "type": "independent review recommended",
        "source": "dry-run",
        "repo": "ollehillbom1/north-star-erp",
        "trigger_user": "ollehillbom1",
    },
    "input_context": {
        "reality_state": {"TASK_ID": "NS-REVIEW-001", "OWNER_SESSION": "harness-dry-run"},
        "task_card": {"id": "NS-REVIEW-001", "title": "VAT evidence follow-up"},
        "git_diff_summary": {"files": ["apps/core-api/src/vat.ts"]},
        "evidence_log": [{"command": "./b.sh --quick", "status": "PASS"}],
    },
    "allowed_paths": ["apps/core-api/src/**", "tests/**"],
    "forbidden_paths": [".env", "**/.env", "**/secrets/**"],
    "required_tests": ["./b.sh --quick"],
    "stop_conditions": ["secrets", "missing reviewer", "runtime claim without evidence"],
}


def test_route_review_trigger_emits_structured_review_and_next_action_contract() -> None:
    result = router.route_review_trigger(copy.deepcopy(BASE_PAYLOAD))

    assert result["status"] == "OK"
    assert result["schema_version"] == "hermes.review-loop.v1"
    assert result["review_request"]["TASK_ID"] == "NS-REVIEW-001"
    assert result["review_request"]["repo"] == "ollehillbom1/north-star-erp"
    assert result["review_request"]["allowed_paths"] == BASE_PAYLOAD["allowed_paths"]
    assert result["next_action_request"]["allowed_next_actions"] == [
        "MARK_TASK_DONE",
        "CREATE_FOLLOWUP_TASK",
        "START_BUILDER_FIX_AGENT",
        "START_QA_AGENT",
        "START_SECURITY_AGENT",
        "START_ACCOUNTING_DOMAIN_AGENT",
        "BLOCK_AND_ESCALATE",
    ]
    assert result["gate"] == "PASS"
    assert "runtime_started" not in result


def test_route_review_trigger_blocks_unknown_repo_before_prompt_generation() -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["event"]["repo"] = "someone-else/repo"

    result = router.route_review_trigger(payload)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "repo_not_allowed"
    assert "generated_review_prompt" not in result


def test_route_review_trigger_blocks_forbidden_path_overlap() -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["allowed_paths"].append(".env")

    result = router.route_review_trigger(payload)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "forbidden_path_allowed"
    assert result["blocked_paths"] == [".env"]


def test_route_review_trigger_blocks_external_integrations_by_default() -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["requested_tools"] = ["execute", "slack", "linear", "fetch_url"]

    result = router.route_review_trigger(payload)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "tool_not_allowed"
    assert result["blocked_tools"] == ["slack", "linear", "fetch_url"]


def test_route_review_trigger_rejects_missing_evidence_log() -> None:
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["input_context"]["evidence_log"] = []

    result = router.route_review_trigger(payload)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "missing_evidence"
