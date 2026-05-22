import json
import subprocess
from pathlib import Path

from docs.hermes.pseudo_router.bootstrap_packet_renderer import render_bootstrap_packet_markdown
from docs.hermes.pseudo_router.bootstrap_packet_writer import (
    SCHEMA_VERSION,
    write_bootstrap_packet_markdown,
)
from docs.hermes.pseudo_router.testrepo_bootstrap_approval_gate import (
    build_bootstrap_approval_packet,
)

ROOT = Path(__file__).resolve().parents[1]


def _approval_packet():
    pipeline_result = {
        "status": "OK",
        "schema_version": "hermes.review-pipeline.v1",
        "gate": "WARN",
        "TASK_ID": "GH-REVIEW-42-9001",
        "final_decision": {
            "next_action": "START_QA_AGENT",
            "verification_required_before_pass": ["focused UAT/API smoke"],
        },
        "side_effects": [],
    }
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
    return build_bootstrap_approval_packet(pipeline_result, profile)


def _rendered_packet(**overrides):
    packet = render_bootstrap_packet_markdown(_approval_packet())
    packet.update(overrides)
    return packet


def test_writes_rendered_markdown_only_under_docs_bootstrap(tmp_path):
    rendered = _rendered_packet()

    result = write_bootstrap_packet_markdown(rendered, repo_root=tmp_path)

    expected_path = tmp_path / "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    assert result["status"] == "WROTE_DRY_RUN_PACKET"
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["gate"] == "PASS"
    assert result["relative_path"] == "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    assert result["absolute_path"] == str(expected_path)
    assert result["side_effects"] == ["wrote_markdown_file"]
    assert expected_path.read_text(encoding="utf-8") == rendered["markdown"]
    assert "Hermes Northstar Testrepo Bootstrap Packet" in expected_path.read_text(encoding="utf-8")


def test_requires_renderer_packet_schema_and_markdown(tmp_path):
    result = write_bootstrap_packet_markdown(_approval_packet(), repo_root=tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "requires_rendered_packet_json"
    assert result["side_effects"] == []
    assert not (tmp_path / "docs").exists()


def test_blocks_absolute_output_paths(tmp_path):
    rendered = _rendered_packet(recommended_filename="/tmp/BOOTSTRAP_PACKET.md")

    result = write_bootstrap_packet_markdown(rendered, repo_root=tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "output_path_must_be_relative"
    assert result["side_effects"] == []
    assert not Path("/tmp/BOOTSTRAP_PACKET.md").exists()


def test_blocks_erp_and_parent_escape_paths(tmp_path):
    for unsafe_path, reason in [
        ("/erp/docs/bootstrap/BOOTSTRAP_PACKET.md", "output_path_must_be_relative"),
        ("../erp/docs/bootstrap/BOOTSTRAP_PACKET.md", "output_path_must_stay_under_docs_bootstrap"),
        ("docs/../BOOTSTRAP_PACKET.md", "output_path_must_stay_under_docs_bootstrap"),
    ]:
        rendered = _rendered_packet(recommended_filename=unsafe_path)

        result = write_bootstrap_packet_markdown(rendered, repo_root=tmp_path)

        assert result["status"] == "BLOCKED"
        assert result["gate"] == "FAIL"
        assert result["block_reason"] == reason
        assert result["side_effects"] == []


def test_blocks_non_markdown_or_missing_output_filename(tmp_path):
    for unsafe_path, reason in [
        ("docs/bootstrap/packet.txt", "output_path_must_be_markdown"),
        ("", "missing_output_path"),
    ]:
        rendered = _rendered_packet(recommended_filename=unsafe_path)

        result = write_bootstrap_packet_markdown(rendered, repo_root=tmp_path)

        assert result["status"] == "BLOCKED"
        assert result["gate"] == "FAIL"
        assert result["block_reason"] == reason
        assert result["side_effects"] == []


def test_cli_writes_from_rendered_packet_json_inside_current_harness_root(tmp_path):
    rendered = _rendered_packet()
    input_path = tmp_path / "rendered_packet.json"
    input_path.write_text(json.dumps(rendered), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "docs/hermes/pseudo_router/bootstrap_packet_writer.py"),
            str(input_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    output = json.loads(result.stdout)
    written = tmp_path / "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    assert result.returncode == 0
    assert result.stderr == ""
    assert output["status"] == "WROTE_DRY_RUN_PACKET"
    assert output["relative_path"] == "docs/bootstrap/BOOTSTRAP_PACKET_GH-REVIEW-42-9001.md"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == rendered["markdown"]
