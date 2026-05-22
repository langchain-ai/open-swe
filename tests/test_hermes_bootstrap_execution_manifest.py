import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_execution_manifest import (
    SCHEMA_VERSION,
    build_bootstrap_execution_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _writer_result(**overrides):
    result = {
        "status": "WROTE_DRY_RUN_PACKET",
        "schema_version": "hermes.bootstrap-packet-writer.v1",
        "gate": "PASS",
        "TASK_ID": "GH-REVIEW-42-9001",
        "relative_path": "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md",
        "absolute_path": str(ROOT / "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"),
        "bytes_written": 1329,
        "side_effects": ["wrote_markdown_file"],
    }
    result.update(overrides)
    return result


def test_builds_human_approval_checklist_from_valid_writer_result():
    manifest = build_bootstrap_execution_manifest(_writer_result(), repo_root=ROOT)

    assert manifest["status"] == "READY_FOR_HUMAN_APPROVAL_CHECKLIST"
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["gate"] == "WARN"
    assert manifest["TASK_ID"] == "GH-REVIEW-42-9001"
    assert manifest["source_packet"] == "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    assert manifest["side_effects"] == []
    assert manifest["hard_disabled_actions"] == {
        "may_create_github_app": False,
        "may_configure_webhook": False,
        "may_start_server": False,
        "may_push_or_pr": False,
        "may_deploy_prod": False,
    }
    assert manifest["required_exact_approval"] == "ALLOW_BOOTSTRAP_INSTALL=YES"
    assert any(
        "Create GitHub App" in step
        for step in manifest["manual_steps_requiring_allow_bootstrap_install"]
    )
    assert any(
        "Configure webhook" in step
        for step in manifest["manual_steps_requiring_allow_bootstrap_install"]
    )
    assert any(
        "Start Open SWE server" in step
        for step in manifest["manual_steps_requiring_allow_bootstrap_install"]
    )
    assert any(
        "Push branch or open PR" in step
        for step in manifest["manual_steps_requiring_allow_bootstrap_install"]
    )
    assert "GATE=WARN" in manifest["markdown"]
    assert "ALLOW_BOOTSTRAP_INSTALL=YES" in manifest["markdown"]
    assert "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md" in manifest["markdown"]


def test_blocks_writer_results_that_did_not_write_dry_run_packet():
    manifest = build_bootstrap_execution_manifest(
        _writer_result(status="BLOCKED", gate="FAIL", side_effects=[]), repo_root=ROOT
    )

    assert manifest["status"] == "BLOCKED"
    assert manifest["gate"] == "FAIL"
    assert manifest["block_reason"] == "requires_wrote_dry_run_packet_status"
    assert manifest["side_effects"] == []
    assert "ALLOW_BOOTSTRAP_INSTALL=YES" not in manifest.get("markdown", "")


def test_blocks_writer_results_with_unexpected_side_effects():
    manifest = build_bootstrap_execution_manifest(
        _writer_result(side_effects=["wrote_markdown_file", "created_github_app"]), repo_root=ROOT
    )

    assert manifest["status"] == "BLOCKED"
    assert manifest["gate"] == "FAIL"
    assert manifest["block_reason"] == "writer_result_has_unexpected_side_effects"
    assert manifest["side_effects"] == []
    assert "created_github_app" not in manifest.get("markdown", "")


def test_blocks_paths_outside_docs_bootstrap_or_erp_paths(tmp_path):
    unsafe_cases = [
        (
            {"relative_path": "docs/../BOOTSTRAP_PACKET.md"},
            "source_packet_must_stay_under_docs_bootstrap",
        ),
        (
            {"relative_path": "../erp/docs/bootstrap/BOOTSTRAP_PACKET.md"},
            "source_packet_must_stay_under_docs_bootstrap",
        ),
        ({"relative_path": "docs/bootstrap/packet.txt"}, "source_packet_must_be_markdown"),
        (
            {
                "relative_path": "docs/bootstrap/BOOTSTRAP_PACKET.md",
                "absolute_path": "/erp/docs/bootstrap/BOOTSTRAP_PACKET.md",
            },
            "source_packet_must_not_touch_erp",
        ),
        (
            {
                "relative_path": "docs/bootstrap/BOOTSTRAP_PACKET.md",
                "absolute_path": str(tmp_path / "outside/BOOTSTRAP_PACKET.md"),
            },
            "source_packet_absolute_path_must_match_repo_root",
        ),
    ]
    for overrides, reason in unsafe_cases:
        manifest = build_bootstrap_execution_manifest(_writer_result(**overrides), repo_root=ROOT)

        assert manifest["status"] == "BLOCKED"
        assert manifest["gate"] == "FAIL"
        assert manifest["block_reason"] == reason
        assert manifest["side_effects"] == []


def test_cli_outputs_manifest_json_without_writing_files(tmp_path):
    input_path = tmp_path / "writer_result.json"
    input_path.write_text(json.dumps(_writer_result()), encoding="utf-8")

    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/bootstrap_execution_manifest.py"),
            str(input_path),
            "--repo-root",
            str(ROOT),
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
    assert output["status"] == "READY_FOR_HUMAN_APPROVAL_CHECKLIST"
    assert output["side_effects"] == []
    assert before == after == ["writer_result.json"]
