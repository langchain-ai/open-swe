from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import local_fix_agent as lfa


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


def test_meaningful_content_fingerprint_excludes_ignored_state_files(tmp_path: Path) -> None:
    (tmp_path / ".ai_publish_state.json").write_text('{"last_success": true}\n')
    publish_changes = {
        "status_output": " M .ai_publish_state.json",
        "meaningful_paths": [],
        "ignored_changes": [".ai_publish_state.json"],
    }

    assert lfa.compute_meaningful_content_fingerprint(tmp_path, publish_changes) == ""


def test_attempt_publish_auto_revalidation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
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
    assert captured["success"] == "success"
    assert captured["test_cmd"] == "pytest -q"


def test_attempt_publish_auto_revalidation_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
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
    assert "auto-revalidation failed" in result["reason"]


def test_attempt_publish_auto_revalidation_no_auto_revalidate_preserves_block(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path("/tmp/repo")
    initial = {
        "validation_state": "blocked",
        "validation_result": "blocked",
        "validation_commit_match": False,
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
    assert commands == []

    lfa.print_publish_summary(result)
    assert "mode_summary: no meaningful changes to publish" in capsys.readouterr().out


def test_publish_current_unstaged_change_stages_with_git_add_a(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "status_output": " M local_fix_agent.py",
            "clean": False,
            "has_unstaged": True,
            "has_staged": False,
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

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A"]:
            return 0, ""
        if command == ["git", "diff", "--cached", "--quiet"]:
            return 1, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["summary_status"] == "staged current repo state"
    assert commands[:4] == [
        ["git", "add", "-A"],
        ["git", "diff", "--cached", "--quiet"],
        ["git", "commit", "-m", "chore: publish current repo state"],
        ["git", "push", "-u", "origin", "feature"],
    ]


def test_publish_current_untracked_files_stage_and_continue(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(lfa, "meaningful_changed_paths", lambda current_repo: ["new_file.txt"])
    monkeypatch.setattr(lfa, "filtered_git_status_output", lambda current_repo, ignore_all_ignored_dirs=True: "?? new_file.txt")
    monkeypatch.setattr(
        lfa,
        "classify_publishable_changes",
        lambda current_repo: {
            "status_output": "?? new_file.txt",
            "meaningful_changes_detected": True,
            "meaningful_paths": ["new_file.txt"],
            "ignored_changes": [],
        },
    )
    monkeypatch.setattr(
        lfa,
        "classify_git_working_tree",
        lambda current_repo: {
            "status_output": "?? new_file.txt",
            "clean": False,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": True,
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

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A"]:
            return 0, ""
        if command == ["git", "diff", "--cached", "--quiet"]:
            return 1, ""
        if command[:2] == ["git", "commit"]:
            return 0, ""
        if command[:3] == ["git", "push", "-u"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["published"] is True
    assert result["working_tree"]["has_untracked"] is True
    assert commands[:4] == [
        ["git", "add", "-A"],
        ["git", "diff", "--cached", "--quiet"],
        ["git", "commit", "-m", "chore: publish current repo state"],
        ["git", "push", "-u", "origin", "feature"],
    ]


def test_publish_current_does_not_reference_specific_files(monkeypatch: pytest.MonkeyPatch) -> None:
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

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        if command == ["git", "add", "-A"]:
            return 0, ""
        if command == ["git", "diff", "--cached", "--quiet"]:
            return 0, ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, False, False, "", "", False)

    assert result["control_path"] == "noop"
    assert commands == [["git", "add", "-A"], ["git", "diff", "--cached", "--quiet"]]
    assert all("README.md" not in cmd for cmd in commands)


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

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
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
    assert commands[0] == ["git", "add", "-A", "--", "local_fix_agent.py"]


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
    assert result["recommended_command"] == "AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr"


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
    ) -> dict:
        captured["repo"] = current_repo
        captured["test_cmd"] = test_cmd
        captured["validation_state"] = validation_state
        captured["force_publish"] = force_publish
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


def test_resolve_publish_requested_defaults_to_true() -> None:
    args = argparse.Namespace(publish_only=False, no_publish_on_success=False, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is True


def test_resolve_publish_requested_honors_no_publish_on_success() -> None:
    args = argparse.Namespace(publish_only=False, no_publish_on_success=True, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is False


def test_resolve_publish_requested_preserves_publish_only_mode() -> None:
    args = argparse.Namespace(publish_only=True, no_publish_on_success=True, publish=False, publish_on_success=False)
    assert lfa.resolve_publish_requested(args) is True


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
        last_validated_commit: str = "",
        current_commit: str = "",
        validation_age_seconds: int = -1,
        auto_revalidated: bool = False,
        validation_reused: bool = False,
    ) -> dict:
        captured["repo"] = repo
        captured["publish_branch"] = publish_branch
        captured["validation_state"] = validation_state
        captured["validation_detail"] = validation_detail
        captured["force_publish"] = force_publish
        captured["validation_commit_match"] = validation_commit_match
        captured["auto_revalidated"] = auto_revalidated
        captured["validation_reused"] = validation_reused
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


def test_publish_current_repo_state_auto_creates_branch_from_main_in_non_interactive_mode(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert result["ignored_changes"] == [".ai_publish_state.json", ".fix_agent_docs_state.json"]
    assert result["meaningful_paths"] == []
    assert commands == [["git", "rev-parse", "HEAD"]]


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
    assert result["meaningful_changes_detected"] is False
    assert result["meaningful_paths"] == []
    assert result["ignored_changes"] == [".ai_publish_state.json"]
    assert commands == [
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--short", "--untracked-files=all"],
    ]


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
    assert result["docs_required"] is True
    assert result["docs_updated"] is True
    assert result["docs_refresh_mode"] == "patch"
    assert result["docs_targets"] == ["README.md"]
    assert any(cmd[:3] == ["git", "add", "-A"] for cmd in commands)


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
    assert "docs_required: true" in out
    assert "docs_updated: true" in out
    assert "docs_refresh_mode: patch" in out
    assert "docs_targets: ['README.md', 'docs/RUNBOOK.md']" in out
