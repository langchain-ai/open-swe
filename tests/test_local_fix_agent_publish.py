from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

import local_fix_agent as lfa


def git_ok(repo: Path, *args: str) -> str:
    code, output = lfa.run_subprocess(["git", *args], repo)
    assert code == 0, output
    return output


def build_merge_conflict_repo(tmp_path: Path, rel_path: str, main_text: str, feature_text: str, base_text: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_ok(repo, "init")
    git_ok(repo, "config", "user.email", "tests@example.com")
    git_ok(repo, "config", "user.name", "Test User")
    git_ok(repo, "checkout", "-b", "main")
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "base")
    git_ok(repo, "checkout", "-b", "feature")
    target.write_text(feature_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "feature")
    git_ok(repo, "checkout", "main")
    target.write_text(main_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "main")
    code, _ = lfa.run_subprocess(["git", "merge", "feature"], repo)
    assert code != 0
    return repo


def build_rebase_conflict_repo(tmp_path: Path, rel_path: str, main_text: str, feature_text: str, base_text: str) -> Path:
    repo = tmp_path / "repo_rebase"
    repo.mkdir()
    git_ok(repo, "init")
    git_ok(repo, "config", "user.email", "tests@example.com")
    git_ok(repo, "config", "user.name", "Test User")
    git_ok(repo, "checkout", "-b", "main")
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "base")
    git_ok(repo, "checkout", "-b", "feature")
    target.write_text(feature_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "feature")
    git_ok(repo, "checkout", "main")
    target.write_text(main_text)
    git_ok(repo, "add", "--", rel_path)
    git_ok(repo, "commit", "-m", "main")
    git_ok(repo, "checkout", "feature")
    code, _ = lfa.run_subprocess(["git", "rebase", "main"], repo)
    assert code != 0
    return repo


def make_preflight(**overrides: object) -> dict:
    data = {
        "branch": "feature",
        "transport": "ssh",
        "gh_available": True,
        "gh_auth": True,
        "ssh_auth": True,
        "origin_url": "git@github.com:octocat/demo.git",
        "origin_owner": "octocat",
        "origin_repo": "demo",
        "current_user": "octocat",
        "requires_fork": False,
        "upstream_present": False,
        "upstream_url": "",
        "upstream_owner": "",
        "upstream_repo": "",
    }
    data.update(overrides)
    return data


def run_successful_main_publish_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    salvaged_tool_call: bool = False,
    use_real_post_success_publish: bool = False,
) -> dict:
    repo = tmp_path / ("repo_salvaged" if salvaged_tool_call else "repo")
    repo.mkdir(parents=True)
    (repo / "tool.py").write_text("print('ok')\n")
    artifact_dir = repo / ".artifacts"
    artifact_dir.mkdir()

    monkeypatch.setattr(sys, "argv", ["local_fix_agent.py", "--repo", str(repo), "--test-cmd", "pytest -q"])
    monkeypatch.setattr(
        lfa,
        "resolve_run_settings",
        lambda args, require_test_cmd: (
            repo,
            "pytest -q",
            1,
            4000,
            "fix",
            "explicit",
            repo / "config.json",
            repo / "recent.json",
            {},
            "",
        ),
    )
    monkeypatch.setattr(lfa, "configure_execution_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "configure_subprocess_safety", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "load_agent_config", lambda *args, **kwargs: ({}, repo / "config.json"))
    monkeypatch.setattr(lfa, "configure_publish_ignore_paths", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        lfa,
        "select_pattern_repo",
        lambda *args, **kwargs: {"selected": "none", "reason": "test", "confidence": "high", "path": None},
    )
    monkeypatch.setattr(lfa, "sync_with_upstream_before_workflow", lambda *args, **kwargs: lfa.make_upstream_sync_result())
    monkeypatch.setattr(lfa, "maybe_handle_merge_conflicts", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "ensure_branch_per_run", lambda current_repo: "agent-run-test")
    monkeypatch.setattr(lfa, "load_pattern_memory", lambda current_repo: {})
    monkeypatch.setattr(lfa, "create_run_artifact_dir", lambda current_repo: artifact_dir)
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "build_system_prompt", lambda *args, **kwargs: "system")
    monkeypatch.setattr(lfa, "build_user_prompt", lambda *args, **kwargs: "user")
    monkeypatch.setattr(lfa, "progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "startup_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "summarize_run_metrics", lambda metrics: "summary")
    monkeypatch.setattr(lfa, "append_run_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "analyze_run_comparison", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "build_action_summary", lambda *args, **kwargs: ("", "", "", ""))
    monkeypatch.setattr(lfa, "write_run_artifacts", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: repo / "recent.json")
    monkeypatch.setattr(lfa, "format_run_artifact_summary", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "print_post_success_publish_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "print_publish_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "print_merge_conflict_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "format_final_operator_summary", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "publish_summary_requires_failure", lambda summary: False)
    monkeypatch.setattr(lfa, "should_track_modified_file", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "save_pattern_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "extract_diagnosis_explanation", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "extract_diff_reasoning", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "extract_edit_plan", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "extract_edit_scope", lambda *args, **kwargs: "")
    monkeypatch.setattr(lfa, "extract_test_alignment", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        lfa,
        "extract_pseudo_tool_call",
        lambda text: ("run_shell", "{}") if salvaged_tool_call else None,
    )

    tool_calls = []
    if not salvaged_tool_call:
        tool_calls = [
            SimpleNamespace(
                id="tool-1",
                type="function",
                function=SimpleNamespace(name="run_shell", arguments="{}"),
            )
        ]
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="run_shell({})" if salvaged_tool_call else "",
                    tool_calls=tool_calls,
                )
            )
        ]
    )
    monkeypatch.setattr(lfa, "call_model", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        lfa,
        "handle_tool",
        lambda repo_path, max_file_chars, tool_name, tool_args, tests_passed: json.dumps(
            {"ok": True, "output": "ok", "command": "pytest -q"}
        )
        if tool_name == "run_shell"
        else json.dumps({"ok": True}),
    )

    captured: dict[str, object] = {"publish_calls": 0}

    def fake_finalize_success(
        repo_path: Path,
        max_file_chars: int,
        messages,
        tests_passed: bool,
        attempt_number: int,
        test_cmd: str,
        failure_context: dict,
        primary_file: str,
        best_attempt: dict | None,
        current_strategy_type: str,
        dry_run: bool,
        show_diff: bool,
        mode: str,
        publish_requested: bool,
        publish_message: str,
    ) -> dict:
        captured["finalize_publish_requested"] = publish_requested
        return {
            "committed": False,
            "rejected": False,
            "output": "publish mode: commit deferred",
            "candidate_results": [],
            "chosen_candidate": "current_patch",
            "changed_paths": ["local_fix_agent.py"],
            "confidence_level": "HIGH",
        }

    def fake_run_post_success_publish(
        repo_path: Path,
        test_cmd: str,
        attempt_number: int,
        confidence_level: str,
        artifact_dir_path: Path | None,
        changed_paths: list[str],
        publish_branch: str,
        publish_pr: bool,
        publish_merge: bool,
        publish_merge_local_main: bool,
        publish_message: str,
        target: str,
        blocked_reason: str | None,
        baseline_paths: list[str],
        dry_run_mode: bool,
        publish_mode: str,
        validation_succeeded: bool,
        publish_requested: bool,
        **kwargs,
    ) -> dict:
        captured["publish_calls"] = int(captured["publish_calls"]) + 1
        captured["run_post_success_publish_requested"] = publish_requested
        captured["baseline_paths"] = baseline_paths
        captured["changed_paths"] = changed_paths
        captured["publish_mode"] = publish_mode
        return {
            "validation_result": "success",
            "publish_requested": publish_requested,
            "publish_triggered": True,
            "publish_mode": publish_mode,
            "publish_result": "success",
            "publish_reason": "validated",
            "publish_result_detail": {"final": {"status": "success"}},
        }

    monkeypatch.setattr(lfa, "finalize_success", fake_finalize_success)
    if use_real_post_success_publish:
        def fake_publish_validated_run(
            repo_path: Path,
            test_cmd: str,
            attempt_number: int,
            confidence_level: str,
            artifact_dir_path: Path | None,
            changed_paths: list[str],
            publish_branch: str,
            publish_pr: bool,
            publish_merge: bool,
            publish_merge_local_main: bool,
            publish_message: str,
            target: str,
            blocked_reason: str | None,
            baseline_paths: list[str],
            dry_run_mode: bool,
            publish_current_mode: bool = False,
            validation_state: str = "success",
            force_publish: bool = False,
            auto_stage_safe_paths: bool = True,
            auto_remediate_blockers: bool = True,
            explain_staging: bool = False,
        ) -> dict:
            captured["publish_validated_run_calls"] = int(captured.get("publish_validated_run_calls", 0)) + 1
            captured["publish_validated_run_changed_paths"] = changed_paths
            captured["publish_validated_run_baseline_paths"] = baseline_paths
            captured["publish_validated_run_validation_state"] = validation_state
            return {
                "published": True,
                "publish_scope": "validated_run",
                "triggered": True,
                "validation_state": validation_state,
                "publish_reason": "validated",
                "docs_checked_at_publish": True,
                "docs_check_performed": True,
                "docs_required": True,
                "docs_updated": True,
                "docs_refresh_mode": "patch",
                "docs_targets": ["README.md"],
                "final": {"status": "success"},
                "verification": {"reason": ""},
            }

        monkeypatch.setattr(lfa, "publish_validated_run", fake_publish_validated_run)
    else:
        monkeypatch.setattr(lfa, "run_post_success_publish", fake_run_post_success_publish)

    lfa.main()
    return captured


def test_build_publish_preflight_detects_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lfa, "origin_remote_url", lambda repo: "https://github.com/octocat/demo")
    monkeypatch.setattr(lfa, "parse_remote_names", lambda repo: ["origin"])
    monkeypatch.setattr(lfa, "github_cli_available", lambda repo: True)
    monkeypatch.setattr(lfa, "github_auth_status", lambda repo: (True, "Logged in to github.com account test-user"))
    monkeypatch.setattr(lfa, "detect_current_github_user", lambda repo, auth_output="": "test-user")
    monkeypatch.setattr(lfa, "probe_github_ssh_auth", lambda: (True, "authenticated"))

    preflight = lfa.build_publish_preflight(Path("/tmp/repo"), "feature")

    assert preflight["transport"] == "https"
    assert preflight["origin_owner"] == "octocat"
    assert preflight["origin_repo"] == "demo"


def test_resolve_publish_target_same_owner_uses_origin() -> None:
    target = lfa.resolve_publish_target(make_preflight(), {})

    assert target["type"] == "origin"
    assert target["repo"] == "octocat/demo"


def test_resolve_publish_target_different_owner_uses_fork() -> None:
    target = lfa.resolve_publish_target(
        make_preflight(
            origin_url="git@github.com:upstream/demo.git",
            origin_owner="upstream",
            current_user="contributor",
            requires_fork=True,
        ),
        {},
    )

    assert target["type"] == "fork"
    assert target["repo"] == "contributor/demo"


def test_resolve_publish_target_missing_auth_marks_auth_blocked() -> None:
    target = lfa.resolve_publish_target(
        make_preflight(
            transport="https",
            gh_available=False,
            gh_auth=False,
            ssh_auth=False,
            origin_url="https://github.com/octocat/demo.git",
            current_user="",
        ),
        {},
    )

    assert target["type"] == "origin"
    assert target["reason"] == "auth_blocked"


def test_prepare_publish_target_rewrites_https_to_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    result = lfa.make_publish_result()
    result["preflight"] = make_preflight(
        transport="https",
        origin_url="https://github.com/octocat/demo",
    )
    result["target"] = {
        "type": "origin",
        "remote_name": "origin",
        "repo": "octocat/demo",
        "transport": "https",
        "url": "https://github.com/octocat/demo",
        "requires_fork": False,
        "reason": "authenticated user owns origin",
    }
    monkeypatch.setattr(lfa, "set_origin_remote_url", lambda repo, new_url: (True, ""))

    ok, reason, next_action = lfa.prepare_publish_target(Path("/tmp/repo"), result)

    assert ok is True
    assert reason == ""
    assert next_action == ""
    assert result["target"]["url"] == "git@github.com:octocat/demo.git"
    assert result["actions"] == ["preflight origin https->ssh"]


def test_classify_publishable_changes_ignores_known_state_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (
            0,
            " M .ai_publish_state.json\n M .fix_agent_docs_state.json\n M .fix_agent_recent.json\n",
        ),
    )

    result = lfa.classify_publishable_changes(Path("/tmp/repo"))

    assert result["meaningful_changes_detected"] is False
    assert result["meaningful_paths"] == []
    assert result["ignored_changes"] == [
        ".ai_publish_state.json",
        ".fix_agent_docs_state.json",
        ".fix_agent_recent.json",
    ]


def test_extract_status_path_preserves_leading_dot_for_staged_files() -> None:
    assert lfa.extract_status_path("M .ai_publish_state.json") == ".ai_publish_state.json"


def test_classify_publishable_changes_treats_docs_code_and_tests_as_meaningful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (
            0,
            " M README.md\n M local_fix_agent.py\n M tests/test_local_fix_agent_publish.py\n M .ai_publish_state.json\n",
        ),
    )

    result = lfa.classify_publishable_changes(Path("/tmp/repo"))

    assert result["meaningful_changes_detected"] is True
    assert result["meaningful_paths"] == [
        "README.md",
        "local_fix_agent.py",
        "tests/test_local_fix_agent_publish.py",
    ]
    assert result["ignored_changes"] == [".ai_publish_state.json"]


def test_classify_publish_path_marks_state_file_as_ignored() -> None:
    result = lfa.classify_publish_path(".ai_publish_state.json")

    assert result["file_type"] == "state"
    assert result["classification_source"] == "explicit_ignore"
    assert result["publishable"] is False
    assert result["publish_reason"] == "internal state file"


def test_classify_publish_path_marks_unknown_text_artifact_as_non_publishable() -> None:
    result = lfa.classify_publish_path("c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt")

    assert result["file_type"] == "artifact"
    assert result["classification_source"] in {"pattern_match", "extension"}
    assert result["publishable"] is False
    assert result["publish_reason"] == "generated/artifact file"


def test_classify_publishable_changes_uses_last_published_commit_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_subprocess(command, cwd, shell=False):
        if command[:3] == ["git", "status", "--short"]:
            return 0, ""
        if command[:3] == ["git", "diff", "--name-status"]:
            return 0, "M\tREADME.md\nM\tlocal_fix_agent.py\nM\t.ai_publish_state.json\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.classify_publishable_changes(Path("/tmp/repo"), baseline_commit="abc123", current_commit="def456")

    assert result["last_published_commit"] == "abc123"
    assert result["current_commit"] == "def456"
    assert result["diff_files_detected"] == ["README.md", "local_fix_agent.py", ".ai_publish_state.json"]
    assert result["meaningful_changes_detected"] is True
    assert result["meaningful_paths"] == ["README.md", "local_fix_agent.py"]
    assert result["ignored_changes"] == [".ai_publish_state.json"]


def test_publish_change_helpers_ignore_known_state_files_consistently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (
            0,
            " M .ai_publish_state.json\n M .fix_agent_docs_state.json\n",
        ),
    )

    assert lfa.meaningful_changed_paths(Path("/tmp/repo"), ignore_path_predicate=lfa.is_publish_ignored_change_path) == []
    working_tree = lfa.classify_git_working_tree(Path("/tmp/repo"), ignore_path_predicate=lfa.is_publish_ignored_change_path)

    assert working_tree["clean"] is True
    assert working_tree["has_staged"] is False
    assert working_tree["has_unstaged"] is False
    assert working_tree["has_untracked"] is False


def test_filtered_git_status_output_preserves_leading_status_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (
            0,
            " M local_fix_agent.py\n M README.md\n",
        ),
    )

    status_output = lfa.filtered_git_status_output(Path("/tmp/repo"), ignore_all_ignored_dirs=True)

    assert status_output == " M local_fix_agent.py\n M README.md"


def test_classify_git_working_tree_treats_first_line_unstaged_file_as_unstaged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (
            0,
            " M local_fix_agent.py\n M README.md\n",
        ),
    )

    working_tree = lfa.classify_git_working_tree(Path("/tmp/repo"), ignore_path_predicate=lfa.is_publish_ignored_change_path)

    assert working_tree["clean"] is False
    assert working_tree["has_staged"] is False
    assert working_tree["has_unstaged"] is True
    assert working_tree["staged_paths"] == []
    assert working_tree["unstaged_paths"] == ["local_fix_agent.py", "README.md"]


def test_normalize_publish_working_tree_audit_prefers_cached_diff_for_staged_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path("/tmp/repo")

    def fake_run_subprocess(command, cwd, shell=False):
        if command[:4] == ["git", "diff", "--cached", "--name-only"]:
            return 0, "docs/RUNBOOK.md\nlocal_fix_agent.py\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "publish_meaningful_changed_paths",
        lambda current_repo: ["README.md", "docs/RUNBOOK.md", "local_fix_agent.py"],
    )

    audit = lfa.normalize_publish_working_tree_audit(
        repo,
        {
            "has_staged": True,
            "staged_paths": [],
            "has_unstaged": True,
            "unstaged_paths": ["README.md"],
            "has_untracked": False,
            "untracked_paths": [],
            "status_output": "M  docs/RUNBOOK.md\nM  local_fix_agent.py\n M README.md\n",
        },
        ["README.md", "docs/RUNBOOK.md", "local_fix_agent.py"],
        publish_current_mode=True,
    )

    assert audit["staged_paths"] == ["docs/RUNBOOK.md", "local_fix_agent.py"]
    assert audit["remaining_paths"] == ["README.md"]


def test_meaningful_content_fingerprint_excludes_ignored_state_files(tmp_path: Path) -> None:
    (tmp_path / ".ai_publish_state.json").write_text('{"last_success": true}\n')
    publish_changes = {
        "status_output": " M .ai_publish_state.json",
        "meaningful_paths": [],
        "ignored_changes": [".ai_publish_state.json"],
    }

    assert lfa.compute_meaningful_content_fingerprint(tmp_path, publish_changes) == ""


def test_meaningful_content_fingerprint_stable_across_equivalent_formatting(tmp_path: Path) -> None:
    script = tmp_path / "tool.py"
    publish_changes = {
        "status_output": " M tool.py",
        "meaningful_paths": ["tool.py"],
        "ignored_changes": [],
    }
    script.write_bytes(b"def run():  \r\n    return 1\r\n")
    before = lfa.compute_meaningful_content_fingerprint(tmp_path, publish_changes)

    script.write_bytes(b"def run():\n    return 1\n")
    after = lfa.compute_meaningful_content_fingerprint(tmp_path, publish_changes)

    assert before == after


