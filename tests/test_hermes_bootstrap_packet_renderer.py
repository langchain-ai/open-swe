import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_packet_renderer import (
    SCHEMA_VERSION,
    render_bootstrap_packet_markdown,
)
from docs.hermes.pseudo_router.testrepo_bootstrap_approval_gate import (
    build_bootstrap_approval_packet,
)

ROOT = Path(__file__).resolve().parents[1]


def _pipeline_result(**overrides):
    result = {
        "status": "OK",
        "schema_version": "hermes.review-pipeline.v1",
        "gate": "WARN",
        "TASK_ID": "GH-REVIEW-42-9001",
        "source_event": {
            "repo": "ollehillbom1/northstar-agent-harness-testrepo",
            "comment_author": "ollehillbom1",
            "issue_number": 42,
            "comment_id": 9001,
        },
        "stages": {
            "github_comment_adapter": {"status": "OK", "gate": "PASS"},
            "next_action_policy": {"status": "OK", "gate": "WARN"},
        },
        "final_decision": {
            "next_action": "START_QA_AGENT",
            "reviewer_identity": "claude-reviewer-1",
            "review_verdict": "NEEDS_FOLLOWUP",
            "final_pass_eligible": False,
            "verification_required_before_pass": ["focused UAT/API smoke"],
            "allowed_next_actions": ["START_QA_AGENT"],
        },
        "side_effects": [],
    }
    result.update(overrides)
    return result


def _bootstrap_profile(**overrides):
    profile = {
        "target_repo": "ollehillbom1/northstar-agent-harness-testrepo",
        "allowed_repos": ["ollehillbom1/northstar-agent-harness-testrepo"],
        "allowed_trigger_users": ["ollehillbom1"],
        "sandbox_type": "daytona",
        "disabled_integrations": ["slack", "linear"],
        "disabled_tools": ["http_request", "fetch_url", "web_search"],
        "external_setup_requested": ["github_app", "webhook", "docker_build"],
        "human_approval": None,
    }
    profile.update(overrides)
    return profile


def test_renders_file_only_human_approval_markdown_without_side_effects():
    approval_packet = build_bootstrap_approval_packet(_pipeline_result(), _bootstrap_profile())

    rendered = render_bootstrap_packet_markdown(approval_packet)

    assert rendered["status"] == "READY_FOR_HUMAN_APPROVAL"
    assert rendered["schema_version"] == SCHEMA_VERSION
    assert rendered["gate"] == "WARN"
    assert rendered["side_effects"] == []
    assert rendered["recommended_filename"] == (
        "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    )
    markdown = rendered["markdown"]
    assert markdown.startswith("# Hermes Northstar Testrepo Bootstrap Packet")
    assert "TASK_ID: GH-REVIEW-42-9001" in markdown
    assert "Target repo: ollehillbom1/northstar-agent-harness-testrepo" in markdown
    assert "Required exact approval:" in markdown
    assert (
        "ALLOW_TESTREPO_BOOTSTRAP=YES repo=ollehillbom1/northstar-agent-harness-testrepo"
        in markdown
    )
    assert "may_create_github_app: false" in markdown
    assert "may_configure_webhook: false" in markdown
    assert "may_push_or_pr: false" in markdown
    assert "may_start_server: false" in markdown
    assert "may_edit_erp: false" in markdown
    assert "GATE=WARN" in markdown


def test_renders_blocked_packet_as_fail_without_approval_phrase():
    approval_packet = build_bootstrap_approval_packet(
        _pipeline_result(),
        _bootstrap_profile(
            target_repo="ollehillbom1/north-star-erp",
            allowed_repos=["ollehillbom1/north-star-erp"],
        ),
    )

    rendered = render_bootstrap_packet_markdown(approval_packet)

    assert rendered["status"] == "BLOCKED"
    assert rendered["gate"] == "FAIL"
    assert rendered["side_effects"] == []
    assert "BLOCKED" in rendered["markdown"]
    assert "northstar_repo_not_allowed_for_testrepo_bootstrap" in rendered["markdown"]
    assert "Required exact approval:" not in rendered["markdown"]
    assert "GATE=FAIL" in rendered["markdown"]


def test_rejects_packets_that_already_claim_side_effects():
    rendered = render_bootstrap_packet_markdown(
        {
            "status": "READY_FOR_HUMAN_APPROVAL",
            "schema_version": "unsafe",
            "gate": "WARN",
            "TASK_ID": "BAD-1",
            "side_effects": ["created_github_app"],
        }
    )

    assert rendered["status"] == "BLOCKED"
    assert rendered["gate"] == "FAIL"
    assert rendered["block_reason"] == "input_packet_must_be_side_effect_free"
    assert rendered["side_effects"] == []
    assert "created_github_app" not in rendered["markdown"]


def test_cli_can_render_from_approval_gate_json_file(tmp_path):
    approval_packet = build_bootstrap_approval_packet(_pipeline_result(), _bootstrap_profile())
    input_path = tmp_path / "approval_packet.json"
    input_path.write_text(json.dumps(approval_packet), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "docs/hermes/pseudo_router/bootstrap_packet_renderer.py",
            str(input_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes Northstar Testrepo Bootstrap Packet" in result.stdout
    assert "GATE=WARN" in result.stdout
    assert result.stderr == ""
