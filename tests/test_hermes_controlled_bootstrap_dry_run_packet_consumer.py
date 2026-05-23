import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.controlled_bootstrap_dry_run_packet_consumer import (
    SCHEMA_VERSION,
    build_controlled_bootstrap_dry_run_packet,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = "ollehillbom1/hermes-open-swe-testrepo"


def _approval_packet(**overrides):
    packet = {
        "schema_version": "hermes.controlled-bootstrap-approval-runner.v1",
        "status": "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_APPROVAL_PACKET_READY",
        "gate": "PASS",
        "APPROVAL_PACKET_READY": True,
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": TEST_REPO,
        "approval_text_echoed": False,
        "approval_phrase_received": True,
        "allowed_actions": ["dry_run_controlled_local_testrepo_bootstrap_preview"],
        "source_readiness_schema": "hermes.bootstrap-readiness-aggregator.v1",
        "source_readiness_gate": "PASS",
        "source_next_required_human_action": "approve controlled local testrepo bootstrap",
        "NEXT_REQUIRED_HUMAN_ACTION": "review controlled local testrepo bootstrap dry-run packet",
        "side_effects": [],
    }
    packet.update(overrides)
    return packet


def test_defaults_to_blocked_and_never_allows_execution_without_inputs():
    packet = build_controlled_bootstrap_dry_run_packet()

    assert packet["schema_version"] == SCHEMA_VERSION
    assert packet["status"] == "BLOCKED"
    assert packet["gate"] == "FAIL"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert packet["DRY_RUN_PACKET_READY"] is False
    assert packet["side_effects"] == []
    assert "missing_approval_packet" in packet["block_reasons"]


def test_ready_approval_packet_emits_execution_checklist_without_live_actions():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(),
        allowed_test_repos=[TEST_REPO],
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["status"] == "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_DRY_RUN_PACKET_READY"
    assert packet["gate"] == "PASS"
    assert packet["DRY_RUN_PACKET_READY"] is True
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert packet["target_repo"] == TEST_REPO
    assert (
        packet["NEXT_REQUIRED_HUMAN_ACTION"]
        == "review controlled local testrepo bootstrap dry-run packet"
    )
    assert packet["side_effects"] == []
    assert packet["execution_checklist"] == [
        {
            "id": "confirm_testrepo_scope",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "confirm_approval_packet_ready",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_local_bootstrap_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "stop_before_live_bootstrap_execution",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
    ]
    assert "ALLOW_BOOTSTRAP_INSTALL=YES" not in serialized
    assert "approval_text" not in serialized
    assert "ghp_" not in serialized
    assert "PRIVATE KEY" not in serialized


def test_blocks_when_approval_runner_packet_is_not_ready():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(
            status="BLOCKED",
            gate="WARN",
            APPROVAL_PACKET_READY=False,
            block_reasons=["readiness_not_ready"],
        ),
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_not_ready" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_blocks_unallowlisted_target_even_when_approval_packet_is_ready():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(target_repo="ollehillbom1/north-star-erp"),
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert (
        "target_repo_not_allowlisted_for_controlled_testrepo_bootstrap" in packet["block_reasons"]
    )
    assert packet["side_effects"] == []


def test_blocks_if_any_live_action_or_executor_toggle_is_enabled():
    unsafe = _approval_packet(
        BOOTSTRAP_EXECUTION_ALLOWED=True,
        may_start_server=True,
        ALLOW_BOOTSTRAP_INSTALL="YES",
    )

    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=unsafe,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_execution_allowed" in packet["block_reasons"]
    assert "live_action_toggle_enabled" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_blocks_non_dry_run_allowed_actions_even_when_dry_run_action_is_present():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(
            allowed_actions=[
                "dry_run_controlled_local_testrepo_bootstrap_preview",
                "start_server",
                "deploy_prod",
            ]
        ),
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_allows_non_dry_run_actions" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_blocks_malformed_allowed_actions_without_crashing():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(allowed_actions=None),
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_missing_dry_run_action" in packet["block_reasons"]
    assert "approval_packet_allows_non_dry_run_actions" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_does_not_mutate_loaded_approval_packet():
    approval_packet = _approval_packet(__invalid_json__=True)

    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=approval_packet,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert "approval_packet_json_invalid" in packet["block_reasons"]
    assert approval_packet["__invalid_json__"] is True


def test_blocks_secret_like_inputs_without_echoing_secret_or_approval_phrase():
    secret_value = "ghp_FAKESECRET7890"
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_approval_packet(
            target_repo=f"token={secret_value}",
            raw_approval_text=f"ALLOW_BOOTSTRAP_INSTALL=YES token={secret_value}",
        ),
        allowed_test_repos=[f"token={secret_value}"],
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["status"] == "BLOCKED"
    assert "unsafe_raw_approval_or_secret_material_detected" in packet["block_reasons"]
    assert packet["target_repo"] == "[REDACTED]"
    assert "ghp_" not in serialized
    assert secret_value not in serialized
    assert "ALLOW_BOOTSTRAP_INSTALL=YES" not in serialized
    assert packet["side_effects"] == []


def test_blocks_malformed_approval_packet_without_crashing():
    packet = build_controlled_bootstrap_dry_run_packet(
        approval_packet=["not", "an", "object"],
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_payload_not_object" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_cli_blocks_malformed_approval_packet_without_traceback(tmp_path):
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_dry_run_packet_consumer.py"),
            "--approval-packet",
            str(approval_path),
            "--allowed-test-repo",
            TEST_REPO,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout)

    assert result.returncode == 1
    assert result.stderr == ""
    assert output["status"] == "BLOCKED"
    assert output["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_payload_not_object" in output["block_reasons"]
    assert output["side_effects"] == []


def test_cli_blocks_invalid_json_approval_packet_without_traceback(tmp_path):
    approval_path = tmp_path / "approval.json"
    approval_path.write_text("{not valid json", encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_dry_run_packet_consumer.py"),
            "--approval-packet",
            str(approval_path),
            "--allowed-test-repo",
            TEST_REPO,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout)

    assert result.returncode == 1
    assert result.stderr == ""
    assert output["status"] == "BLOCKED"
    assert output["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_json_invalid" in output["block_reasons"]
    assert output["side_effects"] == []


def test_cli_blocks_missing_approval_packet_without_traceback(tmp_path):
    missing_path = tmp_path / "missing.json"

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_dry_run_packet_consumer.py"),
            "--approval-packet",
            str(missing_path),
            "--allowed-test-repo",
            TEST_REPO,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout)

    assert result.returncode == 1
    assert result.stderr == ""
    assert output["status"] == "BLOCKED"
    assert output["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "approval_packet_input_unreadable" in output["block_reasons"]
    assert output["side_effects"] == []


def test_cli_reads_json_input_and_writes_only_stdout(tmp_path):
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(_approval_packet()), encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_dry_run_packet_consumer.py"),
            "--approval-packet",
            str(approval_path),
            "--allowed-test-repo",
            TEST_REPO,
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
    assert output["status"] == "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_DRY_RUN_PACKET_READY"
    assert output["side_effects"] == []
    assert before == after