def test_resolve_publish_validation_state_reuses_success_on_fingerprint_match(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_recent_state",
        lambda: {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "validation_command": "pytest -q",
                    "commit_hash": "old123",
                    "validation_result": "success",
                    "meaningful_content_fingerprint": "fp-123",
                    "ts": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "new456")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "fp-123")

    result = lfa.resolve_publish_validation_state(repo)

    assert result["validation_state"] == "success"
    assert result["validation_commit_match"] is False
    assert result["fingerprint_match"] is True
    assert result["reason"] == "validated_reused_fingerprint"


def test_attempt_publish_auto_revalidation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
        "meaningful_changes_detected": True,
        "meaningful_paths": ["local_fix_agent.py"],
        "ignored_changes": [],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "mismatch",
    }
    monkeypatch.setattr(
        lfa,
        "load_recent_state",
        lambda: {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "validation_command": "pytest -q",
                    "commit_hash": "old123",
                    "validation_result": "success",
                    "ts": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (0, "ok"))
    captured: dict[str, object] = {}

    def fake_update_recent_state(current_repo, test_cmd, mode, success, artifact_dir=None, target="", files_changed=None, confidence="", blocked_reason=""):
        captured["success"] = success
        captured["test_cmd"] = test_cmd
        return Path("/tmp/state.json")

    monkeypatch.setattr(lfa, "update_recent_state", fake_update_recent_state)
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "validation_state": "success",
            "validation_result": "success",
            "validation_commit_match": True,
            "last_validated_commit": "new456",
            "current_commit": "new456",
            "validation_age_seconds": 0,
            "reason": "validated",
        },
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial)

    assert result["validation_state"] == "success"
    assert result["auto_revalidated"] is True
    assert result["validation_reused"] is False
    assert result["auto_revalidation_result"] == "success"
    assert captured["success"] == "success"
    assert captured["test_cmd"] == "pytest -q"


def test_attempt_publish_auto_revalidation_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
        "meaningful_changes_detected": True,
        "meaningful_paths": ["local_fix_agent.py"],
        "ignored_changes": [],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "mismatch",
    }
    monkeypatch.setattr(
        lfa,
        "load_recent_state",
        lambda: {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "validation_command": "pytest -q",
                    "commit_hash": "old123",
                    "validation_result": "success",
                    "ts": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (1, "tests failed"))
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: Path("/tmp/state.json"))
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "validation_state": "failed",
            "validation_result": "failed",
            "validation_commit_match": True,
            "last_validated_commit": "new456",
            "current_commit": "new456",
            "validation_age_seconds": 0,
            "reason": "failed",
        },
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial)

    assert result["validation_state"] == "failed"
    assert result["auto_revalidated"] is True
    assert result["validation_reused"] is False
    assert result["auto_revalidation_result"] == "failed"
    assert "auto-revalidation failed" in result["reason"]


def test_attempt_publish_auto_revalidation_no_auto_revalidate_preserves_block(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
        "meaningful_changes_detected": True,
        "meaningful_paths": ["local_fix_agent.py"],
        "ignored_changes": [],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "mismatch",
    }
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auto revalidation should not run")),
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial, no_auto_revalidate=True)

    assert result["validation_state"] == "blocked"
    assert result["auto_revalidated"] is False
    assert result["validation_reused"] is False
    assert result["auto_revalidation_result"] == "not_needed"


def test_attempt_publish_auto_revalidation_reuses_validation_on_fingerprint_match(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "success",
        "validation_result": "success",
        "validation_commit_match": False,
        "fingerprint_match": True,
        "meaningful_changes_detected": True,
        "meaningful_paths": ["local_fix_agent.py"],
        "ignored_changes": [],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "validated_reused_fingerprint",
    }
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("revalidation should not run for fingerprint-equivalent content")),
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial)

    assert result["validation_state"] == "success"
    assert result["validation_reused"] is True
    assert result["auto_revalidated"] is False
    assert result["auto_revalidation_result"] == "not_needed"
    assert result["publish_reason"] == "validated_reused_fingerprint"


def test_attempt_publish_auto_revalidation_reuses_validation_when_only_ignored_changes_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
        "meaningful_changes_detected": False,
        "meaningful_paths": [],
        "ignored_changes": [".ai_publish_state.json"],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "mismatch",
    }
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("revalidation should not run for ignored-only differences")),
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial)

    assert result["validation_state"] == "success"
    assert result["validation_result"] == "success"
    assert result["validation_reused"] is True
    assert result["auto_revalidated"] is False
    assert result["auto_revalidation_result"] == "not_needed"
    assert result["publish_reason"] == "validated_reused_noop"


def test_attempt_publish_auto_revalidation_blocks_if_commit_changes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
        "meaningful_changes_detected": True,
        "meaningful_paths": ["local_fix_agent.py"],
        "ignored_changes": [],
        "last_validated_commit": "old123",
        "current_commit": "new456",
        "validation_age_seconds": 10,
        "reason": "mismatch",
    }
    monkeypatch.setattr(
        lfa,
        "load_recent_state",
        lambda: {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "validation_command": "pytest -q",
                    "commit_hash": "old123",
                    "validation_result": "success",
                    "ts": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (0, "ok"))
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: Path("/tmp/state.json"))
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "validation_state": "success",
            "validation_result": "success",
            "validation_commit_match": False,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_validated_commit": "new456",
            "current_commit": "new789",
            "validation_age_seconds": 0,
            "reason": "validated",
        },
    )

    result = lfa.attempt_publish_auto_revalidation(repo, initial)

    assert result["validation_state"] == "blocked"
    assert result["auto_revalidated"] is True
    assert result["auto_revalidation_result"] == "blocked"
    assert "changed again after the auto-revalidation attempt" in result["reason"]


def test_resolve_merge_conflicts_state_file_takes_theirs(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        ".ai_publish_state.json",
        json.dumps({"side": "main"}) + "\n",
        json.dumps({"side": "feature"}) + "\n",
        json.dumps({"side": "base"}) + "\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -c \"print('ok')\"")

    assert result["merge_conflicts_detected"] is True
    assert result["merge_result"] == "success"
    assert result["resolution_strategy_per_file"][".ai_publish_state.json"] == "take_theirs_state_file"
    assert (repo / ".ai_publish_state.json").read_text() == json.dumps({"side": "feature"}) + "\n"


def test_resolve_merge_conflicts_docs_trivial_merge(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        "README.md",
        "# Demo\n\nline one\nline two\nproxy support\n",
        "# Demo\n\nline one\nlogging notes\n",
        "# Demo\n\nbase line\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -c \"print('ok')\"")

    assert result["merge_result"] == "success"
    assert result["resolution_strategy_per_file"]["README.md"] == "prefer_newer_docs_content"
    assert result["conflict_explanations"]["README.md"]["file_type"] == "docs"
    assert "adds documentation content" in result["conflict_explanations"]["README.md"]["ours_summary"]
    assert "both sides edit the same documentation section" == result["conflict_explanations"]["README.md"]["conflict_reason"]
    assert "line two" in (repo / "README.md").read_text()


def test_resolve_merge_conflicts_code_conflict_resolved_and_validated(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        "app.py",
        "def run():\n    for attempt in range(3):\n        retry = True\n    return 1\n",
        "def run():\n    timeout = 5\n    return timeout\n",
        "def run():\n    return 0\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -m py_compile app.py")

    assert result["merge_conflicts_detected"] is True
    assert result["merge_result"] == "success"
    assert result["validation_result_after_merge"] == "success"
    assert result["resolution_strategy_per_file"]["app.py"] == "structured_merge_combined_logic"
    assert result["conflict_explanations"]["app.py"]["ours_summary"] == "adds retry logic"
    assert result["conflict_explanations"]["app.py"]["theirs_summary"] == "changes timeout handling"
    assert result["conflict_explanations"]["app.py"]["conflict_reason"] == "both sides edit the same code block"
    assert "auto-resolved merge conflicts with validation" in git_ok(repo, "log", "-1", "--pretty=%s")


def test_resolve_merge_conflicts_config_ambiguous_blocks(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        "settings.json",
        json.dumps({"timeout": 10, "mode": "fast"}, indent=2) + "\n",
        json.dumps({"timeout": 5, "mode": "safe"}, indent=2) + "\n",
        json.dumps({"timeout": 1}, indent=2) + "\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -c \"print('ok')\"")

    assert result["merge_conflicts_detected"] is True
    assert result["merge_result"] == "blocked"
    assert result["resolution_strategy_per_file"]["settings.json"] == "blocked_ambiguous_config_conflict"
    assert result["conflict_explanations"]["settings.json"]["ours_summary"] == "changes config keys: timeout, mode"
    assert result["conflict_explanations"]["settings.json"]["theirs_summary"] == "changes config keys: timeout, mode"
    assert result["conflict_explanations"]["settings.json"]["conflict_reason"] == "both sides modify the same config block differently"


def test_resolve_merge_conflicts_code_conflict_ambiguous_blocks(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        "broken.py",
        "def run():\n    if True:\n",
        "def run():\n    return 1\n",
        "def run():\n    return 0\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -m py_compile broken.py")

    assert result["merge_conflicts_detected"] is True
    assert result["merge_result"] == "blocked"
    assert result["resolution_strategy_per_file"]["broken.py"] == "blocked_ambiguous_code_conflict"


def test_resolve_merge_conflicts_strict_mode_blocks_without_attempting_resolution(tmp_path: Path) -> None:
    repo = build_merge_conflict_repo(
        tmp_path,
        "app.py",
        "def run():\n    value = 2\n    return value\n",
        "def run():\n    note = 'ok'\n    return 1\n",
        "def run():\n    return 0\n",
    )

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -m py_compile app.py", no_auto_merge_conflicts=True)

    assert result["merge_conflicts_detected"] is True
    assert result["merge_result"] == "blocked"
    assert result["validation_result_after_merge"] == "not_run"
    assert result["resolution_strategy_per_file"]["app.py"] == "strict_mode_blocked"


def test_print_merge_conflict_summary_blocked_includes_manual_section(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lfa.sys, "argv", ["local_fix_agent.py", "--repo", "/tmp/demo", "--publish-only"])

    lfa.print_merge_conflict_summary(
        {
            "sync_operation_attempted": True,
            "sync_operation": "merge",
            "merge_conflicts_detected": True,
            "conflict_source": "git_merge",
            "auto_conflict_resolution_attempted": True,
            "conflicted_files": ["settings.json"],
            "resolution_strategy_per_file": {"settings.json": "blocked_ambiguous_config_conflict"},
            "validation_result_after_merge": "not_run",
            "merge_result": "blocked",
            "blocked_reason": "config conflict is not clearly compatible",
            "git_sequence_state": "merge",
            "conflict_explanations": {
                "settings.json": {
                    "file": "settings.json",
                    "file_type": "config",
                    "hunk_count": 1,
                    "ours_summary": "changes config keys: timeout, mode",
                    "theirs_summary": "changes config keys: timeout, mode",
                    "conflict_reason": "both sides modify the same config block differently",
                    "suggested_resolution": "merge both only if the resulting config keeps the intended keys and values compatible",
                    "hunks": [],
                }
            },
        }
    )

    output = capsys.readouterr().out
    assert "=== MANUAL MERGE REQUIRED ===" in output
    assert "=== CONFLICT EXPLANATION ===" in output
    assert "conflicted_files: ['settings.json']" in output
    assert "file: settings.json" in output
    assert "file_type: config" in output
    assert "ambiguous config conflict; both sides modify the same settings differently" in output
    assert "ours_summary: changes config keys: timeout, mode" in output
    assert "theirs_summary: changes config keys: timeout, mode" in output
    assert "conflict_reason: both sides modify the same config block differently" in output
    assert "hint: use ours if local settings are known-good; use theirs if upstream defaults must win; merge both only if the combined config remains compatible" in output
    assert "git diff --name-only --diff-filter=U" in output
    assert "<editor> settings.json" in output
    assert "git checkout --ours -- settings.json" in output
    assert "git checkout --theirs -- settings.json" in output
    assert "git add settings.json" in output
    assert "complete_merge: git commit" in output
    assert "Resume:" in output
    assert "python local_fix_agent.py --repo /tmp/demo --publish-only" in output


def test_maybe_handle_merge_conflicts_blocked_disables_further_automation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa.sys, "argv", ["local_fix_agent.py", "--repo", str(repo), "--test-cmd", "pytest -q"])
    monkeypatch.setattr(
        lfa,
        "resolve_merge_conflicts",
        lambda current_repo, validation_command="", no_auto_merge_conflicts=False: {
            "merge_conflicts_detected": True,
            "conflicted_files": ["broken.py"],
            "resolution_strategy_per_file": {"broken.py": "blocked_ambiguous_code_conflict"},
            "validation_result_after_merge": "failed",
            "merge_result": "blocked",
            "blocked_reason": "structured code merge remained syntactically invalid",
            "commit_sha": "",
            "sync_operation_attempted": False,
            "sync_operation": "none",
            "conflict_source": "merge",
            "auto_conflict_resolution_attempted": True,
            "git_sequence_state": "merge",
        },
    )

    outcome = lfa.maybe_handle_merge_conflicts(
        repo,
        validation_command="pytest -q",
        publish_requested=False,
        publish_mode="validated-run",
        publish_branch="",
        publish_pr=False,
        publish_merge=False,
        publish_merge_local_main=False,
        publish_message="",
        target="",
        dry_run_mode=False,
        force_publish=False,
    )

    output = capsys.readouterr().out
    assert outcome["handled"] is True
    assert outcome["success"] is False
    assert outcome["continue_with_repair"] is False
    assert "=== MANUAL MERGE REQUIRED ===" in output
    assert "file: broken.py" in output
    assert "file_type: code" in output
    assert "ours_summary: adds retry logic" not in output
    assert "hint: use ours if local logic is known-good; use theirs if the upstream fix should win; merge both if each side adds valid complementary logic" in output
    assert "Resume:" in output
    assert "python local_fix_agent.py" in output


def test_print_manual_merge_required_uses_rebase_continue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lfa.sys, "argv", ["local_fix_agent.py", "--repo", "/tmp/rebase-demo"])

    lfa.print_manual_merge_required(
        {
            "conflicted_files": ["app.py"],
            "resolution_strategy_per_file": {"app.py": "blocked_ambiguous_code_conflict"},
            "blocked_reason": "structured code merge remained syntactically invalid",
            "git_sequence_state": "rebase",
        }
    )

    output = capsys.readouterr().out
    assert "complete_merge: git rebase --continue" in output


def test_print_manual_merge_required_uses_cherry_pick_continue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lfa.sys, "argv", ["local_fix_agent.py", "--repo", "/tmp/cherry-demo"])

    lfa.print_manual_merge_required(
        {
            "conflicted_files": ["README.md"],
            "resolution_strategy_per_file": {"README.md": "blocked_unknown_conflict"},
            "blocked_reason": "unknown conflicted file type requires manual resolution",
            "git_sequence_state": "cherry_pick",
        }
    )

    output = capsys.readouterr().out
    assert "complete_merge: git cherry-pick --continue" in output


def test_resolve_merge_conflicts_rebase_continue_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = build_rebase_conflict_repo(
        tmp_path,
        "app.py",
        "def run():\n    value = 2\n    return value\n",
        "def run():\n    note = 'ok'\n    return 1\n",
        "def run():\n    return 0\n",
    )
    original = lfa.run_subprocess

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "rebase", "--continue"]:
            return 0, "continued"
        return original(command, cwd, shell=shell)

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.resolve_merge_conflicts(repo, validation_command="python -m py_compile app.py")

    assert result["merge_result"] == "success"
    assert result["git_sequence_state"] == "rebase"


def test_maybe_handle_merge_conflicts_publish_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        lfa,
        "resolve_merge_conflicts",
        lambda current_repo, validation_command="", no_auto_merge_conflicts=False: {
            "merge_conflicts_detected": True,
            "conflicted_files": ["app.py"],
            "resolution_strategy_per_file": {"app.py": "structured_merge_combined_logic"},
            "validation_result_after_merge": "success",
            "merge_result": "success",
            "blocked_reason": "",
            "commit_sha": "abc123",
        },
    )
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: Path("/tmp/state.json"))

    def fake_run_post_success_publish(*args, **kwargs):
        captured["called"] = True
        return {
            "validation_result": "success",
            "validation_state": "success",
            "publish_requested": True,
            "publish_triggered": False,
            "publish_result": "noop",
        }

    monkeypatch.setattr(lfa, "run_post_success_publish", fake_run_post_success_publish)
    monkeypatch.setattr(lfa, "format_final_operator_summary", lambda summary: "FINAL: ok")

    outcome = lfa.maybe_handle_merge_conflicts(
        repo,
        validation_command="pytest -q",
        publish_requested=True,
        publish_mode="validated-run",
        publish_branch="",
        publish_pr=False,
        publish_merge=False,
        publish_merge_local_main=False,
        publish_message="",
        target="",
        dry_run_mode=False,
        force_publish=False,
    )

    assert outcome["handled"] is True
    assert outcome["success"] is True
    assert captured["called"] is True


def test_run_sync_operation_with_conflict_hook_auto_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (1, "conflict") if command == ["git", "pull", "--ff-only", "origin", "main"] else (0, ""))
    monkeypatch.setattr(lfa, "conflicted_git_paths", lambda current_repo: ["app.py"])
    monkeypatch.setattr(
        lfa,
        "resolve_merge_conflicts",
        lambda current_repo, validation_command="", no_auto_merge_conflicts=False: {
            "merge_conflicts_detected": True,
            "conflicted_files": ["app.py"],
            "resolution_strategy_per_file": {"app.py": "structured_merge_combined_logic"},
            "validation_result_after_merge": "success",
            "merge_result": "success",
            "blocked_reason": "",
            "commit_sha": "abc123",
            "auto_conflict_resolution_attempted": True,
        },
    )

    ok, _, result = lfa.run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="pull",
        command=["git", "pull", "--ff-only", "origin", "main"],
        validation_command="pytest -q",
    )

    assert ok is True
    assert result["sync_operation_attempted"] is True
    assert result["sync_operation"] == "pull"
    assert result["conflict_source"] == "pull"
    assert result["merge_result"] == "success"


