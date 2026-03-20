from __future__ import annotations

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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc123"))

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, "", "", None, [], False
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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc123"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, "", "", None, [], False
    )

    assert result["control_path"] == "noop"
    assert result["reason"] == "Publish noop: already published in previous run."
    assert result["fingerprint"]["matched_previous_success"] is True


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
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, "", "", None, [], False
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
        "prepare_publish_target",
        lambda current_repo, result: (
            False,
            "Fork target `contributor/demo` does not exist yet.",
            "Run `gh repo fork upstream/demo --clone=false` and `git remote set-url origin git@github.com:contributor/demo.git`.",
        ),
    )

    result = lfa.publish_validated_run(
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, "", "", None, [], False
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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))
    monkeypatch.setattr(lfa, "detect_existing_pr", lambda current_repo, branch: "https://github.com/octocat/demo/pull/7")

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
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", True, "", "", None, [], False
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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

    def fake_run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
        commands.append(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(lfa, "run_subprocess", fake_run_subprocess)

    result = lfa.publish_current_repo_state(repo, "", False, "", "", False)

    assert result["control_path"] == "noop"
    assert result["reason"] == "no changes to publish"
    assert result["final"]["status"] == "noop"
    assert commands == []

    lfa.print_publish_summary(result)
    assert "no changes detected → noop" in capsys.readouterr().out


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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

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

    result = lfa.publish_current_repo_state(repo, "", False, "", "", False)

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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

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

    result = lfa.publish_current_repo_state(repo, "", False, "", "", False)

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

    result = lfa.publish_current_repo_state(repo, "", False, "", "", False)

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
    monkeypatch.setattr(lfa, "parse_head_commit", lambda current_repo: "abc123")
    monkeypatch.setattr(lfa, "branch_already_up_to_date", lambda current_repo, branch, remote_ref="origin": (False, "abc122"))
    monkeypatch.setattr(lfa, "prepare_publish_target", lambda current_repo, result: (True, "", ""))

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
        repo, "pytest -q", 1, "high", None, ["local_fix_agent.py"], "", False, "", "", None, [], False
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
        publish_message: str,
        target: str,
        blocked_reason: str | None,
        baseline_paths: list[str],
        dry_run_mode: bool,
        publish_current_mode: bool = False,
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
                "publish_message": publish_message,
                "target": target,
                "blocked_reason": blocked_reason,
                "baseline_paths": baseline_paths,
                "dry_run_mode": dry_run_mode,
                "publish_current_mode": publish_current_mode,
            }
        )
        return {"recommended_command": "old", "final": {"status": "noop"}}

    monkeypatch.setattr(lfa, "publish_validated_run", fake_publish_validated_run)

    result = lfa.publish_current_repo_state(repo, "feature/publish", True, "", "", False)

    assert captured["repo"] == repo
    assert captured["test_cmd"] == "n/a (publish current repo state)"
    assert captured["attempt_number"] == 0
    assert captured["confidence_level"] == "n/a"
    assert captured["artifact_dir"] is None
    assert captured["changed_paths"] == []
    assert captured["publish_branch"] == "feature/publish"
    assert captured["publish_pr"] is True
    assert captured["publish_message"] == "chore: publish current repo state"
    assert captured["target"] == ""
    assert captured["blocked_reason"] is None
    assert captured["baseline_paths"] == []
    assert captured["dry_run_mode"] is False
    assert captured["publish_current_mode"] is True
    assert result["recommended_command"] == "AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr"
