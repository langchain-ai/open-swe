import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_install_policy_parser import EXACT_APPROVAL_PHRASE
from docs.hermes.pseudo_router.controlled_bootstrap_approval_runner import (
    SCHEMA_VERSION,
    build_controlled_bootstrap_approval_packet,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_REPO = "ollehillbom1/hermes-open-swe-testrepo"


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


def test_defaults_to_blocked_and_never_allows_execution_without_inputs():
    packet = build_controlled_bootstrap_approval_packet()

    assert packet["schema_version"] == SCHEMA_VERSION
    assert packet["status"] == "BLOCKED"
    assert packet["gate"] == "WARN"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert packet["APPROVAL_PACKET_READY"] is False
    assert packet["side_effects"] == []
    assert "missing_readiness_decision" in packet["block_reasons"]
    assert "missing_exact_approval_phrase" in packet["block_reasons"]


def test_returns_controlled_dry_run_packet_for_ready_allowlisted_testrepo_with_exact_phrase():
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=_readiness_decision(),
        approval_text=EXACT_APPROVAL_PHRASE,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_APPROVAL_PACKET_READY"
    assert packet["gate"] == "PASS"
    assert packet["APPROVAL_PACKET_READY"] is True
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert packet["target_repo"] == TEST_REPO
    assert packet["approval_text_echoed"] is False
    assert packet["approval_phrase_received"] is True
    assert packet["allowed_actions"] == ["dry_run_controlled_local_testrepo_bootstrap_preview"]
    assert packet["NEXT_REQUIRED_HUMAN_ACTION"] == "review controlled local testrepo bootstrap dry-run packet"
    assert packet["side_effects"] == []


def test_blocks_if_readiness_is_not_ready_or_install_allowed_check_is_false():
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=_readiness_decision(
            BOOTSTRAP_READY=False,
            checks={
                "execution_manifest_ready_checklist": True,
                "install_allowed": False,
                "target_repo_allowlisted": True,
                "scanners_and_audits_green": True,
                "live_actions_disabled": True,
                "no_secret_or_approval_echo": True,
            },
        ),
        approval_text=EXACT_APPROVAL_PHRASE,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "readiness_not_ready" in packet["block_reasons"]
    assert "install_not_allowed_by_readiness" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_blocks_unallowlisted_target_repo_even_when_readiness_claims_allowlisted():
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=_readiness_decision(target_repo="ollehillbom1/north-star-erp"),
        approval_text=EXACT_APPROVAL_PHRASE,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "target_repo_not_allowlisted_for_controlled_testrepo_bootstrap" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_blocks_near_miss_approval_phrase_and_does_not_echo_raw_text():
    near_misses = [
        "ALLOW_BOOTSTRAP_INSTALL=YES; start webhook too",
        f"{EXACT_APPROVAL_PHRASE}\nstart webhook too",
    ]

    for approval_text in near_misses:
        packet = build_controlled_bootstrap_approval_packet(
            readiness_decision=_readiness_decision(),
            approval_text=approval_text,
            allowed_test_repos=[TEST_REPO],
        )
        serialized = json.dumps(packet, sort_keys=True)

        assert packet["status"] == "BLOCKED"
        assert "missing_exact_approval_phrase" in packet["block_reasons"]
        assert packet["approval_text_echoed"] is False
        assert approval_text not in serialized
        assert EXACT_APPROVAL_PHRASE not in serialized
        assert packet["side_effects"] == []


def test_blocks_secret_like_target_repo_without_echoing_it():
    secret_repo = "token=ghp_FAKESECRET1234567890"
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=_readiness_decision(target_repo=secret_repo),
        approval_text=EXACT_APPROVAL_PHRASE,
        allowed_test_repos=[secret_repo],
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["status"] == "BLOCKED"
    assert "unsafe_raw_approval_or_secret_material_detected" in packet["block_reasons"]
    assert packet["target_repo"] == "[REDACTED]"
    assert "ghp_" not in serialized
    assert "FAKESECRET" not in serialized
    assert packet["side_effects"] == []


def test_blocks_secret_like_input_without_echoing_secret_or_approval_phrase():
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=_readiness_decision(),
        approval_text=f"{EXACT_APPROVAL_PHRASE}\ntoken=ghp_FAKESECRET1234567890",
        allowed_test_repos=[TEST_REPO],
    )
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["status"] == "BLOCKED"
    assert "unsafe_raw_approval_or_secret_material_detected" in packet["block_reasons"]
    assert "ghp_" not in serialized
    assert "FAKESECRET" not in serialized
    assert EXACT_APPROVAL_PHRASE not in serialized
    assert packet["side_effects"] == []


def test_blocks_malformed_readiness_payload_without_crashing():
    packet = build_controlled_bootstrap_approval_packet(
        readiness_decision=["not", "an", "object"],
        approval_text=EXACT_APPROVAL_PHRASE,
        allowed_test_repos=[TEST_REPO],
    )

    assert packet["status"] == "BLOCKED"
    assert packet["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert "readiness_payload_not_object" in packet["block_reasons"]
    assert packet["side_effects"] == []


def test_cli_blocks_malformed_approval_input_without_traceback(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    approval_path = tmp_path / "approval.json"
    readiness_path.write_text(json.dumps(_readiness_decision()), encoding="utf-8")
    approval_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_approval_runner.py"),
            "--readiness-decision",
            str(readiness_path),
            "--approval-input",
            str(approval_path),
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
    assert "approval_input_payload_not_object" in output["block_reasons"]
    assert output["side_effects"] == []


def test_cli_reads_json_inputs_and_writes_only_stdout(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    approval_path = tmp_path / "approval.json"
    readiness_path.write_text(json.dumps(_readiness_decision()), encoding="utf-8")
    approval_path.write_text(
        json.dumps(
            {
                "approval_text": EXACT_APPROVAL_PHRASE,
                "allowed_test_repos": [TEST_REPO],
            }
        ),
        encoding="utf-8",
    )

    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/controlled_bootstrap_approval_runner.py"),
            "--readiness-decision",
            str(readiness_path),
            "--approval-input",
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
    assert output["APPROVAL_PACKET_READY"] is True
    assert output["BOOTSTRAP_EXECUTION_ALLOWED"] is False
    assert output["side_effects"] == []
    assert before == after == ["approval.json", "readiness.json"]