def test_run_sync_operation_with_conflict_hook_strict_mode_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa, "run_subprocess", lambda command, cwd, shell=False: (1, "conflict"))
    monkeypatch.setattr(lfa, "conflicted_git_paths", lambda current_repo: ["app.py"])
    monkeypatch.setattr(
        lfa,
        "resolve_merge_conflicts",
        lambda current_repo, validation_command="", no_auto_merge_conflicts=False: {
            "merge_conflicts_detected": True,
            "conflicted_files": ["app.py"],
            "resolution_strategy_per_file": {"app.py": "strict_mode_blocked"},
            "validation_result_after_merge": "not_run",
            "merge_result": "blocked",
            "blocked_reason": "strict",
            "commit_sha": "",
            "auto_conflict_resolution_attempted": False,
        },
    )

    ok, reason, result = lfa.run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="branch_sync",
        command=["git", "pull", "--ff-only", "origin", "main"],
        validation_command="pytest -q",
        no_auto_conflict_resolution_after_sync=True,
    )

    assert ok is False
    assert reason == "strict"
    assert result["sync_operation"] == "branch_sync"
    assert result["merge_result"] == "blocked"


def test_sync_with_upstream_before_workflow_no_upstream(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_ok(repo, "init")

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["upstream_detected"] is False
    assert result["origin_detected"] is False
    assert result["sync_attempted"] is False
    assert result["sync_result"] == "not_needed"


def test_sync_with_upstream_before_workflow_dirty_tree_blocks_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    commands: list[object] = []

    def fake_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_pre_task_working_tree",
        lambda current_repo: {
            "working_tree_detected": True,
            "working_tree_clean": False,
            "working_tree_status": " M app.py\n?? notes.txt",
            "dirty_paths": ["app.py", "notes.txt"],
            "auto_stage_attempted": False,
            "auto_staged_paths": [],
            "auto_removed_paths": [],
            "remaining_unstaged_paths": ["app.py", "notes.txt"],
            "working_tree_blockers": [
                {"path": "app.py", "file_type": "code", "reason": "publishable file requires manual review before staging"},
                {"path": "notes.txt", "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"},
            ],
            "working_tree_summary": "Uncommitted changes detected. Blocked due to 2 ambiguous files requiring manual review.",
        },
    )
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "blocked"
    assert "ambiguous changes requiring manual review" in result["reason"]
    assert result["dirty_paths"] == ["app.py", "notes.txt"]
    assert result["working_tree_blockers"] == [
        {"path": "app.py", "file_type": "code", "reason": "publishable file requires manual review before staging"},
        {"path": "notes.txt", "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"},
    ]
    assert commands == []


def test_sync_with_upstream_before_workflow_auto_stages_safe_changes_and_proceeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_pre_task_working_tree",
        lambda current_repo: {
            "working_tree_detected": True,
            "working_tree_clean": False,
            "working_tree_status": "M  local_fix_agent.py",
            "dirty_paths": ["local_fix_agent.py"],
            "auto_stage_attempted": True,
            "auto_staged_paths": ["local_fix_agent.py"],
            "auto_removed_paths": [],
            "remaining_unstaged_paths": [],
            "working_tree_blockers": [],
            "working_tree_summary": "Uncommitted changes detected; staged safe files.",
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: [])
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (_ for _ in ()).throw(AssertionError(f"unexpected command: {command}")),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "not_needed"
    assert result["working_tree_detected"] is True
    assert result["auto_stage_attempted"] is True
    assert result["auto_staged_paths"] == ["local_fix_agent.py"]
    assert result["auto_removed_paths"] == []
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []


def test_sync_with_upstream_before_workflow_auto_removes_artifact_and_proceeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_name = "c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt"
    (repo / artifact_name).write_text("temporary artifact\n")

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_pre_task_working_tree",
        lambda current_repo: {
            "working_tree_detected": True,
            "working_tree_clean": True,
            "working_tree_status": "",
            "dirty_paths": [artifact_name],
            "auto_stage_attempted": False,
            "auto_staged_paths": [],
            "auto_removed_paths": [artifact_name],
            "remaining_unstaged_paths": [],
            "working_tree_blockers": [],
            "working_tree_summary": "Uncommitted changes detected; removed artifacts.",
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: [])
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (_ for _ in ()).throw(AssertionError(f"unexpected command: {command}")),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "not_needed"
    assert result["auto_stage_attempted"] is False
    assert result["auto_removed_paths"] == [artifact_name]
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []


def test_sync_with_upstream_before_workflow_clean_repo_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_pre_task_working_tree",
        lambda current_repo: {
            "working_tree_detected": False,
            "working_tree_clean": True,
            "working_tree_status": "",
            "dirty_paths": [],
            "auto_stage_attempted": False,
            "auto_staged_paths": [],
            "auto_removed_paths": [],
            "remaining_unstaged_paths": [],
            "working_tree_blockers": [],
            "working_tree_summary": "Working tree already clean.",
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: [])
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (_ for _ in ()).throw(AssertionError(f"unexpected command: {command}")),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "not_needed"
    assert result["working_tree_detected"] is False
    assert result["auto_stage_attempted"] is False
    assert result["auto_staged_paths"] == []
    assert result["auto_removed_paths"] == []
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []


def test_classify_pre_task_working_tree_auto_stages_safe_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path("/tmp/repo")
    stage_state = {"after_add": False}
    commands: list[list[str]] = []

    def fake_classify_publish_working_tree(current_repo: Path) -> dict:
        if stage_state["after_add"]:
            return {
                "status_output": "M  local_fix_agent.py",
                "clean": False,
                "has_unstaged": False,
                "has_staged": True,
                "has_untracked": False,
                "staged_paths": ["local_fix_agent.py"],
                "unstaged_paths": [],
                "untracked_paths": [],
            }
        return {
            "status_output": " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": ["local_fix_agent.py"],
            "untracked_paths": [],
        }

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "diff", "--cached", "--name-only"]:
            return 0, "local_fix_agent.py\n" if stage_state["after_add"] else ""
        if command == ["git", "status", "--short", "--untracked-files=all"]:
            return (
                0,
                "M  local_fix_agent.py\n" if stage_state["after_add"] else " M local_fix_agent.py\n",
            )
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "classify_publish_working_tree", fake_classify_publish_working_tree)
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo, ignore_path_predicate=None: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.classify_pre_task_working_tree(repo)

    assert result["working_tree_detected"] is True
    assert result["auto_stage_attempted"] is True
    assert result["auto_staged_paths"] == ["local_fix_agent.py"]
    assert result["auto_removed_paths"] == []
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []
    assert ["git", "add", "-A", "--", "local_fix_agent.py"] in commands


def test_classify_pre_task_working_tree_auto_removes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_name = "c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt"
    (repo / artifact_name).write_text("temporary artifact\n")
    commands: list[list[str]] = []

    def fake_classify_publish_working_tree(current_repo: Path) -> dict:
        if (repo / artifact_name).exists():
            return {
                "status_output": f"?? {artifact_name}",
                "clean": False,
                "has_unstaged": False,
                "has_staged": False,
                "has_untracked": True,
                "staged_paths": [],
                "unstaged_paths": [],
                "untracked_paths": [artifact_name],
            }
        return {
            "status_output": "",
            "clean": True,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        }

    monkeypatch.setattr(lfa, "classify_publish_working_tree", fake_classify_publish_working_tree)
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo, ignore_path_predicate=None: [artifact_name])
    def fail_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        if command == ["git", "diff", "--cached", "--name-only"]:
            return 0, ""
        if command == ["git", "status", "--short", "--untracked-files=all"]:
            return (0, f"?? {artifact_name}\n" if (repo / artifact_name).exists() else "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fail_run_subprocess)

    result = lfa.classify_pre_task_working_tree(repo)

    assert result["working_tree_detected"] is True
    assert result["auto_stage_attempted"] is False
    assert result["auto_removed_paths"] == [artifact_name]
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []
    assert not (repo / artifact_name).exists()
    assert not any(command[:3] == ["git", "add", "-A"] for command in commands)


def test_classify_pre_task_working_tree_blocks_on_ambiguous_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []

    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": "?? settings.data",
            "clean": False,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": True,
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": ["settings.data"],
        },
    )
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo, ignore_path_predicate=None: ["settings.data"])
    def fail_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        if command == ["git", "diff", "--cached", "--name-only"]:
            return 0, ""
        if command == ["git", "status", "--short", "--untracked-files=all"]:
            return 0, "?? settings.data\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fail_run_subprocess)

    result = lfa.classify_pre_task_working_tree(repo)

    assert result["working_tree_detected"] is True
    assert result["auto_stage_attempted"] is False
    assert result["auto_staged_paths"] == []
    assert result["auto_removed_paths"] == []
    assert result["remaining_unstaged_paths"] == ["settings.data"]
    assert result["working_tree_blockers"] == [
        {"path": "settings.data", "file_type": "unknown", "reason": "unknown/generated artifact; requires manual review"}
    ]
    assert "ambiguous file" in result["working_tree_summary"]
    assert not any(command[:3] == ["git", "add", "-A"] for command in commands)


def test_classify_pre_task_working_tree_clean_repo_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []

    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": "",
            "clean": True,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo, ignore_path_predicate=None: [])
    def fail_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        if command == ["git", "diff", "--cached", "--name-only"]:
            return 0, ""
        if command == ["git", "status", "--short", "--untracked-files=all"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fail_run_subprocess)

    result = lfa.classify_pre_task_working_tree(repo)

    assert result["working_tree_detected"] is False
    assert result["working_tree_clean"] is True
    assert result["auto_stage_attempted"] is False
    assert result["auto_staged_paths"] == []
    assert result["auto_removed_paths"] == []
    assert result["remaining_unstaged_paths"] == []
    assert result["working_tree_blockers"] == []
    assert not any(command[:3] == ["git", "add", "-A"] for command in commands)


def test_sync_with_upstream_before_workflow_syncs_origin_first_then_upstream_and_validates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commands: list[object] = []
    saved: list[tuple[str, str]] = []

    def fake_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        if command == ["git", "fetch", "origin"]:
            return 0, ""
        if command == ["git", "rev-parse", "--verify", "origin/feature"]:
            return 0, "abc123\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/feature"]:
            return 0, "2 1\n"
        if command == ["git", "fetch", "upstream"]:
            return 0, ""
        if command == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return 0, "refs/remotes/upstream/main\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"]:
            return 0, "1 2\n"
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 docs refresh\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tREADME.md\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/README.md b/README.md\n+updated docs\n"
        if shell and command == "pytest -q":
            return 0, "ok"
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "clean": True,
            "status_output": "",
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin", "upstream"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            True,
            "",
            {"merge_conflicts_detected": False, "merge_result": "not_needed"},
        ),
    )
    monkeypatch.setattr(
        lfa,
        "update_recent_state",
        lambda current_repo, test_cmd, mode, success, artifact_dir=None, target="", files_changed=None, confidence="", blocked_reason="": saved.append((mode, str(success))) or Path("/tmp/state.json"),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["current_branch"] == "feature"
    assert result["origin_detected"] is True
    assert result["origin_branch"] == "origin/feature"
    assert result["origin_ahead_count"] == 2
    assert result["origin_behind_count"] == 1
    assert result["origin_sync_attempted"] is True
    assert result["origin_sync_result"] == "success"
    assert result["upstream_detected"] is True
    assert result["upstream_branch"] == "upstream/main"
    assert result["ahead_count"] == 1
    assert result["behind_count"] == 2
    assert result["sync_attempted"] is True
    assert result["sync_result"] == "success"
    assert result["validation_result_after_sync"] == "success"
    assert result["analysis"]["risk_level"] == "low"
    assert saved[-1] == ("upstream-sync", "success")
    assert result["git_actions"] == [
        "git fetch origin",
        "git merge --no-edit origin/feature",
        "git fetch upstream",
        "git merge --no-edit upstream/main",
    ]
    assert commands.index(["git", "fetch", "origin"]) < commands.index(["git", "fetch", "upstream"])


def test_sync_with_upstream_before_workflow_origin_only_merge_marks_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "origin"]:
            return 0, ""
        if command == ["git", "rev-parse", "--verify", "origin/feature"]:
            return 0, "abc123\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/feature"]:
            return 0, "0 2\n"
        if command == ["git", "fetch", "upstream"]:
            return 0, ""
        if command == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return 0, "refs/remotes/upstream/main\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"]:
            return 0, "0 0\n"
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "clean": True,
            "status_output": "",
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin", "upstream"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            True,
            "",
            {"merge_conflicts_detected": False, "merge_result": "not_needed"},
        ),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["origin_sync_result"] == "success"
    assert result["sync_result"] == "success"
    assert result["validation_result_after_sync"] == "not_run"
    assert result["git_actions"] == [
        "git fetch origin",
        "git merge --no-edit origin/feature",
        "git fetch upstream",
    ]


def test_sync_with_upstream_before_workflow_upstream_conflict_blocks_and_reports_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "upstream"]:
            return 0, ""
        if command == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return 0, "refs/remotes/upstream/main\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"]:
            return 0, "0 3\n"
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 docs refresh\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tREADME.md\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/README.md b/README.md\n+updated docs\n"
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "clean": True,
            "status_output": "",
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["upstream"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            False,
            "upstream merge produced conflicts in: app.py",
            {
                "merge_conflicts_detected": True,
                "conflicted_files": ["app.py"],
                "resolution_strategy_per_file": {"app.py": "manual_resolution_required"},
                "validation_result_after_merge": "not_run",
                "merge_result": "blocked",
                "blocked_reason": "upstream merge produced conflicts in: app.py",
            },
        ),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "blocked"
    assert result["merge_conflict_result"]["conflicted_files"] == ["app.py"]
    assert "app.py" in result["reason"]


def test_sync_with_upstream_before_workflow_origin_conflict_blocks_and_reports_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "origin"]:
            return 0, ""
        if command == ["git", "rev-parse", "--verify", "origin/feature"]:
            return 0, "abc123\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/feature"]:
            return 0, "0 1\n"
        if command == ["git", "fetch", "upstream"]:
            raise AssertionError("upstream fetch should not run after origin conflict")
        if command == ["git", "fetch", "upstream"]:
            return 0, ""
        if command == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return 0, "refs/remotes/upstream/main\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"]:
            return 0, "0 1\n"
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 docs refresh\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tREADME.md\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/README.md b/README.md\n+updated docs\n"
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "clean": True,
            "status_output": "",
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin", "upstream"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            False,
            "origin merge produced conflicts in: settings.json",
            {
                "merge_conflicts_detected": True,
                "conflicted_files": ["settings.json"],
                "resolution_strategy_per_file": {"settings.json": "manual_resolution_required"},
                "validation_result_after_merge": "not_run",
                "merge_result": "blocked",
                "blocked_reason": "origin merge produced conflicts in: settings.json",
            },
        ),
    )

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "blocked"
    assert result["origin_sync_result"] == "blocked"
    assert result["reason"] == "origin merge produced conflicts in: settings.json"
    assert result["merge_conflict_result"]["merge_result"] == "blocked"


def test_analyze_upstream_changes_docs_only_low_risk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 docs refresh\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tREADME.md\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/README.md b/README.md\n+docs\n"
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.analyze_upstream_changes(repo, "upstream/main")

    assert result["changed_files"] == ["README.md"]
    assert result["change_types"]["README.md"] == "modified"
    assert result["categories"]["README.md"] == "docs"
    assert result["risk_level"] == "low"
    assert result["semantic_summary"] == "docs-only changes"


def test_analyze_upstream_changes_new_file_medium_risk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 add helper\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "A\tagent/utils/linear.py\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/agent/utils/linear.py b/agent/utils/linear.py\n+def helper():\n+    return 1\n"
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.analyze_upstream_changes(repo, "upstream/main")

    assert result["change_types"]["agent/utils/linear.py"] == "added"
    assert "new file added: agent/utils/linear.py" in result["summary"]
    assert result["risk_level"] == "medium"


def test_analyze_upstream_changes_core_logic_high_risk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 cli refactor\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tagent/utils/langsmith.py\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/agent/utils/langsmith.py b/agent/utils/langsmith.py\n+trace()\n"
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.analyze_upstream_changes(repo, "upstream/main")

    assert result["risk_level"] == "high"
    assert result["risk_reason"] == "core code changes can alter agent behavior directly"
    assert "LangSmith logging or tracing integration changed" in result["semantic_summary"]


def test_analyze_upstream_changes_dependencies_high_risk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 deps update\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tuv.lock\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/uv.lock b/uv.lock\n+version = \"1.2.3\"\n"
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.analyze_upstream_changes(repo, "upstream/main")

    assert result["risk_level"] == "high"
    assert result["risk_reason"] == "dependency or lockfile changes can alter runtime behavior"
    assert "dependencies updated: uv.lock" in result["summary"]


def test_print_upstream_change_analysis_outputs_summary(capsys: pytest.CaptureFixture[str]) -> None:
    lfa.print_upstream_change_analysis(
        {
            "commit_count": 2,
            "changed_files": ["agent/utils/langsmith.py", "README.md"],
            "summary": "2 files changed; core logic updated in agent/utils/langsmith.py; docs updated: README.md",
            "semantic_summary": "LangSmith logging or tracing integration changed; documentation changed",
            "risk_level": "high",
            "risk_reason": "core code changes can alter agent behavior directly",
        }
    )

    output = capsys.readouterr().out
    assert "=== UPSTREAM CHANGE ANALYSIS ===" in output
    assert "commits: 2" in output
    assert "files_changed: 2" in output
    assert "risk_level: high" in output


def test_sync_with_upstream_before_workflow_high_risk_still_merges_upstream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "upstream"]:
            return 0, ""
        if command == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return 0, "refs/remotes/upstream/main\n"
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"]:
            return 0, "0 1\n"
        if command == ["git", "log", "HEAD..upstream/main", "--oneline"]:
            return 0, "abc123 core change\n"
        if command == ["git", "diff", "--name-status", "HEAD..upstream/main"]:
            return 0, "M\tagent/core.py\n"
        if command == ["git", "diff", "HEAD..upstream/main"]:
            return 0, "diff --git a/agent/core.py b/agent/core.py\n+return 1\n"
        if shell and command == "pytest -q":
            return 0, "ok"
        return 0, ""

    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "clean": True,
            "status_output": "",
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["upstream"])
    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            True,
            "",
            {"merge_conflicts_detected": False, "merge_result": "not_needed"},
        ),
    )
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: Path("/tmp/state.json"))

    result = lfa.sync_with_upstream_before_workflow(repo, validation_command="pytest -q")

    assert result["sync_result"] == "success"
    assert result["sync_attempted"] is True
    assert result["analysis"]["risk_level"] == "high"


