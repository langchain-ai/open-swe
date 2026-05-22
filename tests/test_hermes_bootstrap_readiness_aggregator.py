import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_install_policy_parser import EXACT_APPROVAL_PHRASE
from docs.hermes.pseudo_router.bootstrap_readiness_aggregator import (
    SCHEMA_VERSION,
    aggregate_bootstrap_readiness,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = "ollehillbom1/hermes-open-swe-testrepo"


def _execution_manifest(**overrides):
    manifest = {
        "schema_version": "hermes.bootstrap-execution-manifest.v1",
        "status": "READY_FOR_HUMAN_APPROVAL_CHECKLIST",
        "gate": "WARN",
        "TASK_ID": "GH-REVIEW-42-9001",
        "hard_disabled_actions": {
            "may_create_github_app": False,
            "may_configure_webhook": False,
            "may_start_server": False,
            "may_push_or_pr": False,
            "may_deploy_prod": False,
        },
        "side_effects": [],
    }
    manifest.update(overrides)
    return manifest


def _install_policy(**overrides):
    policy = {
        "schema_version": "hermes.bootstrap-install-policy.v1",
        "status": "POLICY_EVALUATED",
        "gate": "PASS",
        "INSTALL_ALLOWED": True,
        "install_allowed": True,
        "target_repo": TEST_REPO,
        "allowed_test_repos": [TEST_REPO],
        "required_exact_approval": EXACT_APPROVAL_PHRASE,
        "approval_text_echoed": False,
        "allowed_actions": ["dry_run_testrepo_bootstrap_install"],
        "block_reasons": [],
        "side_effects": [],
    }
    policy.update(overrides)
    return policy


def _readiness(**overrides):
    readiness = {
        "schema_version": "northstar.local-readiness.v1",
        "gate": "PASS",
        "scanners": {
            "ruff": "PASS",
            "gitleaks": "PASS",
            "trufflehog": "PASS",
            "uv_audit": "PASS",
            "osv_scanner": "PASS",
        },
        "audits": {"scanner_suite": "PASS"},
        "side_effects": [],
    }
    readiness.update(overrides)
    return readiness


def _testrepo_gate(**overrides):
    gate = {
        "schema_version": "northstar.testrepo-bootstrap-gate.v1",
        "gate": "PASS",
        "target_repo": TEST_REPO,
        "allowed_test_repos": [TEST_REPO],
        "live_actions_enabled": {
            "ALLOW_BOOTSTRAP_INSTALL": False,
            "ALLOW_WEBHOOK_SETUP": False,
            "ALLOW_GITHUB_APP_SETUP": False,
            "ALLOW_PROD_INSTALL": False,
            "ALLOW_DOCKER_BUILD": False,
        },
        "disabled_webhooks": ["slack", "linear"],
        "disabled_agent_tools": ["slack_thread_reply", "linear_comment", "fetch_url"],
        "side_effects": [],
    }
    gate.update(overrides)
    return gate


def test_defaults_to_bootstrap_ready_false_when_inputs_are_missing():
    decision = aggregate_bootstrap_readiness()

    assert decision["schema_version"] == SCHEMA_VERSION
    assert decision["status"] == "BOOTSTRAP_NOT_READY"
    assert decision["BOOTSTRAP_READY"] is False
    assert decision["side_effects"] == []
    assert decision["NEXT_REQUIRED_HUMAN_ACTION"] == "produce bootstrap readiness inputs"
    assert "missing_execution_manifest" in decision["block_reasons"]


def test_returns_single_ready_decision_only_when_all_dry_run_gates_are_green():
    decision = aggregate_bootstrap_readiness(
        execution_manifest=_execution_manifest(),
        install_policy=_install_policy(),
        readiness_status=_readiness(),
        testrepo_gate=_testrepo_gate(),
    )

    assert decision["status"] == "BOOTSTRAP_READY_FOR_HUMAN_APPROVAL"
    assert decision["gate"] == "PASS"
    assert decision["BOOTSTRAP_READY"] is True
    assert decision["target_repo"] == TEST_REPO
    assert decision["NEXT_REQUIRED_HUMAN_ACTION"] == "approve controlled local testrepo bootstrap"
    assert decision["side_effects"] == []
    assert decision["block_reasons"] == []
    assert decision["checks"] == {
        "execution_manifest_ready_checklist": True,
        "install_allowed": True,
        "target_repo_allowlisted": True,
        "scanners_and_audits_green": True,
        "live_actions_disabled": True,
        "no_secret_or_approval_echo": True,
    }


def test_blocks_install_policy_without_echoing_raw_approval_text_or_secrets():
    decision = aggregate_bootstrap_readiness(
        execution_manifest=_execution_manifest(
            markdown=f"unsafe {EXACT_APPROVAL_PHRASE} ghp_FAKESECRET123456"
        ),
        install_policy=_install_policy(
            INSTALL_ALLOWED=False,
            install_allowed=False,
            approval_text=f"{EXACT_APPROVAL_PHRASE}\nghp_FAKESECRET123456",
            token="ghp_FAKESECRET123456",
        ),
        readiness_status=_readiness(),
        testrepo_gate=_testrepo_gate(),
    )
    serialized = json.dumps(decision, sort_keys=True)

    assert decision["BOOTSTRAP_READY"] is False
    assert "install_policy_not_allowed" in decision["block_reasons"]
    assert "unsafe_raw_approval_or_secret_material_detected" in decision["block_reasons"]
    assert EXACT_APPROVAL_PHRASE not in serialized
    assert "ghp_" not in serialized
    assert "FAKESECRET" not in serialized
    assert decision["side_effects"] == []


def test_blocks_when_any_live_action_is_enabled_or_testrepo_is_not_allowlisted():
    decision = aggregate_bootstrap_readiness(
        execution_manifest=_execution_manifest(
            hard_disabled_actions={"may_start_server": True, "may_push_or_pr": False}
        ),
        install_policy=_install_policy(target_repo="ollehillbom1/north-star-erp"),
        readiness_status=_readiness(),
        testrepo_gate=_testrepo_gate(
            target_repo="ollehillbom1/north-star-erp",
            allowed_test_repos=[TEST_REPO],
            live_actions_enabled={"ALLOW_WEBHOOK_SETUP": True},
        ),
    )

    assert decision["BOOTSTRAP_READY"] is False
    assert "target_repo_not_allowlisted_for_testrepo_bootstrap" in decision["block_reasons"]
    assert "execution_manifest_live_actions_enabled" in decision["block_reasons"]
    assert "testrepo_gate_live_actions_enabled" in decision["block_reasons"]
    assert decision["side_effects"] == []


def test_cli_reads_four_json_inputs_and_writes_only_stdout(tmp_path):
    paths = {}
    payloads = {
        "execution": _execution_manifest(),
        "install": _install_policy(),
        "readiness": _readiness(),
        "testrepo": _testrepo_gate(),
    }
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/bootstrap_readiness_aggregator.py"),
            "--execution-manifest",
            str(paths["execution"]),
            "--install-policy",
            str(paths["install"]),
            "--readiness-status",
            str(paths["readiness"]),
            "--testrepo-gate",
            str(paths["testrepo"]),
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
    assert output["BOOTSTRAP_READY"] is True
    assert output["side_effects"] == []
    assert before == after
