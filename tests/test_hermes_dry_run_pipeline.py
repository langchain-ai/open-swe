from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.dry_run_pipeline import run_pipeline

BASE_COMMENT_EVENT = {
    "repo": "ollehillbom1/north-star-erp",
    "comment_author": "ollehillbom1",
    "comment_body": "@openswe review https://github.com/ollehillbom1/north-star-erp/pull/123",
    "issue_number": 42,
    "comment_id": 9001,
    "allowed_paths": ["apps/core-api/src/**", "tests/**"],
    "forbidden_paths": [".env", "**/.env", "**/secrets/**"],
    "required_tests": ["./b.sh --quick", "npm run typecheck"],
}

BASE_REVIEW_REPORT = {
    "schema_version": "hermes.independent_review.v1",
    "owner_agent_role": "independent_review_agent",
    "reviewer_identity": "claude-reviewer-1",
    "verdict": "NEEDS_FOLLOWUP",
    "confidence": "HIGH",
    "summary": "Runtime evidence is incomplete.",
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


def payload(**overrides: object) -> dict:
    item = {
        "comment_event": copy.deepcopy(BASE_COMMENT_EVENT),
        "review_report": copy.deepcopy(BASE_REVIEW_REPORT),
        "requested_final_gate": "WARN",
    }
    item.update(overrides)
    return item


def test_pipeline_composes_comment_router_and_next_action_policy_without_side_effects() -> None:
    result = run_pipeline(payload())

    assert result["status"] == "OK"
    assert result["schema_version"] == "hermes.review-pipeline.v1"
    assert result["TASK_ID"] == "GH-REVIEW-42-9001"
    assert result["side_effects"] == []
    assert result["stages"]["github_comment_adapter"]["status"] == "OK"
    assert result["stages"]["next_action_policy"]["status"] == "OK"
    assert result["final_decision"]["next_action"] == "START_QA_AGENT"
    assert result["final_decision"]["final_pass_eligible"] is False
    assert (
        result["source_event"]["pr_url"]
        == "https://github.com/ollehillbom1/north-star-erp/pull/123"
    )


def test_pipeline_blocks_at_comment_stage_and_does_not_evaluate_policy() -> None:
    event = copy.deepcopy(BASE_COMMENT_EVENT)
    event["comment_author"] = "unknown-user"

    result = run_pipeline(payload(comment_event=event))

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_stage"] == "github_comment_adapter"
    assert result["block_reason"] == "trigger_user_not_allowed"
    assert "next_action_policy" not in result["stages"]
    assert result["side_effects"] == []


def test_pipeline_blocks_final_pass_when_reviewer_verdict_requires_followup() -> None:
    result = run_pipeline(payload(requested_final_gate="PASS"))

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_stage"] == "next_action_policy"
    assert result["block_reason"] == "reviewer_not_final_pass"
    assert result["TASK_ID"] == "GH-REVIEW-42-9001"
    assert result["side_effects"] == []


def test_pipeline_fixture_is_stable_and_runs_end_to_end() -> None:
    fixture_path = Path("docs/hermes/examples/github_review_pipeline_input.example.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = run_pipeline(fixture)

    assert result["status"] == "OK"
    assert result["TASK_ID"] == "GH-REVIEW-42-9001"
    assert result["final_decision"]["next_action"] == "START_QA_AGENT"


def test_pipeline_cli_outputs_json_without_starting_live_integrations() -> None:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "docs/hermes/pseudo_router/dry_run_pipeline.py",
            "docs/hermes/examples/github_review_pipeline_input.example.json",
        ],
        check=True,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "OK"
    assert result["side_effects"] == []
    assert result["final_decision"]["next_action"] == "START_QA_AGENT"
    assert "webhook" not in completed.stdout.lower()
    assert "github_api_called" not in completed.stdout