def test_maybe_handle_merge_conflicts_no_conflicts_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_ok(repo, "init")
    git_ok(repo, "config", "user.email", "tests@example.com")
    git_ok(repo, "config", "user.name", "Test User")

    outcome = lfa.maybe_handle_merge_conflicts(
        repo,
        validation_command="python -c \"print('ok')\"",
        publish_requested=False,
        publish_mode="validated-run",
        publish_branch="",
        publish_pr=False,
        publish_merge=False,
        publish_merge_local_main=False,
        publish_message="",
        target="",
        dry_run_mode=False,
        force_publish=False,
        no_auto_merge_conflicts=False,
    )

    assert outcome is None


def test_fork_created_in_run_one_reused_in_run_two(monkeypatch: pytest.MonkeyPatch) -> None:
    target = lfa.resolve_publish_target(
        make_preflight(
            origin_url="git@github.com:upstream/demo.git",
            origin_owner="upstream",
            current_user="contributor",
            requires_fork=True,
        ),
        {"fork_created": True, "fork_repo": "contributor/demo"},
    )

    assert target["type"] == "fork"
    assert target["reason"] == "reusing persisted fork target"


def test_https_rewritten_once_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    result = lfa.make_publish_result()
    result["preflight"] = make_preflight(
        transport="ssh",
        origin_url="git@github.com:octocat/demo.git",
    )
    result["target"] = {
        "type": "origin",
        "remote_name": "origin",
        "repo": "octocat/demo",
        "transport": "ssh",
        "url": "git@github.com:octocat/demo.git",
        "requires_fork": False,
        "reason": "authenticated user owns origin",
    }

    called: list[str] = []
    monkeypatch.setattr(lfa, "set_origin_remote_url", lambda repo, new_url: (called.append(new_url) or True, ""))

    ok, _, _ = lfa.prepare_publish_target(Path("/tmp/repo"), result)

    assert ok is True
    assert called == []


def test_origin_change_resets_state(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    saved: list[dict] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {"origin_url": "git@github.com:old/demo.git", "timestamp": 1, "ssh_confirmed": True})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: saved.append(state))
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(
        lfa,
        "build_publish_preflight",
        lambda current_repo, branch: make_preflight(origin_url="git@github.com:new/demo.git", origin_owner="new"),
    )
    monkeypatch.setattr(lfa, "set_origin_remote_url", lambda current_repo, new_url: (True, ""))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: [])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "",
            "meaningful_changes_detected": False,
            "meaningful_paths": [],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc123"))

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["state_reset"] is True
    assert saved


def test_ssh_confirmed_locks_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    result = lfa.make_publish_result()
    result["transport_locked"] = True
    result["preflight"] = make_preflight(transport="https", origin_url="https://github.com/octocat/demo")
    monkeypatch.setattr(lfa, "set_origin_remote_url", lambda repo, new_url: (True, ""))

    ok, error = lfa.apply_origin_normalization(Path("/tmp/repo"), result, {"ssh_confirmed": True})

    assert ok is True
    assert error == ""
    assert result["normalized_origin"] == "git@github.com:octocat/demo.git"


def test_repeat_publish_noop_from_previous_run(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_pr_url": "https://github.com/octocat/demo/pull/7",
            "last_publish_mode": "validated_run",
            "last_meaningful_content_fingerprint": "fp-123",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc123"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "fp-123")

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["control_path"] == "noop"
    assert result["reason"] == "matched previous successful publish fingerprint"
    assert result["fingerprint"]["matched_previous_success"] is True
    assert result["previous_publish_branch"] == "feature"
    assert result["previous_pr_url"] == "https://github.com/octocat/demo/pull/7"
    assert result["previous_commit"] == "abc123"


def test_repeat_publish_noop_from_previous_run_without_pr_metadata_falls_back_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_publish_mode": "validated_run",
            "last_meaningful_content_fingerprint": "fp-123",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc123"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "fp-123")

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "noop"
    assert result["previous_publish_branch"] == "feature"
    assert result["previous_pr_url"] == ""
    assert result["previous_commit"] == "abc123"


def test_same_clean_head_as_last_successful_publish_noops(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_publish_mode": "validated_run",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "",
            "clean": True,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (True, "abc123"))

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "noop"
    assert result["fingerprint"]["matched_previous_success"] is True


def test_new_meaningful_unstaged_changes_do_not_reuse_previous_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_publish_mode": "validated_run",
            "last_meaningful_content_fingerprint": "old-fp",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "new-fp")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["fingerprint"]["matched_previous_success"] is False
    assert result["fingerprint"]["reason"] == "fingerprint mismatch due to new meaningful changes"


def test_same_target_but_different_commit_does_not_reuse_previous_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc122",
            "last_publish_mode": "validated_run",
            "last_meaningful_content_fingerprint": "fp-123",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "",
            "clean": True,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "fp-123")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["fingerprint"]["matched_previous_success"] is False


def test_publish_uses_last_published_commit_diff_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_publish_mode": "validated_run",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "def456")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "run_prepublish_docs_stage", lambda *args, **kwargs: {"docs_checked_at_publish": False, "docs_required": False, "docs_updated": False, "docs_refresh_mode": "none", "docs_targets": []})
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {"status_output": "M  local_fix_agent.py", "clean": False, "has_unstaged": False, "has_staged": True, "has_untracked": False},
    )
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "new-fp")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "def456"))

    def fake_classify(current_repo, baseline_commit="", current_commit="HEAD"):
        captured["baseline_commit"] = baseline_commit
        captured["current_commit"] = current_commit
        return {
            "status_output": "",
            "diff_output": "M\tlocal_fix_agent.py",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        }

    monkeypatch.setattr(lfa, "classify_publishable_changes", fake_classify)
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command[:3] == ["git", "add", "-A"] else (0, ""),
    )
    monkeypatch.setattr(lfa, "verify_publish_sync", lambda *args, **kwargs: {"current_branch": "feature", "upstream_branch": "origin/feature", "upstream_exists": True, "local_head": "def456", "remote_head": "def456", "synced": True, "reason": ""})

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert captured == {"baseline_commit": "abc123", "current_commit": "def456"}
    assert result["meaningful_changes_detected"] is True
    assert result["last_published_commit"] == "abc123"
    assert result["diff_files_detected"] == ["local_fix_agent.py"]


def test_same_commit_but_different_meaningful_content_fingerprint_does_not_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "load_publish_state",
        lambda current_repo: {
            "last_success": True,
            "last_branch": "feature",
            "last_commit": "abc123",
            "last_publish_mode": "validated_run",
            "last_meaningful_content_fingerprint": "old-fp",
            "origin_url": "git@github.com:octocat/demo.git",
            "timestamp": 1,
            "ssh_confirmed": True,
            "last_target_repo": "octocat/demo",
        },
    )
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "compute_meaningful_content_fingerprint", lambda current_repo, publish_changes: "new-fp")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["fingerprint"]["matched_previous_success"] is False


def test_publish_validated_run_missing_auth_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "set_origin_remote_url", lambda current_repo, new_url: (True, ""))
    monkeypatch.setattr(
        lfa,
        "build_publish_preflight",
        lambda current_repo, branch: make_preflight(
            transport="https",
            gh_available=False,
            gh_auth=False,
            ssh_auth=False,
            origin_url="https://github.com/octocat/demo.git",
            current_user="",
        ),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["control_path"] == "blocked_auth"
    assert result["final"]["status"] == "blocked"


def test_publish_validated_run_fork_required_non_interactive_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin", "upstream"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(
        lfa,
        "build_publish_preflight",
        lambda current_repo, branch: make_preflight(
            origin_url="git@github.com:upstream/demo.git",
            origin_owner="upstream",
            current_user="contributor",
            requires_fork=True,
            upstream_present=True,
            upstream_url="git@github.com:upstream/demo.git",
            upstream_owner="upstream",
            upstream_repo="demo",
        ),
    )
    monkeypatch.setattr(lfa.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "prepare_publish_target",
        lambda current_repo, result: (
            False,
            "Fork target `contributor/demo` does not exist yet.",
            "Run `gh repo fork upstream/demo --clone=false` and `git remote set-url origin git@github.com:contributor/demo.git`.",
        ),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["control_path"] == "fork_push"
    assert "gh repo fork upstream/demo --clone=false" in result["next_action"]


def test_ci_mode_disables_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    result = lfa.make_publish_result()
    result["environment"] = {
        "ci": True,
        "github_actions": True,
        "interactive": False,
        "allow_auto_fork": False,
    }
    result["preflight"] = make_preflight(
        origin_url="git@github.com:upstream/demo.git",
        origin_owner="upstream",
        current_user="contributor",
        requires_fork=True,
    )
    result["target"] = {
        "type": "fork",
        "remote_name": "origin",
        "repo": "contributor/demo",
        "transport": "ssh",
        "url": "git@github.com:contributor/demo.git",
        "requires_fork": True,
        "reason": "origin owner upstream differs from authenticated user contributor",
    }
    monkeypatch.setattr(lfa, "target_remote_exists", lambda repo, target: (False, ""))
    monkeypatch.setattr(lfa, "prompt_yes_no", lambda question, default=False: (_ for _ in ()).throw(AssertionError("prompt should not be used in CI")))

    ok, reason, next_action = lfa.prepare_publish_target(Path("/tmp/repo"), result)

    assert ok is False
    assert "AI_PUBLISH_ALLOW_FORK=1" in next_action


def test_existing_pr_prevents_duplicate_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/7")
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["published"] is True
    assert result["pr_already_exists"] is True
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/7"
    assert not any(cmd[:3] == ["gh", "pr", "create"] for cmd in commands)


def test_publish_current_clean_tree_results_in_noop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: [])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "",
            "meaningful_changes_detected": False,
            "meaningful_paths": [],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["control_path"] == "noop"
    assert result["reason"] == "no meaningful changes to publish"
    assert result["final"]["status"] == "noop"
    assert result["auto_stage_attempted"] is False
    assert result["auto_stage_result"] == "not_needed"
    assert commands == [["git", "status", "--short", "--untracked-files=all"]]

    lfa.print_publish_summary(result)
    assert "mode_summary: no meaningful changes to publish" in capsys.readouterr().out


def test_publish_current_unstaged_change_auto_stages_publishable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: " M local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py" if stage_state["after_add"] else " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": False,
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(
                {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["summary_status"] == "staged 1 publishable file(s)"
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] == "success"
    assert result["auto_staged_paths"] == ["local_fix_agent.py"]
    assert result["staging_summary"] == {"auto_staged": 1, "ignored": 0, "blocked": 0}
    assert result["staging_decision_reason"] == "safe publishable files were auto-staged and re-audited successfully"
    assert result["file_decisions"] == [
        {
            "path": "local_fix_agent.py",
            "file_type": "code",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
            "tracked": True,
            "staged": True,
            "unstaged": False,
            "untracked": False,
            "action": "auto_staged",
            "reason": "safe tracked code file",
        }
    ]
    assert ["git", "add", "-A", "--", "local_fix_agent.py"] in commands
    assert ["git", "commit", "-m", "chore: publish current repo state"] in commands
    assert ["git", "fetch", "origin", "main"] in commands
    assert ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"] in commands
    assert ["git", "push", "-u", "origin", "feature"] in commands


def test_publish_current_safe_file_plus_internal_state_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["docs/README.md"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M docs/README.md\n?? .ai_publish_state.json\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["docs/README.md"],
            "ignored_changes": [".ai_publish_state.json"],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": "M  docs/README.md\n?? .ai_publish_state.json" if stage_state["after_add"] else " M docs/README.md\n?? .ai_publish_state.json",
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": True,
            "staged_paths": ["docs/README.md"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["docs/README.md"],
            "untracked_paths": [".ai_publish_state.json"],
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "docs/README.md"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "baseRefName": "main", "headRefName": "feature"})
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "success"
    assert result["safe_staged_paths"] == ["docs/README.md"]
    assert result["ignored_nonblocking_paths"] == [".ai_publish_state.json"]
    assert result["true_blockers"] == []
    assert result["blocker_count"] == 0
    assert result["publishable_ready"] is True
    assert result["blocked_file_analysis"] == []
    assert ["git", "add", "-A", "--", "docs/README.md"] in commands


def test_publish_current_ignored_internal_files_only_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "?? .ai_publish_state.json\n",
            "meaningful_changes_detected": False,
            "meaningful_paths": [],
            "ignored_changes": [".ai_publish_state.json"],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(lfa, "raw_git_status_output", lambda current_repo: "?? .ai_publish_state.json\n")

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "noop"
    assert result["ignored_nonblocking_paths"] == [".ai_publish_state.json"]
    assert result["true_blockers"] == []
    assert result["blocker_count"] == 0
    assert result["publishable_ready"] is True


def test_publish_current_safe_file_plus_artifact_auto_removes_and_publishes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_name = "c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt"
    (repo / artifact_name).write_text("temporary artifact\n")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": f" M local_fix_agent.py\n?? {artifact_name}\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": (
                f"M  local_fix_agent.py\n?? {artifact_name}"
                if stage_state["after_add"] and (repo / artifact_name).exists()
                else "M  local_fix_agent.py"
                if stage_state["after_add"]
                else f" M local_fix_agent.py\n?? {artifact_name}"
            ),
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": bool((repo / artifact_name).exists()),
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [artifact_name] if (repo / artifact_name).exists() else [],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "success"
    assert result["published"] is True
    assert result["safe_staged_paths"] == ["local_fix_agent.py"]
    assert result["true_blockers"] == []
    assert result["blocker_count"] == 0
    assert result["publishable_ready"] is True
    assert result["blocker_remediation_attempted"] is True
    assert result["blocker_remediation_result"] == "success"
    assert result["auto_removed_paths"] == [artifact_name]
    assert result["remaining_true_blockers"] == []
    assert not (repo / artifact_name).exists()
    assert ["git", "add", "-A", "--", "local_fix_agent.py"] in commands
    assert any(cmd[:2] == ["git", "commit"] for cmd in commands)
    assert any(cmd[:3] == ["git", "push", "-u"] for cmd in commands)


def test_publish_current_repo_state_auto_removes_agent_run_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".fix_agent_runs" / "run123"
    run_dir.mkdir(parents=True)
    diff_path = run_dir / "diff.patch"
    log_path = run_dir / "log.txt"
    diff_path.write_text("generated diff")
    log_path.write_text("generated log")
    stage_state = {"after_add": False}
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": (
                " M local_fix_agent.py\n" "?? .fix_agent_runs/run123/diff.patch\n" "?? .fix_agent_runs/run123/log.txt\n"
            ),
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    def classify_run_artifact_working_tree(_current_repo: Path) -> dict:
        lines = []
        untracked_paths: list[str] = []
        if stage_state["after_add"]:
            lines.append("M  local_fix_agent.py")
        else:
            lines.append(" M local_fix_agent.py")
        for artifact in (diff_path, log_path):
            rel = str(artifact.relative_to(repo))
            if artifact.exists():
                lines.append(f"?? {rel}")
                untracked_paths.append(rel)
        status_output = "\n".join(lines)
        return {
            "status_output": status_output,
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": bool(untracked_paths),
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": untracked_paths,
        }

    monkeypatch.setattr(lfa, "classify_publish_working_tree", classify_run_artifact_working_tree)

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "baseRefName": "main", "headRefName": "feature"})
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "success"
    assert result["blocker_remediation_attempted"] is True
    assert result["blocker_remediation_result"] == "success"
    assert set(result["auto_removed_paths"]) == {".fix_agent_runs/run123/diff.patch", ".fix_agent_runs/run123/log.txt"}
    assert not diff_path.exists()
    assert not log_path.exists()
    assert result["true_blockers"] == []


def test_publish_current_repo_state_agent_run_artifact_outside_policy_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside_dir = repo / ".fix_agent_runs_extra"
    outside_dir.mkdir()
    artifact = outside_dir / "artifact.log"
    artifact.write_text("temp")
    stage_state = {"after_add": False}
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": f" M local_fix_agent.py\n?? {artifact.relative_to(repo)}\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    def classify_external_artifact_working_tree(_: Path) -> dict:
        lines = []
        if stage_state["after_add"]:
            lines.append("M  local_fix_agent.py")
        else:
            lines.append(" M local_fix_agent.py")
        artifact_rel = str(artifact.relative_to(repo))
        if artifact.exists():
            lines.append(f"?? {artifact_rel}")
        status_output = "\n".join(lines)
        return {
            "status_output": status_output,
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": artifact.exists(),
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [artifact_rel] if artifact.exists() else [],
        }

    monkeypatch.setattr(lfa, "classify_publish_working_tree", classify_external_artifact_working_tree)

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "blocked"
    assert result["auto_removed_paths"] == []
    assert result["true_blockers"] == [
        {"path": str(artifact.relative_to(repo)), "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"}
    ]


def test_attempt_publish_auto_revalidation_reruns_when_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    validation_state = {
        "validation_state": "success",
        "validation_result": "success",
        "validation_commit_match": False,
        "fingerprint_match": False,
        "last_validated_commit": "abc123",
        "current_commit": "def456",
        "last_validated_fingerprint": "hash1",
        "current_fingerprint": "hash2",
        "meaningful_changes_detected": True,
    }
    def fake_load_recent_state() -> dict:
        return {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "commit_hash": "abc123",
                    "validation_command": "echo ok",
                    "validation_result": "success",
                    "success": True,
                    "ts": int(time.time()),
                }
            ]
        }
    monkeypatch.setattr(lfa, "load_recent_state", lambda: fake_load_recent_state())
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "run_subprocess", lambda cmd, cwd, shell=True: (0, "ok"))
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "validation_state": "success",
            "validation_result": "success",
            "validation_commit_match": True,
            "fingerprint_match": True,
            "last_validated_commit": "def456",
            "current_commit": "def456",
            "validation_age_seconds": 0,
        },
    )
    result = lfa.attempt_publish_auto_revalidation(repo, validation_state)
    assert result["validation_stale_detected"] is True
    assert result["validation_rerun_attempted"] is True
    assert result["validation_rerun_result"] == "success"
    assert result["validation_commit_updated"] is True
    assert result["validation_state"] == "success"


