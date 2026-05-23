import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_dry_run_plan import (
    SCHEMA_VERSION,
    build_bootstrap_dry_run_plan,
)
from docs.hermes.pseudo_router.bootstrap_install_policy_parser import EXACT_APPROVAL_PHRASE

ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = "example-owner/example-testrepo"


def _readiness_decision(**overrides):
    decision = {
        "schema_version": "hermes.bootstrap-readiness-aggregator.v1",
        "status": "BOOTSTRAP_READY_FOR_HUMAN_APPROVAL",
        "gate": "PASS",
        "BOOTSTRAP_READY": True,
        "target_repo": TEST_REPO,
        "checks": {
            "execution_manifest_ready_checklist": True,
            "install_allowed": True,
            "target_repo_allowlisted": True,
            "scanners_and_audits_green": True,
            "live_actions_disabled": True,
            "no_secret_or_approval_echo": True,
        },
        "block_reasons": [],
        "NEXT_REQUIRED_HUMAN_ACTION": "approve controlled local testrepo bootstrap",
        "side_effects": [],
    }
    decision.update(overrides)
    return decision


def _approval_packet(**overrides):
    packet = {
        "schema_version": "hermes.testrepo-bootstrap-approval.v1",
        "status": "APPROVED_DRY_RUN_PLAN",
        "gate": "PASS",
        "TASK_ID": "GH-REVIEW-42-9001",
        "target_repo": TEST_REPO,
        "approval_packet": {
            "dry_run_only": True,
            "target_repo": TEST_REPO,
            "required_exact_approval": f"{EXACT_APPROVAL_PHRASE} repo={TEST_REPO}",
            "may_create_github_app": False,
            "may_configure_webhook": False,
            "may_push_or_pr": False,
            "may_start_server": False,
            "may_edit_erp": False,
        },
        "side_effects": [],
    }
    packet.update(overrides)
    return packet


def test_blocks_by_default_when_readiness_is_not_ready():
    plan = build_bootstrap_dry_run_plan(
        _readiness_decision(BOOTSTRAP_READY=False, status="BOOTSTRAP_NOT_READY"),
        _approval_packet(),
    )

    assert plan["schema_version"] == SCHEMA_VERSION
    assert plan["status"] == "BLOCKED"
    assert plan["gate"] == "FAIL"
    assert plan["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert plan["side_effects"] == []
    assert "readiness_not_ready" in plan["block_reasons"]


def test_ready_inputs_emit_sanitized_dry_run_plan_without_live_actions():
    plan = build_bootstrap_dry_run_plan(_readiness_decision(), _approval_packet())
    serialized = json.dumps(plan, sort_keys=True)

    assert plan["status"] == "DRY_RUN_BOOTSTRAP_PLAN_READY"
    assert plan["gate"] == "PASS"
    assert plan["target_repo"] == TEST_REPO
    assert plan["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert plan["NEXT_REQUIRED_HUMAN_ACTION"] == "review dry-run bootstrap plan output"
    assert plan["side_effects"] == []
    assert plan["planned_steps"] == [
        {
            "id": "validate_testrepo_scope",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_github_app_setup_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_webhook_setup_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_local_testrepo_bootstrap_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
    ]
    assert EXACT_APPROVAL_PHRASE not in serialized
    assert "required_exact_approval" not in serialized
    assert "ghp_" not in serialized
    assert "PRIVATE KEY" not in serialized


def test_blocks_if_approval_packet_enables_any_live_action():
    unsafe_packet = _approval_packet(
        approval_packet={
            "dry_run_only": True,
            "target_repo": TEST_REPO,
            "may_create_github_app": True,
            "may_configure_webhook": False,
            "may_push_or_pr": False,
            "may_start_server": False,
            "may_edit_erp": False,
        }
    )

    plan = build_bootstrap_dry_run_plan(_readiness_decision(), unsafe_packet)

    assert plan["status"] == "BLOCKED"
    assert plan["gate"] == "FAIL"
    assert plan["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_live_actions_enabled" in plan["block_reasons"]
    assert plan["side_effects"] == []


def test_blocks_if_readiness_and_approval_target_repos_do_not_match():
    plan = build_bootstrap_dry_run_plan(
        _readiness_decision(target_repo="example-owner/example-testrepo"),
        _approval_packet(target_repo="example-owner/other-testrepo"),
    )

    assert plan["status"] == "BLOCKED"
    assert plan["gate"] == "FAIL"
    assert plan["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "target_repo_mismatch" in plan["block_reasons"]
    assert plan["target_repo"] == TEST_REPO
    assert plan["side_effects"] == []


def test_cli_reads_json_inputs_and_writes_only_stdout(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    approval_path = tmp_path / "approval.json"
    readiness_path.write_text(json.dumps(_readiness_decision()), encoding="utf-8")
    approval_path.write_text(json.dumps(_approval_packet()), encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/bootstrap_dry_run_plan.py"),
            "--readiness-decision",
            str(readiness_path),
            "--approval-packet",
            str(approval_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    output = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stderr == ""
    assert output["status"] == "DRY_RUN_BOOTSTRAP_PLAN_READY"
    assert output["side_effects"] == []
    assert before == after