def test_attempt_publish_auto_revalidation_blocks_when_rerun_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    validation_state = {
        "validation_state": "success",
        "validation_result": "success",
        "validation_commit_match": False,
        "fingerprint_match": False,
        "last_validated_commit": "abc123",
        "current_commit": "def456",
    }
    monkeypatch.setattr(
        lfa,
        "load_recent_state",
        lambda: {
            "recent_runs": [
                {
                    "repo": str(repo),
                    "target": "",
                    "commit_hash": "abc123",
                    "validation_command": "echo fail",
                    "validation_result": "success",
                    "success": True,
                    "ts": int(time.time()),
                }
            ]
        },
    )
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(lfa, "run_subprocess", lambda cmd, cwd, shell=True: (1, "failed"))
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "validation_state": "blocked",
            "validation_result": "blocked",
            "validation_commit_match": False,
            "fingerprint_match": False,
            "last_validated_commit": "abc123",
            "current_commit": "def456",
        },
    )
    result = lfa.attempt_publish_auto_revalidation(repo, validation_state)
    assert result["validation_stale_detected"] is True
    assert result["validation_rerun_attempted"] is True
    assert result["validation_rerun_result"] == "failed"
    assert result["validation_state"] == "blocked"


def test_attempt_publish_auto_revalidation_skips_when_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    validation_state = {
        "validation_state": "success",
        "validation_result": "success",
        "validation_commit_match": True,
        "fingerprint_match": True,
        "last_validated_commit": "abc123",
        "current_commit": "abc123",
    }
    monkeypatch.setattr(lfa, "load_recent_state", lambda: {"recent_runs": []})
    result = lfa.attempt_publish_auto_revalidation(repo, validation_state)
    assert result["validation_stale_detected"] is False
    assert result["validation_rerun_attempted"] is False
    assert result["validation_rerun_result"] == "not_needed"

def test_publish_current_untracked_files_stage_and_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["new_file.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "?? new_file.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "?? new_file.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["new_file.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "A  new_file.py" if stage_state["after_add"] else "?? new_file.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": stage_state["after_add"],
            "has_untracked": not stage_state["after_add"],
            "staged_paths": ["new_file.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [],
            "untracked_paths": [] if stage_state["after_add"] else ["new_file.py"],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "new_file.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(
                {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["working_tree"]["staged_paths"] == ["new_file.py"]
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] == "success"
    assert result["auto_staged_paths"] == ["new_file.py"]
    assert result["staging_summary"] == {"auto_staged": 1, "ignored": 0, "blocked": 0}
    assert result["file_decisions"] == [
        {
            "path": "new_file.py",
            "file_type": "code",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
            "tracked": True,
            "staged": True,
            "unstaged": False,
            "untracked": False,
            "action": "auto_staged",
            "reason": "safe new publishable code file",
        }
    ]
    assert ["git", "add", "-A", "--", "new_file.py"] in commands
    assert ["git", "commit", "-m", "chore: publish current repo state"] in commands
    assert ["git", "fetch", "origin", "main"] in commands
    assert ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"] in commands
    assert ["git", "push", "-u", "origin", "feature"] in commands


def test_publish_current_auto_stage_targets_publishable_files_only(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["README.md", "local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: " M local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["README.md", "local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": "M  README.md\nM  local_fix_agent.py" if stage_state["after_add"] else " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": False,
            "staged_paths": ["README.md", "local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(
                {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] == "success"
    assert result["auto_staged_paths"] == ["local_fix_agent.py"]
    assert any(cmd[:4] == ["git", "add", "-A", "--"] for cmd in commands)
    assert ["git", "commit", "-m", "chore: publish current repo state"] in commands


def test_validated_run_publish_still_stages_tracked_files_only(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=False: "M  local_fix_agent.py")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["published"] is True
    assert ["git", "add", "-A", "--", "local_fix_agent.py"] in commands


def test_low_confidence_persisted_state_triggers_recompute() -> None:
    preflight = make_preflight(
        origin_url="git@github.com:upstream/demo.git",
        origin_owner="upstream",
        current_user="contributor",
        requires_fork=True,
        gh_auth=False,
        ssh_auth=False,
    )
    result = lfa.make_publish_result()
    result["preflight"] = preflight
    result["state_reset"] = False

    confidence = lfa.compute_state_confidence(result, {"fork_created": True, "fork_repo": "contributor/demo"}, normalization_ok=True)
    target = lfa.resolve_publish_target(preflight, {} if confidence == "low" else {"fork_created": True, "fork_repo": "contributor/demo"})

    assert confidence == "low"
    assert target["type"] == "origin"


def test_high_confidence_persisted_state_reuses_fork_transport() -> None:
    result = lfa.make_publish_result()
    result["preflight"] = make_preflight(
        origin_url="git@github.com:upstream/demo.git",
        origin_owner="upstream",
        current_user="contributor",
        requires_fork=True,
    )

    confidence = lfa.compute_state_confidence(result, {"fork_created": True, "fork_repo": "contributor/demo", "ssh_confirmed": True}, normalization_ok=True)
    target = lfa.resolve_publish_target(result["preflight"], {"fork_created": True, "fork_repo": "contributor/demo"} if confidence == "high" else {})

    assert confidence == "high"
    assert target["type"] == "fork"
    assert target["transport"] == "ssh"
    assert target["reason"] == "reusing persisted fork target"


def test_control_path_always_set() -> None:
    result = lfa.make_publish_result()

    assert "control_path" in result


def test_publish_current_repo_state_uses_current_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    captured: dict[str, object] = {}

    def fake_publish_validated_run(
        current_repo: Path,
        test_cmd: str,
        attempt_number: int,
        confidence_level: str,
        artifact_dir: Path | None,
        changed_paths: list[str],
        publish_branch: str,
        publish_pr: bool,
        publish_merge: bool,
        publish_merge_local_main: bool,
        publish_message: str,
        target: str,
        blocked_reason: str | None,
        baseline_paths: list[str],
        dry_run_mode: bool,
        publish_current_mode: bool = False,
        validation_state: str = "success",
        force_publish: bool = False,
        auto_stage_safe_paths: bool = True,
        auto_remediate_blockers: bool = True,
        explain_staging: bool = False,
    ) -> dict:
        captured.update(
            {
                "repo": current_repo,
                "test_cmd": test_cmd,
                "attempt_number": attempt_number,
                "confidence_level": confidence_level,
                "artifact_dir": artifact_dir,
                "changed_paths": changed_paths,
                "publish_branch": publish_branch,
                "publish_pr": publish_pr,
                "publish_merge": publish_merge,
                "publish_merge_local_main": publish_merge_local_main,
                "publish_message": publish_message,
                "target": target,
                "blocked_reason": blocked_reason,
                "baseline_paths": baseline_paths,
                "dry_run_mode": dry_run_mode,
                "publish_current_mode": publish_current_mode,
                "validation_state": validation_state,
                    "force_publish": force_publish,
                    "auto_stage_safe_paths": auto_stage_safe_paths,
                    "auto_remediate_blockers": auto_remediate_blockers,
                    "explain_staging": explain_staging,
                }
            )
        return {"recommended_command": "old", "final": {"status": "noop"}}

    monkeypatch.setattr(lfa, "publish_validated_run", fake_publish_validated_run)

    result = lfa.publish_current_repo_state(repo, "feature/publish", True, False, False, "", "", False)

    assert captured["repo"] == repo
    assert captured["test_cmd"] == "n/a (publish current repo state)"
    assert captured["attempt_number"] == 0
    assert captured["confidence_level"] == "n/a"
    assert captured["artifact_dir"] is None
    assert captured["changed_paths"] == []
    assert captured["publish_branch"] == "feature/publish"
    assert captured["publish_pr"] is True
    assert captured["publish_merge"] is False
    assert captured["publish_merge_local_main"] is False
    assert captured["publish_message"] == "chore: publish current repo state"
    assert captured["target"] == ""
    assert captured["blocked_reason"] is None
    assert captured["baseline_paths"] == []
    assert captured["dry_run_mode"] is False
    assert captured["publish_current_mode"] is True
    assert captured["validation_state"] == "success"
    assert captured["force_publish"] is False
    assert captured["auto_remediate_blockers"] is True
    assert result["recommended_command"] == "./scripts/fixpublish.sh"


def test_run_post_success_publish_triggers_on_validation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    captured: dict[str, object] = {}

    def fake_publish_validated_run(
        current_repo: Path,
        test_cmd: str,
        attempt_number: int,
        confidence_level: str,
        artifact_dir: Path | None,
        changed_paths: list[str],
        publish_branch: str,
        publish_pr: bool,
        publish_merge: bool,
        publish_merge_local_main: bool,
        publish_message: str,
        target: str,
        blocked_reason: str | None,
        baseline_paths: list[str],
        dry_run_mode: bool,
        publish_current_mode: bool = False,
        validation_state: str = "success",
        force_publish: bool = False,
        auto_stage_safe_paths: bool = True,
        auto_remediate_blockers: bool = True,
        explain_staging: bool = False,
    ) -> dict:
        captured["repo"] = current_repo
        captured["test_cmd"] = test_cmd
        captured["validation_state"] = validation_state
        captured["force_publish"] = force_publish
        captured["auto_stage_safe_paths"] = auto_stage_safe_paths
        captured["auto_remediate_blockers"] = auto_remediate_blockers
        captured["explain_staging"] = explain_staging
        return {
            "published": True,
            "publish_scope": "validated_run",
            "triggered": True,
            "validation_state": validation_state,
            "publish_reason": "validated",
            "final": {"status": "success"},
            "verification": {"reason": ""},
        }

    monkeypatch.setattr(lfa, "publish_validated_run", fake_publish_validated_run)

    summary = lfa.run_post_success_publish(
        repo,
        "pytest -q",
        2,
        "HIGH",
        None,
        ["local_fix_agent.py"],
        "",
        False,
        False,
        False,
        "",
        "",
        None,
        [],
        False,
        "validated-run",
        True,
        True,
    )

    assert summary["validation_result"] == "success"
    assert summary["publish_triggered"] is True
    assert summary["publish_result"] == "success"
    assert summary["publish_mode"] == "validated-run"
    assert summary["publish_reason"] == "validated"
    assert captured["repo"] == repo
    assert captured["test_cmd"] == "pytest -q"
    assert captured["validation_state"] == "success"
    assert captured["force_publish"] is False
    assert captured["auto_remediate_blockers"] is True


def test_resolve_publish_requested_defaults_to_true() -> None:
    args = argparse.Namespace(publish_only=False, no_publish_on_success=False, no_finalize=False, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is True


def test_resolve_publish_requested_honors_no_publish_on_success() -> None:
    args = argparse.Namespace(publish_only=False, no_publish_on_success=True, no_finalize=False, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is False


def test_resolve_publish_requested_honors_no_finalize() -> None:
    args = argparse.Namespace(publish_only=False, no_publish_on_success=False, no_finalize=True, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is False


def test_resolve_publish_requested_preserves_publish_only_mode() -> None:
    args = argparse.Namespace(publish_only=True, no_publish_on_success=True, no_finalize=True, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is True


def test_successful_main_run_invokes_publish_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = run_successful_main_publish_flow(monkeypatch, tmp_path)

    assert captured["finalize_publish_requested"] is True
    assert captured["run_post_success_publish_requested"] is True
    assert captured["publish_calls"] == 1
    assert captured["changed_paths"] == ["local_fix_agent.py"]
    assert captured["baseline_paths"] == ["local_fix_agent.py"]
    assert captured["publish_mode"] == "validated-run"


def test_successful_main_run_invokes_publish_by_default_for_salvaged_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = run_successful_main_publish_flow(monkeypatch, tmp_path, salvaged_tool_call=True)

    assert captured["finalize_publish_requested"] is True
    assert captured["run_post_success_publish_requested"] is True
    assert captured["publish_calls"] == 1
    assert captured["baseline_paths"] == ["local_fix_agent.py"]


def test_multiple_successful_runs_each_attempt_publish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = run_successful_main_publish_flow(monkeypatch, tmp_path / "first")
    second = run_successful_main_publish_flow(monkeypatch, tmp_path / "second")

    assert first["publish_calls"] == 1
    assert second["publish_calls"] == 1
    assert first["run_post_success_publish_requested"] is True
    assert second["run_post_success_publish_requested"] is True


def test_successful_main_run_reaches_publish_validated_run_and_docs_reporting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = run_successful_main_publish_flow(
        monkeypatch,
        tmp_path,
        use_real_post_success_publish=True,
    )

    assert captured["finalize_publish_requested"] is True
    assert captured["publish_validated_run_calls"] == 1
    assert captured["publish_validated_run_changed_paths"] == ["local_fix_agent.py"]
    assert captured["publish_validated_run_baseline_paths"] == ["local_fix_agent.py"]
    assert captured["publish_validated_run_validation_state"] == "success"


def test_recommended_publish_current_command_uses_finalizer_script() -> None:
    assert lfa.recommended_publish_current_command(include_pr=True) == "./scripts/fixpublish.sh"


def test_ensure_validation_record_creates_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    calls: list[tuple[str, str]] = []
    states = [
        {
            "current_commit": "abc123",
            "last_validated_commit": "",
            "validation_result": "blocked",
            "validation_state": "blocked",
            "reason": "publish blocked because no validation record was recorded for this repo; use --force-publish to override",
        },
        {
            "current_commit": "abc123",
            "last_validated_commit": "abc123",
            "validation_result": "success",
            "validation_state": "success",
            "reason": "validated",
        },
    ]

    monkeypatch.setattr(lfa, "resolve_publish_validation_state", lambda current_repo: states.pop(0))
    monkeypatch.setattr(
        lfa,
        "run_repo_validation_command",
        lambda current_repo, validation_command, mode, confidence, target="", files_changed=None: calls.append((validation_command, mode)) or {"ok": True, "validation_result": "success", "reason": "", "output": ""},
    )

    result = lfa.ensure_validation_record_for_current_commit(repo, validation_command="pytest -q")

    assert result["ok"] is True
    assert result["validation_record_created"] is True
    assert result["validation_commit"] == "abc123"
    assert calls == [("pytest -q", "finalization-prepare")]


def test_ensure_validation_record_reuses_existing_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(
        lfa,
        "resolve_publish_validation_state",
        lambda current_repo: {
            "current_commit": "abc123",
            "last_validated_commit": "abc123",
            "validation_result": "success",
            "validation_state": "success",
            "reason": "validated",
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_repo_validation_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("validation should be reused")),
    )

    result = lfa.ensure_validation_record_for_current_commit(repo, validation_command="pytest -q")

    assert result["ok"] is True
    assert result["validation_record_created"] is False
    assert result["validation_record_reused"] is True
    assert result["validation_commit"] == "abc123"


def test_ensure_validation_record_refreshes_current_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    states = [
        {
            "current_commit": "abc123",
            "last_validated_commit": "abc123",
            "validation_result": "failed",
            "validation_state": "failed",
            "reason": "pytest failed",
        },
        {
            "current_commit": "abc123",
            "last_validated_commit": "abc123",
            "validation_result": "success",
            "validation_state": "success",
            "reason": "validated",
        },
    ]
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(lfa, "resolve_publish_validation_state", lambda current_repo: states.pop(0))
    monkeypatch.setattr(
        lfa,
        "run_repo_validation_command",
        lambda current_repo, validation_command, mode, confidence, target="", files_changed=None: calls.append((validation_command, mode)) or {"ok": True, "validation_result": "success", "reason": "", "output": ""},
    )

    result = lfa.ensure_validation_record_for_current_commit(repo, validation_command="pytest -q")

    assert result["ok"] is True
    assert result["validation_record_created"] is True
    assert result["validation_record_reused"] is False
    assert result["validation_result"] == "success"
    assert calls == [("pytest -q", "finalization-prepare")]


def test_ensure_validation_record_failed_validation_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    states = [
        {
            "current_commit": "abc123",
            "last_validated_commit": "",
            "validation_result": "blocked",
            "validation_state": "blocked",
            "reason": "publish blocked because no validation record was recorded for this repo; use --force-publish to override",
        },
        {
            "current_commit": "abc123",
            "last_validated_commit": "abc123",
            "validation_result": "failed",
            "validation_state": "failed",
            "reason": "publish blocked because the latest validation run failed; use --force-publish to override",
        },
    ]

    monkeypatch.setattr(lfa, "resolve_publish_validation_state", lambda current_repo: states.pop(0))
    monkeypatch.setattr(
        lfa,
        "run_repo_validation_command",
        lambda current_repo, validation_command, mode, confidence, target="", files_changed=None: {"ok": False, "validation_result": "failed", "reason": "pytest failed", "output": "pytest failed"},
    )

    result = lfa.ensure_validation_record_for_current_commit(repo, validation_command="pytest -q")

    assert result["ok"] is False
    assert result["validation_record_created"] is True
    assert result["validation_result"] == "failed"


def test_run_repo_validation_command_invalid_shell_command_uses_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "local_fix_agent.py").write_text("print('ok')\n")
    calls: list[object] = []
    updates: list[tuple[str, str, str]] = []

    def fake_run_subprocess(command, cwd, shell=False):
        calls.append(command)
        if shell and command == "bad((":
            return 2, '/bin/sh: 1: Syntax error: word unexpected (expecting ")")'
        if shell and command == "pytest -q":
            return 0, "ok"
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "repo_files", lambda current_repo: ["local_fix_agent.py", "tests/test_local_fix_agent_publish.py"])
    monkeypatch.setattr(lfa, "latest_repo_validation_command", lambda current_repo: "bad((")
    monkeypatch.setattr(
        lfa,
        "update_recent_state",
        lambda current_repo, test_cmd, mode, success, artifact_dir=None, target="", files_changed=None, confidence="", blocked_reason="": updates.append((test_cmd, str(success), blocked_reason)) or Path("/tmp/state.json"),
    )

    result = lfa.run_repo_validation_command(
        repo,
        "bad((",
        mode="finalization-prepare",
        confidence="finalization-prepare",
    )

    assert result["ok"] is True
    assert result["validation_result"] == "success"
    assert result["validation_error_type"] == "invalid_validation_command"
    assert result["fallback_validation_used"] is True
    assert result["fallback_validation_result"] == "passed"
    assert result["validation_command_used"] == "pytest -q"
    assert updates[0][1] == "success"
    assert "Validation command failed; used fallback pytest validation." in updates[0][2]
    assert calls == ["bad((", "pytest -q"]


def test_run_repo_validation_command_real_test_failure_does_not_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "local_fix_agent.py").write_text("print('ok')\n")
    updates: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (1, "FAILED tests/test_local_fix_agent_publish.py::test_x\nE       assert 1 == 2"),
    )
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "repo_files", lambda current_repo: ["local_fix_agent.py", "tests/test_local_fix_agent_publish.py"])
    monkeypatch.setattr(
        lfa,
        "update_recent_state",
        lambda current_repo, test_cmd, mode, success, artifact_dir=None, target="", files_changed=None, confidence="", blocked_reason="": updates.append((test_cmd, str(success), blocked_reason)) or Path("/tmp/state.json"),
    )

    result = lfa.run_repo_validation_command(
        repo,
        "pytest -q",
        mode="finalization-prepare",
        confidence="finalization-prepare",
    )

    assert result["ok"] is False
    assert result["validation_result"] == "failed"
    assert result["validation_error_type"] == "assertion_mismatch"
    assert result["fallback_validation_used"] is False
    assert result["fallback_validation_result"] == "not_needed"
    assert result["validation_command_used"] == "pytest -q"
    assert updates[0][0] == "pytest -q"


def test_run_repo_validation_command_invalid_command_uses_py_compile_after_invalid_pytest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "local_fix_agent.py").write_text("print('ok')\n")
    calls: list[object] = []

    def fake_run_subprocess(command, cwd, shell=False):
        calls.append(command)
        if shell and command in {"bad((", "stillbad((", "pytest -q"}:
            return 2, '/bin/sh: 1: command not found'
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(lfa, "repo_files", lambda current_repo: ["local_fix_agent.py", "tests/test_local_fix_agent_publish.py"])
    monkeypatch.setattr(lfa, "latest_repo_validation_command", lambda current_repo: "stillbad((")
    monkeypatch.setattr(lfa, "update_recent_state", lambda *args, **kwargs: Path("/tmp/state.json"))

    result = lfa.run_repo_validation_command(
        repo,
        "bad((",
        mode="finalization-prepare",
        confidence="finalization-prepare",
    )

    assert result["ok"] is True
    assert result["validation_error_type"] == "invalid_validation_command"
    assert result["fallback_validation_used"] is True
    assert result["fallback_validation_result"] == "passed"
    assert "py_compile" in result["validation_command_used"]
    assert calls == ["bad((", "stillbad((", "pytest -q", [lfa.sys.executable, "-m", "py_compile", "local_fix_agent.py"]]


def test_no_finalize_is_reported_as_incomplete(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        lfa.fail_incomplete_without_finalization()

    output = capsys.readouterr().out
    assert excinfo.value.code == 2
    assert "FINALIZATION SKIPPED: --no-finalize" in output
    assert "FINAL: validation succeeded, finalization skipped (incomplete)" in output


def test_docs_describe_required_finalizer() -> None:
    readme = Path("/home/tom/ai/open-swe/README.md").read_text()
    runbook = Path("/home/tom/ai/open-swe/docs/RUNBOOK.md").read_text()
    troubleshooting = Path("/home/tom/ai/open-swe/docs/TROUBLESHOOTING.md").read_text()
    fixpublish = Path("/home/tom/ai/open-swe/scripts/fixpublish.sh").read_text()

    assert "./scripts/fixpublish.sh" in readme
    assert "--no-finalize" in readme
    assert "./scripts/fixpublish.sh" in runbook
    assert "--no-finalize" in runbook
    assert "finalization skipped (incomplete)" in troubleshooting
    assert "--ensure-validation-record" in fixpublish
    assert '--repo "$ROOT_DIR" --publish-only --publish-pr' in fixpublish


def test_run_post_success_publish_skips_on_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "publish_validated_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publish should not run after failed validation")),
    )

    summary = lfa.run_post_success_publish(
        Path("/tmp/repo"),
        "pytest -q",
        1,
        "MEDIUM",
        None,
        [],
        "",
        False,
        False,
        False,
        "",
        "",
        None,
        [],
        False,
        "validated-run",
        False,
        True,
    )

    assert summary["validation_result"] == "failed"
    assert summary["publish_requested"] is True
    assert summary["publish_triggered"] is False
    assert summary["publish_result"] == "blocked"
    assert summary["publish_reason"] == "blocked_by_validation"
    assert "validation_result=failed" in summary["publish_detail_reason"]
    assert summary["pr_created_or_reused"] is False


def test_run_post_success_publish_uses_current_repo_state_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_publish_current_repo_state(
        repo: Path,
        publish_branch: str,
        publish_pr: bool,
        publish_merge: bool,
        publish_merge_local_main: bool,
        publish_message: str,
        target: str,
        dry_run_mode: bool,
        validation_state: str = "success",
        validation_detail: str = "",
        force_publish: bool = False,
        validation_commit_match: bool = False,
        fingerprint_match: bool = False,
        last_validated_commit: str = "",
        current_commit: str = "",
        validation_age_seconds: int = -1,
        auto_revalidated: bool = False,
        validation_reused: bool = False,
        auto_revalidation_result: str = "not_needed",
        auto_stage_safe_paths: bool = True,
        auto_remediate_blockers: bool = True,
        explain_staging: bool = False,
    ) -> dict:
        captured["repo"] = repo
        captured["publish_branch"] = publish_branch
        captured["validation_state"] = validation_state
        captured["validation_detail"] = validation_detail
        captured["force_publish"] = force_publish
        captured["validation_commit_match"] = validation_commit_match
        captured["fingerprint_match"] = fingerprint_match
        captured["auto_revalidated"] = auto_revalidated
        captured["validation_reused"] = validation_reused
        captured["auto_revalidation_result"] = auto_revalidation_result
        captured["auto_stage_safe_paths"] = auto_stage_safe_paths
        captured["auto_remediate_blockers"] = auto_remediate_blockers
        captured["explain_staging"] = explain_staging
        return {
            "published": True,
            "publish_scope": "current_repo_state",
            "triggered": True,
            "validation_state": validation_state,
            "publish_reason": "validated",
            "final": {"status": "success"},
            "verification": {"reason": ""},
        }

    monkeypatch.setattr(lfa, "publish_current_repo_state", fake_publish_current_repo_state)

    summary = lfa.run_post_success_publish(
        Path("/tmp/repo"),
        "n/a",
        0,
        "n/a",
        None,
        [],
        "feature/publish",
        True,
        False,
        False,
        "",
        "",
        None,
        [],
        False,
        "current-repo-state",
        True,
        True,
    )

    assert summary["publish_triggered"] is True
    assert summary["publish_result"] == "success"
    assert summary["publish_mode"] == "current-repo-state"
    assert summary["publish_reason"] == "validated"
    assert captured["publish_branch"] == "feature/publish"
    assert captured["validation_state"] == "success"
    assert captured["validation_commit_match"] is True
    assert captured["auto_remediate_blockers"] is True


def test_run_post_success_publish_without_publish_flag_does_not_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "publish_validated_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publish should not run when not requested")),
    )

    summary = lfa.run_post_success_publish(
        Path("/tmp/repo"),
        "pytest -q",
        1,
        "MEDIUM",
        None,
        ["local_fix_agent.py"],
        "",
        False,
        False,
        False,
        "",
        "",
        None,
        [],
        False,
        "validated-run",
        True,
        False,
    )

    assert summary["validation_result"] == "success"
    assert summary["publish_requested"] is False
    assert summary["publish_triggered"] is False
    assert summary["publish_result"] == "not_requested"


def test_print_post_success_publish_summary_uses_required_real_run_banners(capsys: pytest.CaptureFixture[str]) -> None:
    summary = {
        "validation_result": "success",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_mode": "validated-run",
        "publish_result": "success",
        "publish_reason": "",
        "pr_created_or_reused": True,
        "pr_merged": False,
        "local_main_synced": False,
    }

    lfa.print_post_success_publish_summary(summary)
    print(lfa.format_final_operator_summary(summary))

    out = capsys.readouterr().out
    assert "=== VALIDATION RESULT ===" in out
    assert "=== POST-SUCCESS PUBLISH ===" in out
    assert "=== PUBLISH RESULT ===" in out
    assert "validation_result: success" in out
    assert "publish_requested: true" in out
    assert "publish_triggered: true" in out
    assert "publish_result: success" in out
    assert "FINAL: validation succeeded, publish succeeded" in out
    assert "Example successful repair + publish summary" not in out
    assert "https://github.com/example/repo/pull/123" not in out
    assert "fix-agent/20260320-123456" not in out


def test_run_post_success_publish_requested_but_publish_flow_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_publish_validated_run(*args, **kwargs) -> dict:
        return {
            "publish_scope": "validated_run",
            "triggered": True,
            "publish_reason": "validated",
            "reason": "publish flow was skipped because no executor ran",
            "final": {"status": "failed"},
            "verification": {"reason": ""},
        }

    monkeypatch.setattr(lfa, "publish_validated_run", fake_publish_validated_run)

    summary = lfa.run_post_success_publish(
        Path("/tmp/repo"),
        "pytest -q",
        1,
        "MEDIUM",
        None,
        ["local_fix_agent.py"],
        "",
        False,
        False,
        False,
        "",
        "",
        None,
        [],
        False,
        "validated-run",
        True,
        True,
    )

    assert summary["publish_requested"] is True
    assert summary["publish_triggered"] is True
    assert summary["publish_result"] == "failed"
    assert summary["publish_reason"] == "validated"
    assert summary["publish_detail_reason"] == "publish flow was skipped because no executor ran"


def test_format_final_operator_summary_distinguishes_publish_failure() -> None:
    summary = {
        "validation_result": "success",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_result": "failed",
        "publish_reason": "push failed",
    }

    assert lfa.format_final_operator_summary(summary) == "FINAL: validation succeeded, publish failed"


def test_format_final_operator_summary_distinguishes_publish_blocked_from_mergeability_blocked() -> None:
    summary = {
        "validation_result": "success",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_result": "blocked",
        "publish_reason": "staging blocked",
        "final_workflow_result": "blocked",
    }

    assert lfa.format_final_operator_summary(summary) == "FINAL: validation succeeded, publish blocked"


def test_format_final_operator_summary_prefers_previous_pr_on_noop_reuse() -> None:
    summary = {
        "validation_result": "blocked",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_result": "noop",
        "publish_reason": "matched previous successful publish fingerprint",
        "previous_pr_url": "https://github.com/octocat/demo/pull/7",
    }

    assert lfa.format_final_operator_summary(summary) == "FINAL: already published — PR: https://github.com/octocat/demo/pull/7"


def test_publish_validated_run_can_create_and_merge_pr_when_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    saved: list[dict] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: saved.append(state))
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(
        lfa,
        "build_publish_preflight",
        lambda current_repo, branch: make_preflight(
            origin_url="git@github.com:upstream/demo.git",
            origin_owner="upstream",
            current_user="contributor",
            requires_fork=True,
        ),
    )
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
        },
    )
    monkeypatch.setattr(
        lfa,
        "locally_verify_pr_mergeability",
        lambda current_repo, base_branch, head_branch: {
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
            "pr_mergeability_reason": "",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "create"]:
            return 0, "https://github.com/upstream/demo/pull/9\n"
        if command[:3] == ["gh", "pr", "merge"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, True, False, "", "", None, [], False
    )

    assert result["published"] is True
    assert result["final"]["status"] == "success"
    assert result["pr_created_or_reused"] is True
    assert result["pr_merged"] is True
    assert result["pr_status"] == "created"
    assert result["pr_url"] == "https://github.com/upstream/demo/pull/9"
    assert result["verification"]["synced"] is True
    assert saved[-1]["last_branch"] == "feature"
    assert saved[-1]["last_commit"] == "abc123"
    assert saved[-1]["last_pr_url"] == "https://github.com/upstream/demo/pull/9"
    assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in commands)
    assert any(cmd[:3] == ["gh", "pr", "merge"] for cmd in commands)


def test_publish_validated_run_marks_failed_verification_when_remote_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "def456",
            "synced": False,
            "reason": "publish verification failed: local HEAD abc123 does not match origin/feature def456",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["published"] is False
    assert result["final"]["status"] == "failed_verification"
    assert result["reason"] == "publish verification failed: local HEAD abc123 does not match origin/feature def456"


def test_publish_success_reports_mergeable_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/7")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "success"
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/7"
    assert result["pr_mergeable"] == "true"
    assert result["pr_conflicts_detected"] is False
    assert result["pr_mergeable_final"] == "true"
    assert result["pr_conflicts_detected_final"] is False


def test_resolve_pr_mergeability_unknown_uses_local_fallback_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "verify_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "PR mergeability is not yet known (UNKNOWN)",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
        },
    )
    monkeypatch.setattr(
        lfa,
        "locally_verify_pr_mergeability",
        lambda current_repo, base_branch, head_branch: {
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
            "pr_mergeability_reason": "",
        },
    )

    result = lfa.resolve_pr_mergeability(Path("/tmp/repo"), "https://github.com/octocat/demo/pull/11")

    assert result["pr_mergeable"] == "unknown"
    assert result["pr_mergeability_source"] == "local_fallback"
    assert result["pr_mergeable_final"] == "true"
    assert result["pr_conflicts_detected_final"] is False


def test_resolve_pr_mergeability_unknown_uses_local_fallback_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "verify_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "PR mergeability is not yet known (UNKNOWN)",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
        },
    )
    monkeypatch.setattr(
        lfa,
        "locally_verify_pr_mergeability",
        lambda current_repo, base_branch, head_branch: {
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "false",
            "pr_conflicts_detected_final": True,
            "pr_mergeability_reason": "local mergeability check found conflicts against origin/main: app.py",
        },
    )

    result = lfa.resolve_pr_mergeability(Path("/tmp/repo"), "https://github.com/octocat/demo/pull/12")

    assert result["pr_mergeability_source"] == "local_fallback"
    assert result["pr_mergeable_final"] == "false"
    assert result["pr_conflicts_detected_final"] is True


def test_resolve_pr_mergeability_local_conflict_overrides_github_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "verify_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
        },
    )
    monkeypatch.setattr(
        lfa,
        "locally_verify_pr_mergeability",
        lambda current_repo, base_branch, head_branch: {
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "false",
            "pr_conflicts_detected_final": True,
            "pr_mergeability_reason": "local mergeability check found conflicts against origin/main: app.py",
        },
    )

    result = lfa.resolve_pr_mergeability(Path("/tmp/repo"), "https://github.com/octocat/demo/pull/13")

    assert result["pr_mergeable"] == "true"
    assert result["pr_mergeability_source"] == "local_fallback"
    assert result["pr_mergeable_final"] == "false"
    assert result["pr_conflicts_detected_final"] is True


def test_locally_verify_pr_mergeability_executes_merge_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_git_sequence_state", lambda current_repo: "merge")
    monkeypatch.setattr(lfa, "conflicted_git_paths", lambda current_repo: [])

    def fake_run_subprocess(command, cwd, shell=False):
        commands.append(command)
        if command == ["git", "fetch", "origin"]:
            return 0, ""
        if command == ["git", "merge", "origin/main", "--no-commit", "--no-ff"]:
            return 0, ""
        if command == ["git", "merge", "--abort"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.locally_verify_pr_mergeability(Path("/tmp/repo"), "main", "feature")

    assert result["pr_mergeability_source"] == "local_fallback"
    assert result["pr_mergeable_final"] == "true"
    assert result["pr_conflicts_detected_final"] is False
    assert ["git", "fetch", "origin"] in commands
    assert ["git", "merge", "origin/main", "--no-commit", "--no-ff"] in commands
    assert ["git", "merge", "--abort"] in commands


def test_align_branch_with_base_before_publish_already_aligned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "3 0\n"
        raise AssertionError(command)

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.align_branch_with_base_before_publish(
        repo,
        branch="feature",
        base_branch="main",
        validation_command="pytest -q",
    )

    assert result["prepublish_base_alignment_attempted"] is False
    assert result["alignment_needed"] is False
    assert result["alignment_result"] == "not_needed"
    assert result["validation_rerun_after_alignment"] is False


def test_align_branch_with_base_before_publish_merges_and_reruns_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    commits = iter(["abc123", "def456"])
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: next(commits))
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            True,
            "",
            {"merge_conflicts_detected": False, "merge_result": "not_needed", "validation_result_after_merge": "not_run"},
        ),
    )
    monkeypatch.setattr(
        lfa,
        "ensure_validation_record_for_current_commit",
        lambda current_repo, validation_command="", target="": {
            "ok": True,
            "validation_record_created": True,
            "validation_record_reused": False,
            "validation_commit": "def456",
            "validation_result": "success",
            "validation_command": validation_command,
            "reason": "validated",
        },
    )

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "2 1\n"
        raise AssertionError(command)

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.align_branch_with_base_before_publish(
        repo,
        branch="feature",
        base_branch="main",
        validation_command="pytest -q",
    )

    assert result["prepublish_base_alignment_attempted"] is True
    assert result["branch_diverged"] is True
    assert result["alignment_needed"] is True
    assert result["alignment_result"] == "success"
    assert result["alignment_changed_commit"] is True
    assert result["validation_rerun_after_alignment"] is True
    assert result["validation_result_after_alignment"] == "success"


def test_align_branch_with_base_before_publish_blocks_on_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(
        lfa,
        "run_sync_operation_with_conflict_hook",
        lambda current_repo, sync_operation, command, validation_command="", no_auto_conflict_resolution_after_sync=False: (
            False,
            "overlapping code conflict with low merge confidence",
            {
                "merge_conflicts_detected": True,
                "conflicted_files": ["local_fix_agent.py"],
                "resolution_strategy_per_file": {"local_fix_agent.py": "blocked_ambiguous_code_conflict"},
                "validation_result_after_merge": "not_run",
                "merge_result": "blocked",
                "blocked_reason": "overlapping code conflict with low merge confidence",
            },
        ),
    )

    def fake_run_subprocess(command, cwd, shell=False):
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 2\n"
        raise AssertionError(command)

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.align_branch_with_base_before_publish(
        repo,
        branch="feature",
        base_branch="main",
        validation_command="pytest -q",
    )

    assert result["prepublish_base_alignment_attempted"] is True
    assert result["alignment_result"] == "blocked"
    assert result["alignment_block_reason"] == "overlapping code conflict with low merge confidence"
    assert result["merge_conflict_result"]["conflicted_files"] == ["local_fix_agent.py"]


def test_publish_success_conflicting_pr_auto_repair_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/8")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "false",
            "pr_conflicts_detected": True,
            "pr_mergeability_reason": "PR has merge conflicts against its base branch (DIRTY)",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "false",
            "pr_conflicts_detected_final": True,
        },
    )
    monkeypatch.setattr(
        lfa,
        "repair_pr_mergeability",
        lambda current_repo, pr_url, validation_command="": {
            "attempted": True,
            "result": "success",
            "reason": "",
            "mergeability": {
                "pr_mergeable": "true",
                "pr_conflicts_detected": False,
                "pr_mergeability_reason": "",
                "pr_mergeability_source": "github",
                "pr_mergeable_final": "true",
                "pr_conflicts_detected_final": False,
            },
            "merge_conflict_result": None,
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "success"
    assert result["final_workflow_result"] == "success"
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/8"
    assert result["pr_mergeable"] == "true"
    assert result["pr_conflicts_detected"] is False
    assert result["pr_mergeable_final"] == "true"
    assert result["pr_conflicts_detected_final"] is False
    assert result["pr_mergeability_repair_attempted"] is True
    assert result["pr_mergeability_repair_result"] == "success"


def test_publish_success_conflicting_pr_auto_repair_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/8")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "false",
            "pr_conflicts_detected": True,
            "pr_mergeability_reason": "PR has merge conflicts against its base branch (DIRTY)",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "false",
            "pr_conflicts_detected_final": True,
        },
    )
    monkeypatch.setattr(
        lfa,
        "repair_pr_mergeability",
        lambda current_repo, pr_url, validation_command="": {
            "attempted": True,
            "result": "blocked",
            "reason": "config conflict is not clearly compatible",
            "mergeability": {
                "pr_mergeable": "false",
                "pr_conflicts_detected": True,
                "pr_mergeability_reason": "PR has merge conflicts against its base branch (DIRTY)",
                "pr_mergeability_source": "github",
                "pr_mergeable_final": "false",
                "pr_conflicts_detected_final": True,
            },
            "merge_conflict_result": {
                "merge_conflicts_detected": True,
                "conflicted_files": ["settings.json"],
                "resolution_strategy_per_file": {"settings.json": "blocked_ambiguous_config_conflict"},
                "validation_result_after_merge": "not_run",
                "merge_result": "blocked",
                "blocked_reason": "config conflict is not clearly compatible",
                "sync_operation_attempted": True,
                "sync_operation": "pr_mergeability_repair",
                "conflict_source": "pr_mergeability_repair",
                "auto_conflict_resolution_attempted": True,
            },
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "success"
    assert result["final_workflow_result"] == "blocked"
    assert result["pr_mergeability_repair_attempted"] is True
    assert result["pr_mergeability_repair_result"] == "blocked"
    assert result["reason"] == "config conflict is not clearly compatible"
    assert result["merge_conflict_result"]["conflicted_files"] == ["settings.json"]


def test_publish_success_conflicting_pr_validation_failure_after_repair_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/10")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "false",
            "pr_conflicts_detected": True,
            "pr_mergeability_reason": "PR has merge conflicts against its base branch (DIRTY)",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "false",
            "pr_conflicts_detected_final": True,
        },
    )
    monkeypatch.setattr(
        lfa,
        "repair_pr_mergeability",
        lambda current_repo, pr_url, validation_command="": {
            "attempted": True,
            "result": "blocked",
            "reason": "validation failed after merge resolution",
            "mergeability": {
                "pr_mergeable": "false",
                "pr_conflicts_detected": True,
                "pr_mergeability_reason": "PR has merge conflicts against its base branch (DIRTY)",
                "pr_mergeability_source": "github",
                "pr_mergeable_final": "false",
                "pr_conflicts_detected_final": True,
            },
            "merge_conflict_result": {
                "merge_conflicts_detected": True,
                "conflicted_files": ["app.py"],
                "resolution_strategy_per_file": {"app.py": "structured_merge_combined_logic"},
                "validation_result_after_merge": "failed",
                "merge_result": "blocked",
                "blocked_reason": "validation failed after merge resolution",
                "sync_operation_attempted": True,
                "sync_operation": "pr_mergeability_repair",
                "conflict_source": "pr_mergeability_repair",
                "auto_conflict_resolution_attempted": True,
            },
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["final_workflow_result"] == "blocked"
    assert result["pr_mergeability_repair_result"] == "blocked"
    assert result["reason"] == "validation failed after merge resolution"


def test_publish_success_reports_unknown_pr_mergeability(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/9")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "PR mergeability is not yet known (UNKNOWN)",
            "pr_mergeability_source": "github",
            "pr_mergeable_final": "unknown",
            "pr_conflicts_detected_final": False,
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["final"]["status"] == "success"
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/9"
    assert result["pr_mergeable"] == "unknown"
    assert result["pr_conflicts_detected"] is False
    assert result["pr_mergeable_final"] == "unknown"
    assert result["pr_conflicts_detected_final"] is False
    assert "not yet known" in result["pr_mergeability_reason"]
    assert result["pr_mergeability_repair_attempted"] is False


def test_publish_success_with_prepublish_alignment_avoids_postpublish_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": "M  local_fix_agent.py",
            "diff_output": "",
            "diff_files_detected": ["local_fix_agent.py"],
            "last_published_commit": baseline_commit,
            "current_commit": current_commit,
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    commits = iter(["abc123", "abc123", "def456", "def456", "def456"])
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: next(commits))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "resolve_prepublish_base_branch", lambda current_repo, branch, default_branch: ("main", "https://github.com/octocat/demo/pull/9"))
    monkeypatch.setattr(
        lfa,
        "align_branch_with_base_before_publish",
        lambda current_repo, branch, base_branch, validation_command="", no_auto_conflict_resolution_after_sync=False: {
            "prepublish_base_alignment_attempted": True,
            "base_branch": base_branch,
            "branch_diverged": True,
            "alignment_needed": True,
            "alignment_result": "success",
            "alignment_changed_commit": True,
            "validation_rerun_after_alignment": True,
            "alignment_block_reason": "",
            "merge_conflict_result": None,
            "validation_result_after_alignment": "success",
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "def456",
            "remote_head": "def456",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "resolve_pr_mergeability",
        lambda current_repo, pr_url: {
            "pr_mergeable": "true",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "",
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
        },
    )
    monkeypatch.setattr(
        lfa,
        "repair_pr_mergeability",
        lambda current_repo, pr_url, validation_command="": (_ for _ in ()).throw(AssertionError("repair should not run")),
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (0, "") if command == ["git", "add", "-A", "--", "local_fix_agent.py"] or command[:2] == ["git", "commit"] or command[:3] == ["git", "push", "-u"] else (0, ""),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, False, False, "", "", None, [], False
    )

    assert result["alignment_result"] == "success"
    assert result["alignment_changed_commit"] is True
    assert result["validation_rerun_after_alignment"] is True
    assert result["pr_mergeability_repair_attempted"] is False
    assert result["pr_mergeability_repair_result"] == "not_needed"


def test_publish_current_repo_state_auto_creates_branch_from_main_in_non_interactive_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    branch_state = {"current": "main"}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: branch_state["current"])
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight(branch="main"))
    monkeypatch.setattr(lfa, "make_publish_branch_name", lambda: "fix-agent/auto-branch")
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/7")
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "checkout", "-b", "fix-agent/auto-branch"]:
            branch_state["current"] = "fix-agent/auto-branch"
            return 0, ""
        if command == ["git", "add", "-A"]:
            return 0, ""
        if command == ["git", "diff", "--cached", "--quiet"]:
            return 1, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", True, False, False, "", "", False)

    assert result["published"] is True
    assert result["branch"] == "fix-agent/auto-branch"
    assert "auto-created publish branch from default branch" in result["actions"]
    assert result["pr_already_exists"] is True
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/7"
    assert commands[0] == ["git", "checkout", "-b", "fix-agent/auto-branch"]


def test_publish_current_repo_state_main_branch_with_only_ignored_changes_noops(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight(branch="main"))
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": " M .ai_publish_state.json\n M .fix_agent_docs_state.json",
            "meaningful_changes_detected": False,
            "meaningful_paths": [],
            "ignored_changes": [".ai_publish_state.json", ".fix_agent_docs_state.json"],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", True, False, False, "", "", False)

    assert result["published"] is False
    assert result["triggered"] is False
    assert result["final"]["status"] == "noop"
    assert result["reason"] == "no meaningful changes to publish"
    assert result["auto_stage_attempted"] is False
    assert result["auto_stage_result"] == "not_needed"
    assert result["ignored_changes"] == [".ai_publish_state.json", ".fix_agent_docs_state.json"]
    assert result["meaningful_paths"] == []
    assert commands == [
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--short", "--untracked-files=all"],
    ]


def test_publish_current_repo_state_only_state_file_change_uses_real_classification_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight(branch="main"))

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "status", "--short", "--untracked-files=all"]:
            return 0, " M .ai_publish_state.json\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", True, False, False, "", "", False)

    assert result["published"] is False
    assert result["triggered"] is False
    assert result["final"]["status"] == "noop"
    assert result["reason"] == "no meaningful changes to publish"
    assert result["auto_stage_attempted"] is False
    assert result["auto_stage_result"] == "not_needed"
    assert result["meaningful_changes_detected"] is False
    assert result["meaningful_paths"] == []
    assert result["ignored_changes"] == [".ai_publish_state.json"]
    assert result["staging_summary"] == {"auto_staged": 0, "ignored": 1, "blocked": 0}
    assert result["staging_decision_reason"] == "only excluded/internal files were detected"
    assert result["file_decisions"] == [
        {
            "path": ".ai_publish_state.json",
            "file_type": "state",
            "classification_source": "explicit_ignore",
            "publishable": False,
            "publish_reason": "internal state file",
            "tracked": True,
            "staged": False,
            "unstaged": True,
            "untracked": False,
            "action": "ignored",
            "reason": "internal state file",
        }
    ]
    assert commands == [
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--short", "--untracked-files=all"],
        ["git", "status", "--short", "--untracked-files=all"],
    ]


def test_publish_current_repo_state_publishes_docs_code_and_tests_while_excluding_state_file(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    publishable_paths = [
        "docs/README.md",
        "local_fix_agent.py",
        "tests/test_local_fix_agent_publish.py",
    ]
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: publishable_paths)
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": (
                " M docs/README.md\n"
                " M local_fix_agent.py\n"
                " M tests/test_local_fix_agent_publish.py\n"
                " M .ai_publish_state.json\n"
            ),
            "meaningful_changes_detected": True,
            "meaningful_paths": publishable_paths,
            "ignored_changes": [".ai_publish_state.json"],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": (
                "M  docs/README.md\n"
                "M  local_fix_agent.py\n"
                "M  tests/test_local_fix_agent_publish.py\n"
                " M .ai_publish_state.json\n"
                if stage_state["after_add"]
                else
                " M docs/README.md\n"
                " M local_fix_agent.py\n"
                " M tests/test_local_fix_agent_publish.py\n"
                " M .ai_publish_state.json\n"
            ),
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": False,
            "staged_paths": publishable_paths if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else publishable_paths,
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", *publishable_paths]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["ignored_changes"] == [".ai_publish_state.json"]
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] == "success"
    assert result["staging_summary"] == {"auto_staged": 3, "ignored": 1, "blocked": 0}
    assert any(
        item["path"] == ".ai_publish_state.json"
        and item["file_type"] == "state"
        and item["action"] == "ignored"
        and item["reason"] == "internal state file"
        for item in result["file_decisions"]
    )
    assert result["working_tree"]["staged_paths"] == publishable_paths
    assert ["git", "add", "-A", "--", *publishable_paths] in commands
    assert ["git", "commit", "-m", "chore: publish current repo state"] in commands
    assert ["git", "push", "-u", "origin", "feature"] in commands


def test_publish_current_repo_state_blocks_if_publishable_changes_remain_unstaged_after_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["docs/README.md", "local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M docs/README.md\n M local_fix_agent.py\n M .ai_publish_state.json\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["docs/README.md", "local_fix_agent.py"],
            "ignored_changes": [".ai_publish_state.json"],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": (
                " M docs/README.md\n"
                " M local_fix_agent.py\n"
                " M .ai_publish_state.json\n"
                if not stage_state["after_add"]
                else
                " M docs/README.md\n"
                " M local_fix_agent.py\n"
                " M .ai_publish_state.json\n"
            ),
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": ["docs/README.md", "local_fix_agent.py"],
            "untracked_paths": [],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "docs/README.md", "local_fix_agent.py"] or command == ["git", "add", "-A", "--", "local_fix_agent.py", "docs/README.md"]:
            stage_state["after_add"] = True
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "blocked"
    assert result["published"] is False
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] in {"partial", "blocked"}
    assert result["staging_summary"]["blocked"] == 0
    assert result["safe_stage_candidate_paths"] == ["docs/README.md", "local_fix_agent.py"]
    assert result["true_blockers"] == []
    assert result["blocker_count"] == 0
    assert result["publishable_ready"] is False
    assert "publishable changes remained unstaged after staging" in result["reason"]
    assert "docs/README.md" in result["reason"]
    assert not any(cmd[:2] == ["git", "commit"] for cmd in commands)
    assert not any(cmd[:3] == ["git", "push", "-u"] for cmd in commands)


def test_publish_current_repo_state_reuses_existing_pr_and_pushes_new_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/7")
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M local_fix_agent.py\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py" if stage_state["after_add"] else " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": False,
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "locally_verify_pr_mergeability",
        lambda current_repo, base_branch, head_branch: {
            "pr_mergeability_source": "local_fallback",
            "pr_mergeable_final": "true",
            "pr_conflicts_detected_final": False,
            "pr_mergeability_reason": "",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(
                {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", True, False, False, "", "", False)

    assert result["published"] is True
    assert result["auto_stage_attempted"] is True
    assert result["auto_stage_result"] == "success"
    assert result["pr_already_exists"] is True
    assert result["pr_created_or_reused"] is True
    assert result["pr_url"] == "https://github.com/octocat/demo/pull/7"
    assert ["git", "commit", "-m", "chore: publish current repo state"] in commands
    assert ["git", "push", "-u", "origin", "feature"] in commands
    assert not any(cmd[:3] == ["gh", "pr", "create"] for cmd in commands)


def test_publish_current_repo_state_unsafe_file_blocks_with_manual_staging_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["notes.txt"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M notes.txt\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["notes.txt"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": " M notes.txt",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": ["notes.txt"],
            "untracked_paths": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_subprocess",
        lambda command, cwd, shell=False: (_ for _ in ()).throw(AssertionError(f"unexpected command: {command}")),
    )

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "blocked"
    assert result["auto_stage_attempted"] is False
    assert result["auto_stage_result"] == "blocked"
    assert result["remaining_unstaged_paths"] == ["notes.txt"]
    assert result["remaining_unstaged"] == [
        {
            "path": "notes.txt",
            "file_type": "artifact",
            "classification_source": "extension",
            "publishable": False,
            "tracked": True,
            "staged": False,
            "unstaged": True,
            "untracked": False,
            "reason": "generated/artifact file",
        }
    ]
    assert result["staging_summary"] == {"auto_staged": 0, "ignored": 0, "blocked": 1}
    assert result["file_decisions"] == [
        {
            "path": "notes.txt",
            "file_type": "artifact",
            "classification_source": "extension",
            "publishable": False,
            "publish_reason": "generated/artifact file",
            "tracked": True,
            "staged": False,
            "unstaged": True,
            "untracked": False,
            "action": "true_blocker",
            "reason": "unknown/generated artifact; requires manual review",
        }
    ]
    assert result["safe_staged_paths"] == []
    assert result["ignored_nonblocking_paths"] == []
    assert result["true_blockers"] == [{"path": "notes.txt", "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"}]
    assert result["blocker_count"] == 1
    assert result["publishable_ready"] is False
    assert result["staging_decision_reason"] == "one or more files were classified as unknown/artifact and require manual review"
    assert result["staging_reason"] == "ambiguous or unsafe file requires manual review"
    assert "git add -- notes.txt" in result["next_action"]
    assert result["blocked_file_analysis"][0]["recommended_action"] == "inspect manually before staging"
    assert "git restore --staged -- notes.txt" in result["blocked_file_analysis"][0]["recommended_commands"]


def test_publish_current_repo_state_strict_no_auto_remediate_preserves_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_name = "c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt"
    (repo / artifact_name).write_text("temporary artifact\n")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": f" M local_fix_agent.py\n?? {artifact_name}\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": f"M  local_fix_agent.py\n?? {artifact_name}" if stage_state["after_add"] else f" M local_fix_agent.py\n?? {artifact_name}",
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": True,
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": [artifact_name],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False, auto_remediate_blockers=False)

    assert result["final"]["status"] == "blocked"
    assert result["blocker_remediation_attempted"] is False
    assert result["blocker_remediation_result"] == "not_needed"
    assert result["true_blockers"] == [{"path": artifact_name, "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"}]
    assert result["remaining_true_blockers"] == [{"path": artifact_name, "file_type": "artifact", "reason": "unknown/generated artifact; requires manual review"}]
    assert (repo / artifact_name).exists()
    assert not any(cmd[:2] == ["git", "commit"] for cmd in commands)


def test_publish_current_repo_state_resolves_safe_artifact_but_blocks_on_ambiguous_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact_name = "c7c5dc0cfd3d57af083f1ae879ccfb868f2f2e76.txt"
    (repo / artifact_name).write_text("temporary artifact\n")
    commands: list[list[str]] = []
    stage_state = {"after_add": False}
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py", "settings.data"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": f" M local_fix_agent.py\n?? {artifact_name}\n?? settings.data\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py", "settings.data"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": (
                "M  local_fix_agent.py\n?? settings.data\n"
                if stage_state["after_add"] and not (repo / artifact_name).exists()
                else f" M local_fix_agent.py\n?? {artifact_name}\n?? settings.data\n"
                if not stage_state["after_add"]
                else f"M  local_fix_agent.py\n?? {artifact_name}\n?? settings.data\n"
            ),
            "clean": False,
            "has_unstaged": not stage_state["after_add"],
            "has_staged": stage_state["after_add"],
            "has_untracked": True,
            "staged_paths": ["local_fix_agent.py"] if stage_state["after_add"] else [],
            "unstaged_paths": [] if stage_state["after_add"] else ["local_fix_agent.py"],
            "untracked_paths": ["settings.data"] + ([artifact_name] if (repo / artifact_name).exists() else []),
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["after_add"] = True
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["final"]["status"] == "blocked"
    assert result["blocker_remediation_attempted"] is True
    assert result["blocker_remediation_result"] in {"partial", "blocked"}
    assert result["auto_removed_paths"] == [artifact_name]
    assert result["remaining_true_blockers"] == [{"path": "settings.data", "file_type": "unknown", "reason": "unknown/generated artifact; requires manual review"}]
    assert result["true_blockers"] == [{"path": "settings.data", "file_type": "unknown", "reason": "unknown/generated artifact; requires manual review"}]
    assert not (repo / artifact_name).exists()
    assert any(item["path"] == "settings.data" for item in result["blocked_file_analysis"])


def test_publish_current_repo_state_no_auto_stage_blocks_with_exact_manual_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(
        lfa,
        "detect_publish_environment",
        lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False},
    )
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "publish_meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M local_fix_agent.py\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_publish_working_tree",
        lambda current_repo: {
            "status_output": " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": ["local_fix_agent.py"],
            "untracked_paths": [],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "fetch", "origin", "main"]:
            return 0, ""
        if command == ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return 0, "1 0\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False, auto_stage_safe_paths=False)

    assert result["final"]["status"] == "blocked"
    assert result["auto_stage_attempted"] is False
    assert result["auto_stage_result"] == "blocked"
    assert result["staging_reason"] == "automatic staging disabled by --no-auto-stage"
    assert result["staging_decision_reason"] == "automatic staging is disabled; manual staging is required for safe publishable files"
    assert result["remaining_unstaged_paths"] == ["local_fix_agent.py"]
    assert result["safe_stage_candidate_paths"] == ["local_fix_agent.py"]
    assert result["true_blockers"] == []
    assert result["blocker_count"] == 0
    assert result["publishable_ready"] is False
    assert "git add -- local_fix_agent.py" in result["next_action"]
    assert result["blocked_file_analysis"][0]["recommended_action"] == "stage and include in publish"
    assert result["blocked_analysis_summary"]["primary_next_step"] == "stage the publishable file changes, then rerun publish"
    assert not any(cmd[:3] == ["git", "add", "-A"] for cmd in commands)


def test_recommend_publish_block_action_internal_state_file() -> None:
    analysis = lfa.recommend_publish_block_action(
        Path("/tmp/repo"),
        {
            "path": ".ai_publish_state.json",
            "file_type": "state",
            "classification_source": "explicit_ignore",
            "publishable": False,
            "tracked": True,
            "staged": False,
            "unstaged": True,
            "untracked": False,
        },
    )

    assert analysis["recommended_action"] == "leave untracked / do not publish"
    assert "internal state file" in analysis["blocking_reason"]


def test_recommend_publish_block_action_generated_root_artifact() -> None:
    analysis = lfa.recommend_publish_block_action(
        Path("/tmp/repo"),
        {
            "path": "c76abc1234567890ef.txt",
            "file_type": "artifact",
            "classification_source": "pattern_match",
            "publishable": False,
            "tracked": False,
            "staged": False,
            "unstaged": False,
            "untracked": True,
        },
    )

    assert analysis["confidence"] == "high"
    assert analysis["recommended_action"] == "remove generated artifact"
    assert "rm c76abc1234567890ef.txt" in analysis["recommended_commands"]


def test_print_publish_summary_shows_staging_block_analysis(capsys: pytest.CaptureFixture[str]) -> None:
    result = lfa.make_publish_result()
    result["final"]["status"] = "blocked"
    result["blocked_file_analysis"] = [
        {
            "path": "notes.txt",
            "file_type": "artifact",
            "classification_source": "extension",
            "publishable": False,
            "confidence": "high",
            "blocking_reason": "file looks like generated output or a temporary artifact and does not match publishable patterns",
            "recommended_action": "remove generated artifact",
            "recommended_commands": ["rm notes.txt", "echo '*.txt' >> .gitignore"],
        }
    ]
    result["blocked_analysis_summary"] = {
        "blocked_count": 1,
        "primary_next_step": "remove or ignore the artifact-style file, then rerun publish",
        "fallback_next_step": "inspect the file manually if you intended to keep it in the repo",
        "rerun_command": "./scripts/fixpublish.sh",
    }

    lfa.print_publish_summary(result)

    output = capsys.readouterr().out
    assert "=== STAGING BLOCK ANALYSIS ===" in output
    assert "recommended_action: remove generated artifact" in output
    assert "rerun: ./scripts/fixpublish.sh" in output


def test_print_publish_summary_explain_staging_shows_file_decisions(capsys: pytest.CaptureFixture[str]) -> None:
    result = lfa.make_publish_result()
    result["explain_staging"] = True
    result["staging_summary"] = {"auto_staged": 1, "ignored": 1, "blocked": 1}
    result["staging_decision_reason"] = "one or more files were classified as unknown/artifact and require manual review"
    result["file_decisions"] = [
        {
            "path": "local_fix_agent.py",
            "file_type": "code",
            "classification_source": "extension",
            "publishable": True,
            "action": "auto_staged",
            "reason": "safe tracked code file",
        },
        {
            "path": ".ai_publish_state.json",
            "file_type": "state",
            "classification_source": "explicit_ignore",
            "publishable": False,
            "action": "ignored",
            "reason": "internal state file",
        },
        {
            "path": "notes.txt",
            "file_type": "artifact",
            "classification_source": "extension",
            "publishable": False,
            "action": "blocked",
            "reason": "unknown/generated artifact; requires manual review",
        },
    ]

    lfa.print_publish_summary(result)

    output = capsys.readouterr().out
    assert "=== STAGING FILE DECISIONS ===" in output
    assert "file_decision: path=local_fix_agent.py file_type=code" in output
    assert "file_decision: path=.ai_publish_state.json file_type=state" in output
    assert "file_decision: path=notes.txt file_type=artifact" in output


def test_run_prepublish_docs_stage_no_docs_impact_returns_no_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "detect_publish_docs_impact",
        lambda repo, changed_paths, publish_current_mode=False: {
            "docs_required": False,
            "docs_targets": [],
            "docs_refresh_mode": "none",
        },
    )

    result = lfa.run_prepublish_docs_stage(tmp_path, "pytest -q", ["local_fix_agent.py"])

    assert result["docs_checked_at_publish"] is True
    assert result["docs_required"] is False
    assert result["docs_updated"] is False
    assert result["docs_refresh_mode"] == "none"

    docs_reporting = lfa.summarize_docs_publish_reporting(
        docs_check_performed=result["docs_checked_at_publish"],
        docs_required=result["docs_required"],
        docs_updated=result["docs_updated"],
        blocked=result["blocked"],
        reason=result["reason"],
    )

    assert docs_reporting["docs_check_performed"] is True
    assert docs_reporting["docs_status"] == "up_to_date"
    assert docs_reporting["docs_reason"] == "no documentation changes detected"


def test_run_prepublish_docs_stage_updates_and_revalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "detect_publish_docs_impact",
        lambda repo, changed_paths, publish_current_mode=False: {
            "docs_required": True,
            "docs_targets": ["README.md"],
            "docs_refresh_mode": "patch",
        },
    )
    monkeypatch.setattr(
        lfa,
        "apply_publish_docs_updates",
        lambda repo, docs_check: {"ok": True, "updated": True, "updated_targets": ["README.md"], "reason": ""},
    )
    monkeypatch.setattr(
        lfa,
        "revalidate_publish_docs",
        lambda repo, test_cmd, publish_current_mode=False: {"ran": True, "ok": True, "command": "pytest -q", "output": ""},
    )

    result = lfa.run_prepublish_docs_stage(Path("/tmp/repo"), "pytest -q", ["local_fix_agent.py"])

    assert result["docs_required"] is True
    assert result["docs_updated"] is True
    assert result["revalidated"] is True
    assert result["revalidation_command"] == "pytest -q"
    assert result["blocked"] is False


def test_run_prepublish_docs_stage_update_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "detect_publish_docs_impact",
        lambda repo, changed_paths, publish_current_mode=False: {
            "docs_required": True,
            "docs_targets": ["README.md"],
            "docs_refresh_mode": "patch",
        },
    )
    monkeypatch.setattr(
        lfa,
        "apply_publish_docs_updates",
        lambda repo, docs_check: {"ok": False, "updated": False, "updated_targets": [], "reason": "docs refresh failed"},
    )

    result = lfa.run_prepublish_docs_stage(Path("/tmp/repo"), "pytest -q", ["local_fix_agent.py"])

    assert result["blocked"] is True
    assert result["reason"] == "docs refresh failed"


def test_publish_validated_run_docs_update_is_included_in_published_result(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py", "README.md"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py\nM  README.md",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py", "README.md"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py\nM  README.md",
            "clean": False,
            "has_unstaged": False,
            "has_staged": True,
            "has_untracked": False,
        },
    )
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "M  local_fix_agent.py\nM  README.md")
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_prepublish_docs_stage",
        lambda current_repo, test_cmd, changed_paths, publish_current_mode=False: {
            "docs_checked_at_publish": True,
            "docs_required": True,
            "docs_updated": True,
            "docs_refresh_mode": "patch",
            "docs_targets": ["README.md"],
            "blocked": False,
            "reason": "",
            "updated_targets": ["README.md"],
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A", "--", "README.md", "local_fix_agent.py"] or command == ["git", "add", "-A", "--", "local_fix_agent.py", "README.md"]:
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["published"] is True
    assert result["docs_checked_at_publish"] is True
    assert result["docs_check_performed"] is True
    assert result["docs_status"] == "updated"
    assert result["docs_reason"] == "documentation updated due to code changes"
    assert result["docs_required"] is True
    assert result["docs_updated"] is True
    assert result["docs_refresh_mode"] == "patch"
    assert result["docs_targets"] == ["README.md"]
    assert any(cmd[:3] == ["git", "add", "-A"] for cmd in commands)


def test_publish_current_repo_state_stages_docs_updated_targets_before_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path("/tmp/repo")
    commands: list[list[str]] = []
    stage_state = {"docs_added": False, "all_added": False}

    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(
        lfa,
        "build_publish_preflight",
        lambda current_repo, branch: make_preflight(origin_owner="tophat1720", current_user="tophat1720", origin_url="git@github.com:tophat1720/demo.git"),
    )
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(
        lfa,
        "publish_meaningful_changed_paths",
        lambda current_repo: ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md", "local_fix_agent.py"],
    )
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo, baseline_commit="", current_commit="HEAD": {
            "status_output": " M README.md\n M docs/RUNBOOK.md\n M docs/TROUBLESHOOTING.md\n M local_fix_agent.py\n",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md", "local_fix_agent.py"],
            "ignored_changes": [],
            "last_published_commit": "",
            "current_commit": "abc123",
            "diff_files_detected": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_prepublish_docs_stage",
        lambda current_repo, test_cmd, changed_paths, publish_current_mode=False: {
            "docs_checked_at_publish": True,
            "docs_required": True,
            "docs_updated": True,
            "docs_refresh_mode": "rewrite",
            "docs_targets": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md"],
            "blocked": False,
            "reason": "",
            "updated_targets": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md"],
        },
    )

    def fake_classify_publish_working_tree(current_repo: Path) -> dict:
        if stage_state["all_added"]:
            return {
                "status_output": "M  README.md\nM  docs/RUNBOOK.md\nM  docs/TROUBLESHOOTING.md\nM  local_fix_agent.py",
                "clean": False,
                "has_unstaged": False,
                "has_staged": True,
                "has_untracked": False,
                "staged_paths": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md", "local_fix_agent.py"],
                "unstaged_paths": [],
                "untracked_paths": [],
            }
        if stage_state["docs_added"]:
            return {
                "status_output": "M  README.md\nM  docs/RUNBOOK.md\nM  docs/TROUBLESHOOTING.md\n M local_fix_agent.py",
                "clean": False,
                "has_unstaged": True,
                "has_staged": True,
                "has_untracked": False,
                "staged_paths": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md"],
                "unstaged_paths": ["local_fix_agent.py"],
                "untracked_paths": [],
            }
        return {
            "status_output": " M README.md\n M docs/RUNBOOK.md\n M docs/TROUBLESHOOTING.md\n M local_fix_agent.py",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": ["README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md", "local_fix_agent.py"],
            "untracked_paths": [],
        }

    monkeypatch.setattr(lfa, "classify_publish_working_tree", fake_classify_publish_working_tree)
    monkeypatch.setattr(
        lfa,
        "verify_publish_sync",
        lambda current_repo, branch, remote_ref="origin": {
            "current_branch": branch,
            "upstream_branch": f"{remote_ref}/{branch}",
            "upstream_exists": True,
            "local_head": "abc123",
            "remote_head": "abc123",
            "synced": True,
            "reason": "",
        },
    )

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A", "--", "README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md"]:
            stage_state["docs_added"] = True
            return 0, ""
        if command == ["git", "add", "-A", "--", "local_fix_agent.py"]:
            stage_state["all_added"] = True
            return 0, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert ["git", "add", "-A", "--", "README.md", "docs/RUNBOOK.md", "docs/TROUBLESHOOTING.md"] in commands


def test_publish_validated_run_docs_failure_blocks_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    monkeypatch.setattr(lfa, "load_publish_state", lambda current_repo: {})
    monkeypatch.setattr(lfa, "save_publish_state", lambda current_repo, state: None)
    monkeypatch.setattr(lfa, "detect_publish_environment", lambda: {"ci": False, "github_actions": False, "interactive": False, "allow_auto_fork": False})
    monkeypatch.setattr(lfa, "is_git_repo", lambda current_repo: True)
    monkeypatch.setattr(lfa, "parse_remote_names", lambda current_repo: ["origin"])
    monkeypatch.setattr(lfa, "current_git_branch", lambda current_repo: "feature")
    monkeypatch.setattr(lfa, "detect_default_branch", lambda current_repo: "main")
    monkeypatch.setattr(lfa, "build_publish_preflight", lambda current_repo, branch: make_preflight())
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["local_fix_agent.py"])
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "M  local_fix_agent.py",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["local_fix_agent.py"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_prepublish_docs_stage",
        lambda current_repo, test_cmd, changed_paths, publish_current_mode=False: {
            "docs_checked_at_publish": True,
            "docs_required": True,
            "docs_updated": False,
            "docs_refresh_mode": "patch",
            "docs_targets": ["README.md"],
            "blocked": True,
            "reason": "docs update completed, but revalidation failed: pytest failed",
            "updated_targets": [],
        },
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, False, False, "", "", None, [], False
    )

    assert result["published"] is False
    assert result["docs_check_performed"] is True
    assert result["docs_status"] == "required_but_blocked"
    assert result["docs_reason"] == "docs update completed, but revalidation failed: pytest failed"
    assert result["final"]["status"] == "blocked"
    assert result["reason"] == "docs update completed, but revalidation failed: pytest failed"


def test_print_post_success_publish_summary_includes_docs_fields(capsys: pytest.CaptureFixture[str]) -> None:
    summary = {
        "validation_result": "success",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_mode": "validated-run",
        "publish_result": "success",
        "publish_reason": "",
        "pr_created_or_reused": True,
        "pr_merged": False,
        "local_main_synced": False,
        "docs_checked_at_publish": True,
        "docs_required": True,
        "docs_updated": True,
        "docs_refresh_mode": "patch",
        "docs_targets": ["README.md", "docs/RUNBOOK.md"],
    }

    lfa.print_post_success_publish_summary(summary)
    out = capsys.readouterr().out

    assert "docs_checked_at_publish: true" in out
    assert "docs_check_performed: true" in out
    assert "docs_status: updated" in out
    assert "docs_reason: documentation updated due to code changes" in out
    assert "docs_required: true" in out
    assert "docs_updated: true" in out
    assert "docs_refresh_mode: patch" in out
    assert "docs_targets: ['README.md', 'docs/RUNBOOK.md']" in out


def test_print_publish_summary_includes_blocked_docs_reporting(capsys: pytest.CaptureFixture[str]) -> None:
    result = {
        "final": {"status": "blocked"},
        "target": {},
        "environment": {},
        "fingerprint": {},
        "actions": [],
        "control_path": "blocked_docs",
        "state_loaded": True,
        "state_reset": False,
        "reused_fork": False,
        "transport_locked": False,
        "state_confidence": "high",
        "remote_url": "",
        "normalized_origin": "",
        "auth_transport": "https",
        "branch": "feature",
        "meaningful_changes_detected": True,
        "last_published_commit": "abc122",
        "current_publish_candidate_commit": "abc123",
        "diff_files_detected": ["local_fix_agent.py"],
        "ignored_changes": [],
        "meaningful_paths": ["local_fix_agent.py"],
        "docs_checked_at_publish": True,
        "docs_required": True,
        "docs_updated": False,
        "docs_refresh_mode": "patch",
        "docs_targets": ["README.md"],
        "base_branch": "main",
        "prepublish_base_alignment_attempted": False,
        "branch_diverged": False,
        "alignment_needed": False,
        "alignment_result": "not_needed",
        "alignment_changed_commit": False,
        "validation_rerun_after_alignment": False,
        "validation_state": "success",
        "validation_commit_match": True,
        "fingerprint_match": True,
        "auto_revalidated": False,
        "validation_reused": False,
        "auto_revalidation_result": "not_needed",
        "last_validated_commit": "abc123",
        "current_commit": "abc123",
        "validation_age_seconds": 5,
        "publish_reason": "validated",
        "reason": "docs update completed, but revalidation failed: pytest failed",
        "pr_already_exists": False,
        "pr_created_or_reused": False,
        "pr_merged": False,
        "local_main_synced": False,
        "noop": False,
        "commit_sha": "",
        "pr_url": None,
        "previous_publish_branch": "",
        "previous_pr_url": "",
        "previous_commit": "",
        "verification": {},
        "pr_requested": False,
        "pr_status": "not_requested",
        "pr_mergeable": "unknown",
        "pr_conflicts_detected": False,
        "pr_mergeability_source": "github",
        "pr_mergeable_final": "unknown",
        "pr_conflicts_detected_final": False,
        "pr_mergeability_repair_attempted": False,
        "pr_mergeability_repair_result": "not_needed",
        "final_workflow_result": "blocked",
    }

    lfa.print_publish_summary(result)
    out = capsys.readouterr().out

    assert "docs_checked_at_publish: true" in out
    assert "docs_check_performed: true" in out
    assert "docs_status: required_but_blocked" in out
    assert "docs_reason: docs update completed, but revalidation failed: pytest failed" in out
    assert "docs_required: true" in out
    assert "docs_updated: false" in out


def test_print_post_success_publish_summary_includes_final_pr_mergeability_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = {
        "validation_result": "success",
        "validation_state": "success",
        "publish_requested": True,
        "publish_triggered": True,
        "publish_mode": "current-repo-state",
        "publish_result": "success",
        "final_workflow_result": "blocked",
        "pr_mergeable": "unknown",
        "pr_conflicts_detected": False,
        "pr_mergeability_source": "local_fallback",
        "pr_mergeable_final": "false",
        "pr_conflicts_detected_final": True,
        "pr_mergeability_repair_attempted": True,
        "pr_mergeability_repair_result": "blocked",
        "pr_mergeability_reason": "local mergeability check found conflicts against origin/main",
    }

    lfa.print_post_success_publish_summary(summary)
    out = capsys.readouterr().out

    assert "pr_mergeable: unknown" in out
    assert "pr_mergeability_source: local_fallback" in out
    assert "pr_mergeable_final: false" in out
    assert "pr_conflicts_detected_final: true" in out
    assert "pr_mergeability_repair_attempted: true" in out
    assert "pr_mergeability_repair_result: blocked" in out
    assert "final_workflow_result: blocked" in out
