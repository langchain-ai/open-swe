from __future__ import annotations

from pathlib import Path

import pytest

import local_fix_agent as lfa


class InputFeeder:
    def __init__(self, values: list[str]) -> None:
        self._values = iter(values)

    def __call__(self, prompt: str = "") -> str:
        return next(self._values)


class DummyStdin:
    def isatty(self) -> bool:
        return True


def test_interactive_prompt_yes_no_accepts_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", InputFeeder([""]))

    assert lfa.interactive_prompt_yes_no("Continue?", default=True) is True


def test_interactive_prompt_text_supports_help_then_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("builtins.input", InputFeeder(["?", ""]))

    value = lfa.interactive_prompt_text("Repo path:", "/tmp/repo")

    output = capsys.readouterr().out
    assert value == "/tmp/repo"
    assert "help: enter a value or press Enter to accept the default" in output


def test_interactive_workflow_registry_contains_required_menu_items() -> None:
    registry = lfa.interactive_workflow_registry()

    assert list(registry) == [
        "fix_validate",
        "new_script",
        "publish_current",
        "publish_validated",
        "import_training",
        "inspect_patterns",
        "manage_patterns",
        "probe",
        "sync_conflicts",
        "settings",
        "exit",
    ]
    assert registry["probe"]["label"] == "Probe API / M3U8 endpoint"
    assert registry["fix_validate"]["label"] == "Fix or validate a script"


def test_interactive_fix_validate_action_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "tool.py"
    script.write_text("print('ok')\n")
    monkeypatch.setattr(lfa, "latest_repo_validation_command", lambda repo_path: "")
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                str(script),
                "1",
                "1",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_fix_validate_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["workflow"] == "Fix or validate a script"
    assert action["scaffolded"] is False
    assert action["inputs"]["mode"] == "fix_and_validate"
    assert action["inputs"]["validation_choice"] == "auto-detect"
    assert action["inputs"]["pattern_source"] == "auto"
    assert action["inputs"]["probe_planned"] == "no"
    assert action["commands"][0]["label"] == "fix/validate"
    assert action["commands"][0]["compact_preview"] == "Fix and validate the script"
    assert action["commands"][0]["args"] == [
        "--repo",
        str(repo),
        "--script",
        str(script),
    ]


def test_interactive_fix_validate_action_custom_validation_and_advanced_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "tool.py"
    script.write_text("print('ok')\n")
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                str(script),
                "1",
                "3",
                "pytest tests/test_tool.py -q",
                "4",
                "/tmp/pattern-repo",
                "y",
                "3",
                "y",
                "n",
                "y",
                "n",
                "n",
            ]
        ),
    )

    action = lfa.interactive_fix_validate_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["validation_choice"] == "pytest tests/test_tool.py -q"
    assert action["inputs"]["pattern_source"] == "/tmp/pattern-repo"
    assert action["inputs"]["advanced_options"] is True
    assert action["commands"][0]["compact_preview"] == "Fix and validate the script"
    assert action["commands"][0]["args"] == [
        "--repo",
        str(repo),
        "--script",
        str(script),
        "--pattern-repo",
        "/tmp/pattern-repo",
        "--test-cmd",
        "pytest tests/test_tool.py -q",
        "--mode",
        "deep",
        "--no-auto-stage",
        "--no-auto-conflict-resolution-after-sync",
    ]


def test_interactive_fix_validate_quick_mode_uses_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "tool.py"
    script.write_text("print('ok')\n")
    monkeypatch.setattr("builtins.input", InputFeeder([str(repo), str(script)]))

    action = lfa.interactive_fix_validate_action(
        {"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human", "interaction_mode": "quick"}
    )

    assert action["inputs"]["workflow_mode"] == "quick"
    assert action["inputs"]["validation_choice"] == "auto-detect"
    assert action["inputs"]["pattern_source"] == "auto"
    assert action["commands"][0]["args"] == ["--repo", str(repo), "--script", str(script)]


def test_interactive_publish_validated_action_builds_canonical_command_pair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "y",
            ]
        ),
    )

    action = lfa.interactive_publish_validated_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["workflow"] == "Publish last validated run"
    assert action["commands"][0]["args"] == ["--repo", str(repo), "--ensure-validation-record"]
    assert action["commands"][1]["args"] == [
        "--repo",
        str(repo),
        "--publish-only",
        "--publish-pr",
    ]


def make_publish_preflight(
    *,
    meaningful_paths: list[str] | None = None,
    staged_paths: list[str] | None = None,
    unstaged_paths: list[str] | None = None,
    validation_result: str = "success",
    validation_record_exists: bool = True,
    validation_commit_match: bool = True,
    revalidation_planned: bool = False,
    would_block: bool = False,
    would_block_reason: str = "",
    staging_plan: str = "all publishable changes already staged",
    remaining_unstaged: list[dict] | None = None,
    safe_staged_paths: list[str] | None = None,
    ignored_nonblocking_paths: list[str] | None = None,
    safe_stage_candidate_paths: list[str] | None = None,
    true_blockers: list[dict] | None = None,
    blocker_count: int = 0,
    publishable_ready: bool = True,
    staging_decision_reason: str = "all publishable changes already staged",
    blocked_file_analysis: list[dict] | None = None,
    blocked_analysis_summary: dict | None = None,
    auto_remediate_blockers: bool = True,
    auto_resolvable_blockers: list[str] | None = None,
    unresolved_blockers_after_remediation: list[str] | None = None,
) -> dict:
    return {
        "changes": {"meaningful_paths": meaningful_paths or ["docs/README.md", "local_fix_agent.py"]},
        "working_tree": {
            "staged_paths": staged_paths or [],
            "unstaged_paths": unstaged_paths or [],
            "untracked_paths": [],
        },
        "validation_state": {"validation_result": validation_result},
        "validation_record_exists": validation_record_exists,
        "validation_commit_match": validation_commit_match,
        "revalidation_planned": revalidation_planned,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "staging_plan": staging_plan,
        "remaining_unstaged": remaining_unstaged or [],
        "safe_staged_paths": safe_staged_paths or [],
        "ignored_nonblocking_paths": ignored_nonblocking_paths or [],
        "safe_stage_candidate_paths": safe_stage_candidate_paths or [],
        "true_blockers": true_blockers or [],
        "blocker_count": blocker_count,
        "publishable_ready": publishable_ready,
        "auto_remediate_blockers": auto_remediate_blockers,
        "auto_resolvable_blockers": auto_resolvable_blockers or [],
        "unresolved_blockers_after_remediation": unresolved_blockers_after_remediation or [],
        "blocked_file_analysis": blocked_file_analysis or [],
        "blocked_analysis_summary": blocked_analysis_summary or {},
        "staging_decision_reason": staging_decision_reason,
        "preflight": {"branch": "agent-run-123"},
    }


def test_interactive_publish_current_action_normal_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa, "interactive_publish_preflight_state", lambda *args, **kwargs: make_publish_preflight(staged_paths=["docs/README.md"]))
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "1",
                "1",
                "",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_publish_current_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["workflow"] == "Publish current repo state"
    assert action["inputs"]["publish_mode"] == "normal"
    assert action["inputs"]["validation_status"] == "success"
    assert action["inputs"]["revalidation_will_run"] is False
    assert action["commands"][0]["preview_command"] == f"./scripts/fixpublish.sh --repo {repo}"
    assert action["commands"][0]["run_command"][-2:] == ["--repo", str(repo)]


def test_interactive_publish_current_action_auto_stage_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_publish_preflight_state",
        lambda *args, **kwargs: make_publish_preflight(
            staged_paths=[],
            unstaged_paths=["c7c5dc0.txt"],
            would_block=True,
            would_block_reason="one or more unsafe or ambiguous files would still require manual review",
            staging_plan="auto-stage 2 safe path(s); manual review required for 1 unsafe path(s)",
            remaining_unstaged=[{"path": "c7c5dc0.txt", "file_type": "artifact", "reason": "generated/artifact file"}],
            true_blockers=[{"path": "c7c5dc0.txt", "file_type": "artifact", "reason": "generated/artifact file"}],
            blocker_count=1,
            publishable_ready=False,
            auto_resolvable_blockers=[],
            unresolved_blockers_after_remediation=["c7c5dc0.txt"],
            staging_decision_reason="one or more files were classified as unknown/artifact and require manual review",
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "1",
                "1",
                "",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_publish_current_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["publish_would_block"] is True
    assert "manual review" in action["inputs"]["publish_block_reason"]
    assert "auto-stage 2 safe path(s)" in action["inputs"]["staging_plan"]
    assert "true_blockers:" in action["notes"][3]


def test_interactive_publish_current_action_validation_mismatch_plans_revalidation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_publish_preflight_state",
        lambda *args, **kwargs: make_publish_preflight(
            validation_result="blocked",
            validation_commit_match=False,
            revalidation_planned=True,
            staging_plan="all publishable changes already staged",
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "1",
                "1",
                "",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_publish_current_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["validation_commit_match"] is False
    assert action["inputs"]["revalidation_will_run"] is True
    assert action["inputs"]["validation_status"] == "blocked"


def test_interactive_publish_current_action_force_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa, "interactive_publish_preflight_state", lambda *args, **kwargs: make_publish_preflight(validation_result="blocked"))
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "2",
                "2",
                "n",
                "2",
                "y",
                "y",
                "y",
            ]
        ),
    )

    action = lfa.interactive_publish_current_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["publish_mode"] == "force"
    assert "--force-publish" in action["commands"][0]["preview_command"]
    assert "--no-auto-stage" in action["commands"][0]["preview_command"]
    assert "--no-auto-remediate-blockers" in action["commands"][0]["preview_command"]
    assert "--no-auto-revalidate" in action["commands"][0]["preview_command"]
    assert "--explain-staging" in action["commands"][0]["preview_command"]
    assert "--no-auto-conflict-resolution-after-sync" in action["commands"][0]["preview_command"]


def test_interactive_menu_prints_all_required_items_and_probe_is_not_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr("builtins.input", InputFeeder(["11"]))

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "=== LOCAL FIX AGENT APP ===" in output
    assert "Fix or validate a script" in output
    assert "Create a new script" in output
    assert "Publish current repo state" in output
    assert "Publish last validated run" in output
    assert "Import a script into training" in output
    assert "Inspect learned patterns" in output
    assert "Manage patterns" in output
    assert "Probe API / M3U8 endpoint" in output
    assert "Sync / repair repo conflicts" in output
    assert "Settings / advanced options" in output
    assert "[1] Fix or validate a script - Fix one script and run validation, or validate a script without editing it. [Enter]" in output


def test_interactive_menu_routes_to_probe_handler_without_making_it_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    called: list[str] = []

    def fake_probe_handler(session: dict) -> dict:
        called.append("probe")
        return {
            "workflow": "Probe API / M3U8 endpoint",
            "description": "probe route",
            "scaffolded": True,
            "inputs": {"repo": session["repo"]},
            "notes": ["probe stub"],
            "commands": [{"label": "probe endpoint", "args": ["--repo", session["repo"], "--probe-url", "https://example.com/feed.m3u8"]}],
        }

    monkeypatch.setattr(lfa, "interactive_probe_action", fake_probe_handler)
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr("builtins.input", InputFeeder(["8", "2", "11"]))

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert called == ["probe"]
    assert "when_to_use: probe route" in output
    assert "probe endpoint: python local_fix_agent.py --repo" in output


def test_interactive_back_and_cancel_work(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    called: list[str] = []

    def fake_fix_handler(session: dict) -> dict:
        called.append("fix")
        return {
            "workflow": "Fix or validate a script",
            "description": "fix route",
            "scaffolded": True,
            "inputs": {"repo": session["repo"]},
            "notes": ["stub"],
            "commands": [{"label": "fix/validate", "args": ["--repo", session["repo"], "--script", "demo.py"]}],
        }

    monkeypatch.setattr(lfa, "interactive_fix_validate_action", fake_fix_handler)
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr("builtins.input", InputFeeder(["1", "2", "11"]))

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert called == ["fix"]
    assert "Next step:" in output
    assert "Exiting interactive mode." in output


def test_interactive_fix_validate_offers_probe_for_network_dependent_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "network_tool.py"
    script.write_text(
        "import requests\n"
        "PLAYLIST = 'https://media.example.com/master.m3u8'\n"
        "HEADERS = {'Authorization': 'Bearer token'}\n"
    )
    monkeypatch.setattr(lfa, "latest_repo_validation_command", lambda repo_path: "")
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                str(script),
                "2",
                "1",
                "1",
                "y",
                "2",
                "",
                "y",
                "y",
                "n",
            ]
        ),
    )

    action = lfa.interactive_fix_validate_action({"repo": str(repo), "http_proxy": "http://proxy.internal:8080", "https_proxy": "", "output": "human"})

    assert action["inputs"]["mode"] == "validate_only"
    assert action["inputs"]["probe_planned"].startswith("yes (m3u8")
    assert len(action["commands"]) == 2
    assert action["commands"][0]["label"] == "probe endpoint"
    assert "--probe-url" in action["commands"][0]["args"]
    assert "--probe-header" in action["commands"][0]["args"]
    assert action["commands"][1]["args"] == [
        "--repo",
        str(repo),
        "--http-proxy",
        "http://proxy.internal:8080",
        "--script",
        str(script),
        "--script-validate-only",
    ]


def test_interactive_fix_validate_does_not_offer_probe_for_local_only_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "local_tool.py"
    script.write_text("import argparse\nprint('ok')\n")
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                str(script),
                "1",
                "1",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_fix_validate_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert len(action["commands"]) == 1
    assert action["inputs"]["probe_planned"] == "no"
    assert action["notes"] == []


def test_interactive_new_script_action_builds_local_generation_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "1",
                str(repo),
                "create a local cli tool with logging",
                "3",
                "scripts/generated_tool.py",
                "1",
                "1",
                "n",
            ]
        ),
    )

    action = lfa.interactive_new_script_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human", "interaction_mode": "guided"})

    assert action["inputs"]["workflow_mode"] == "guided"
    assert action["inputs"]["probe_planned"] == "no"
    assert action["inputs"]["script_domain"] == "cli"
    assert action["inputs"]["validation_plan"] == "auto-detect"
    assert action["commands"][0]["args"] == [
        "--repo",
        str(repo),
        "--new-script",
        "scripts/generated_tool.py",
        "--new-script-purpose",
        "create a local cli tool with logging",
    ]


def test_interactive_new_script_action_offers_probe_for_network_dependent_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "",
                str(repo),
                "build an API client for https://example.com/v1/models",
                "tools/api_client.py",
                "y",
                "1",
                "",
                "y",
                "n",
            ]
        ),
    )

    action = lfa.interactive_new_script_action(
        {
            "repo": str(repo),
            "http_proxy": "http://proxy.internal:8080",
            "https_proxy": "",
            "output": "human",
            "interaction_mode": "quick",
        }
    )

    assert action["inputs"]["workflow_mode"] == "quick"
    assert action["inputs"]["probe_planned"].startswith("yes (api -> https://example.com/v1/models)")
    assert "--probe-url" in action["commands"][0]["args"]
    assert "--probe-type" in action["commands"][0]["args"]
    assert "--http-proxy" in action["commands"][0]["args"]


def test_interactive_new_script_action_supports_custom_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "1",
                str(repo),
                "build a parser for report files",
                "7",
                "tools/report_parser.py",
                "1",
                "4",
                "pytest tests/test_report_parser.py -q",
                "n",
            ]
        ),
    )

    action = lfa.interactive_new_script_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human", "interaction_mode": "guided"})

    assert action["inputs"]["validation_plan"] == "pytest tests/test_report_parser.py -q"
    assert "--new-script-validation-mode" in action["commands"][0]["args"]
    assert "custom" in action["commands"][0]["args"]
    assert "--test-cmd" in action["commands"][0]["args"]


def test_interactive_command_preview_is_shown_before_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: list[list[str]] = []

    def fake_run_backend(args: list[str]) -> dict:
        captured.append(args)
        return {"returncode": 0, "output": "validation_result: success\nvalidation_command: python demo.py --help\nFINAL: validation succeeded, publish succeeded\n"}

    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(lfa, "run_interactive_backend_command", fake_run_backend)
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "1",
                str(repo),
                str(repo / "tool.py"),
                "1",
                "1",
                "1",
                "n",
                "1",
                "n",
            ]
        ),
    )
    (repo / "tool.py").write_text("print('ok')\n")

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "=== CONFIRMATION SUMMARY ===" in output
    assert "equivalent_command:" in output
    assert "command_preview:" in output
    assert "fix/validate: python local_fix_agent.py --repo" in output
    assert "=== WORKFLOW RESULT ===" in output
    assert "status: success" in output
    assert "validation_result: success" in output
    assert "what_happened: The agent fixed issues, reran validation, and the script now passes." in output
    assert len(captured) == 1


def test_interactive_new_script_result_display(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(
        lfa,
        "run_interactive_backend_command",
        lambda args: {
            "returncode": 0,
            "output": (
                "script_generated: true\n"
                f"output_path: {repo / 'tools/api_client.py'}\n"
                "pattern_source_used: default\n"
                "patterns_applied: ['cli_style', 'logging_style']\n"
                "probe_used: true\n"
                "key_probe_findings: json_keys=object,data\n"
                "validation_plan: python api_client.py --help\n"
                "generation_confidence: high\n"
                "validation_result: success\n"
                "validation_success: True\n"
                "script_kind: api\n"
            ),
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "2",
                "",
                str(repo),
                "build an API client for https://example.com/v1/models",
                "tools/api_client.py",
                "y",
                "1",
                "",
                "n",
                "n",
                "1",
                "n",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app(
        {
            "repo": str(repo),
            "http_proxy": "http://proxy.internal:8080",
            "https_proxy": "",
            "output": "human",
            "interaction_mode": "quick",
        }
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "workflow: Create a new script" in output
    assert "probe_used: true" in output
    assert "generation_confidence: high" in output
    assert "validation_result: success" in output
    assert "publish_result: not_requested" in output
    assert "what_happened: Generated a network-aware script using live API probe results and validated it successfully." in output


def test_interactive_new_script_failure_runs_repair_pass(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[list[str]] = []
    outputs = iter(
        [
            {
                "returncode": 1,
                "output": (
                    "script_generated: true\n"
                    f"output_path: {repo / 'scripts/generated_tool.py'}\n"
                    "validation_result: blocked\n"
                    "validation_success: False\n"
                    "blocked_reason: help output failed\n"
                ),
            },
            {
                "returncode": 0,
                "output": (
                    "validation_result: success\n"
                    "validation_command: python scripts/generated_tool.py --help\n"
                ),
            },
        ]
    )

    def fake_run_backend(args: list[str]) -> dict:
        calls.append(args)
        return next(outputs)

    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(lfa, "run_interactive_backend_command", fake_run_backend)
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "2",
                "",
                str(repo),
                "create a local cli tool with logging",
                "scripts/generated_tool.py",
                "n",
                "1",
                "n",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human", "interaction_mode": "quick"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert len(calls) == 2
    assert "--new-script" in calls[0]
    assert "--script" in calls[1]
    assert "repair_attempted: true" in output
    assert "repair_result: success" in output
    assert "what_happened: Generated the script, repaired validation issues, and the script now passes." in output


def test_interactive_new_script_success_can_publish(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    backend_calls: list[list[str]] = []

    def fake_run_backend(args: list[str]) -> dict:
        backend_calls.append(args)
        return {
            "returncode": 0,
            "output": (
                "script_generated: true\n"
                f"output_path: {repo / 'scripts/generated_tool.py'}\n"
                "pattern_source_used: default\n"
                "patterns_applied: ['cli_style']\n"
                "probe_used: false\n"
                "validation_plan: python generated_tool.py --help\n"
                "generation_confidence: medium\n"
                "validation_result: success\n"
                "validation_success: True\n"
                "script_kind: local\n"
            ),
        }

    class FakeStdout:
        def __iter__(self):
            yield "publish_result: success\n"
            yield "pr_url: https://github.com/example/repo/pull/11\n"

    class FakePopen:
        def __init__(self, command, cwd=None, stdout=None, stderr=None, text=None):
            self.command = command
            self.cwd = cwd
            self.stdout = FakeStdout()

        def wait(self):
            return 0

    monkeypatch.setattr(lfa, "run_interactive_backend_command", fake_run_backend)
    monkeypatch.setattr(lfa.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "2",
                "",
                str(repo),
                "create a local cli tool with logging",
                "scripts/generated_tool.py",
                "n",
                "1",
                "y",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human", "interaction_mode": "quick"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert len(backend_calls) == 1
    assert "publish_result: success" in output
    assert "pr_url: https://github.com/example/repo/pull/11" in output
    assert "what_happened: Generated a new script, validated it, and published it safely." in output


def test_interactive_validate_only_result_display_blocked(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "tool.py"
    script.write_text("print('ok')\n")
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "1",
                str(repo),
                str(script),
                "2",
                "3",
                "pytest tests/test_tool.py -q",
                "1",
                "n",
                "1",
                "n",
            ]
        ),
    )
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(
        lfa,
        "run_interactive_backend_command",
        lambda args: {
            "returncode": 1,
            "output": "validation_result: blocked\nvalidation_command: pytest tests/test_tool.py -q\nblocked_reason: assertion failed\n",
        },
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: blocked" in output
    assert "validation_result: blocked" in output
    assert "blocked_reason: assertion failed" in output
    assert "next_step: Check the validation command output above" in output
    assert "what_happened: The run blocked because the validation command failed." in output


def test_interactive_publish_current_confirmation_preview_and_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(lfa, "interactive_publish_preflight_state", lambda *args, **kwargs: make_publish_preflight(staged_paths=["docs/README.md"]))

    class FakeStdout:
        def __iter__(self):
            yield "validation_result: success\n"
            yield "publish_triggered: true\n"
            yield "publish_result: success\n"
            yield "branch: fix-agent/20260321-120000\n"
            yield "pr_url: https://github.com/example/repo/pull/5\n"
            yield "pr_mergeable_final: true\n"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStdout()

        def wait(self):
            return 0

    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(lfa.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "3",
                str(repo),
                "1",
                "1",
                "",
                "1",
                "n",
                "1",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "equivalent_command:" in output
    assert f"./scripts/fixpublish.sh --repo {repo}" in output
    assert "validation_result: success" in output
    assert "publish_result: success" in output
    assert "branch_used: fix-agent/20260321-120000" in output
    assert "what_happened: Changes were validated and published successfully." in output


def test_interactive_publish_current_result_blocked(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_publish_preflight_state",
        lambda *args, **kwargs: make_publish_preflight(
            would_block=True,
            would_block_reason="publishable or ambiguous files remain unstaged and automatic staging is disabled",
        ),
    )

    class FakeStdout:
        def __iter__(self):
            yield "validation_result: success\n"
            yield "publish_triggered: true\n"
            yield "publish_result: blocked\n"
            yield "branch: agent-run-123\n"
            yield "pr_mergeable_final: unknown\n"
            yield "staging_reason: publishable or ambiguous files remain unstaged and automatic staging is disabled\n"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStdout()

        def wait(self):
            return 1

    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(lfa.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "3",
                str(repo),
                "1",
                "2",
                "n",
                "1",
                "n",
                "1",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: blocked" in output
    assert "publish_result: blocked" in output
    assert "blocked_reason: publishable or ambiguous files remain unstaged" in output
    assert "next_step: Review the blocker above, fix it, then rerun publish." in output
    assert "what_happened: Publish blocked due to unstaged files." in output


def test_interactive_publish_blocked_followup_shows_expert_analysis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    preflight = make_publish_preflight(
        would_block=True,
        would_block_reason="one or more unsafe or ambiguous files would still require manual review",
        remaining_unstaged=[{"path": "c76abc.txt", "file_type": "artifact", "classification_source": "extension", "publishable": False, "reason": "generated/artifact file"}],
        blocked_file_analysis=[
            {
                "path": "c76abc.txt",
                "file_type": "artifact",
                "classification_source": "extension",
                "publishable": False,
                "confidence": "high",
                "blocking_reason": "file looks like generated output or a temporary artifact and does not match publishable patterns",
                "recommended_action": "remove generated artifact",
                "recommended_commands": ["rm c76abc.txt", "echo '*.txt' >> .gitignore"],
            }
        ],
        blocked_analysis_summary={
            "blocked_count": 1,
            "primary_next_step": "remove or ignore the artifact-style file, then rerun publish",
            "fallback_next_step": "inspect the file manually if you intended to keep it in the repo",
            "rerun_command": "./scripts/fixpublish.sh",
        },
    )
    monkeypatch.setattr(lfa, "interactive_publish_preflight_state", lambda *args, **kwargs: preflight)

    class FakeStdout:
        def __iter__(self):
            yield "validation_result: success\n"
            yield "publish_triggered: true\n"
            yield "publish_result: blocked\n"
            yield "staging_reason: one or more unsafe or ambiguous files would still require manual review\n"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStdout()

        def wait(self):
            return 1

    monkeypatch.setattr(lfa.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(["3", str(repo), "1", "1", "", "1", "n", "1", "1", "4", "11"]),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "=== STAGING BLOCK ANALYSIS ===" in output
    assert "recommended_action: remove generated artifact" in output
    assert "rerun: ./scripts/fixpublish.sh" in output


def test_interactive_prepare_training_import_local_sanitized_and_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "local",
            "source_origin": source,
            "acquisition_method": "direct",
            "proxy_used": False,
            "content": "API_TOKEN='secret'\nprint('ok')\n",
        },
    )
    monkeypatch.setattr(
        lfa,
        "sanitize_pattern_script_content",
        lambda content: ("API_TOKEN='<redacted>'\nprint('ok')\n", True),
    )
    monkeypatch.setattr(lfa, "run_candidate_validation", lambda pattern_repo, candidate_path: {"passed": True, "limited_validation": False, "validation_command": "python tool.py --help"})
    monkeypatch.setattr(
        lfa,
        "extract_script_patterns_with_metadata",
        lambda repo, script_path, source_record: [
            {"pattern_type": "cli_style", "family": "cli_style", "confidence": 0.91, "applicability_context": ["cli"], "summary": "CLI pattern"}
        ],
    )

    result = lfa.interactive_prepare_training_import(
        source_value=str(tmp_path / "tool.py"),
        source_type="local",
        target_repo=tmp_path / "pattern-repo",
        requested_trust="trusted",
        sanitize_before_import=True,
        validate_before_promote=True,
        allow_auto_fix=True,
        pattern_tags=["cli"],
        pattern_type_hint="",
    )

    assert result["ok"] is True
    assert result["source_type"] == "local"
    assert result["acquisition_method"] == "direct"
    assert result["sanitized_changed"] is True
    assert result["validation_result"] == "success"
    assert result["repair_attempted"] is False
    assert result["pattern_type"] == "cli_style"
    assert result["confidence_level"] == "high"
    assert result["safe_for_trusted"] is True


def test_interactive_prepare_training_import_http_uses_proxy_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "http",
            "source_origin": source,
            "acquisition_method": "curl",
            "proxy_used": True,
            "content": "print('ok')\n",
        },
    )
    monkeypatch.setattr(lfa, "run_candidate_validation", lambda pattern_repo, candidate_path: {"passed": True, "limited_validation": False, "validation_command": "python -m py_compile script.py"})
    monkeypatch.setattr(
        lfa,
        "extract_script_patterns_with_metadata",
        lambda repo, script_path, source_record: [{"pattern_type": "function_organization", "family": "function_organization", "confidence": 0.74, "applicability_context": ["local_utils"]}],
    )

    result = lfa.interactive_prepare_training_import(
        source_value="https://example.com/tool.py",
        source_type="http",
        target_repo=tmp_path / "pattern-repo",
        requested_trust="experimental",
        sanitize_before_import=True,
        validate_before_promote=True,
        allow_auto_fix=True,
        pattern_tags=[],
        pattern_type_hint="",
    )

    assert result["source_type"] == "http"
    assert result["acquisition_method"] == "curl"
    assert result["proxy_used"] is True
    assert result["confidence_level"] == "medium"


def test_interactive_prepare_training_import_skips_validation_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "local",
            "source_origin": source,
            "acquisition_method": "direct",
            "proxy_used": False,
            "content": "print('ok')\n",
        },
    )
    monkeypatch.setattr(
        lfa,
        "extract_script_patterns_with_metadata",
        lambda repo, script_path, source_record: [{"pattern_type": "cli_style", "family": "cli_style", "confidence": 0.88, "applicability_context": ["cli"]}],
    )

    result = lfa.interactive_prepare_training_import(
        source_value=str(tmp_path / "tool.py"),
        source_type="local",
        target_repo=tmp_path / "pattern-repo",
        requested_trust="experimental",
        sanitize_before_import=True,
        validate_before_promote=False,
        allow_auto_fix=True,
        pattern_tags=[],
        pattern_type_hint="",
    )

    assert result["validation_result"] == "skipped"
    assert "validation was skipped before promotion" in result["warnings"]


def test_interactive_prepare_training_import_ssh_validation_fail_then_repair_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "ssh",
            "source_origin": source,
            "acquisition_method": "scp",
            "proxy_used": False,
            "content": "print('ok')\n",
        },
    )
    validations = iter(
        [
            {"passed": False, "limited_validation": False, "validation_command": "python broken.py"},
            {"passed": True, "limited_validation": False, "validation_command": "python fixed.py"},
        ]
    )
    monkeypatch.setattr(lfa, "run_candidate_validation", lambda pattern_repo, candidate_path: next(validations))
    monkeypatch.setattr(lfa, "repair_training_candidate", lambda pattern_repo, candidate_path: {"ok": True, "output": "repaired", "command": ["python", "local_fix_agent.py"]})
    monkeypatch.setattr(
        lfa,
        "extract_script_patterns_with_metadata",
        lambda repo, script_path, source_record: [{"pattern_type": "request_session", "family": "request_session", "confidence": 0.88, "applicability_context": ["network"]}],
    )

    result = lfa.interactive_prepare_training_import(
        source_value="ssh://user@example.com/path/tool.py",
        source_type="ssh",
        target_repo=tmp_path / "pattern-repo",
        requested_trust="trusted",
        sanitize_before_import=True,
        validate_before_promote=True,
        allow_auto_fix=True,
        pattern_tags=[],
        pattern_type_hint="",
    )

    assert result["source_type"] == "ssh"
    assert result["repair_attempted"] is True
    assert result["repair_result"] == "success"
    assert result["validation_result"] == "success"
    assert result["safe_for_trusted"] is True


def test_interactive_prepare_training_import_validation_fail_recommends_experimental(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lfa,
        "fetch_pattern_source",
        lambda source, cwd: {
            "ok": True,
            "source_type": "local",
            "source_origin": source,
            "acquisition_method": "direct",
            "proxy_used": False,
            "content": "print('broken')\n",
        },
    )
    monkeypatch.setattr(lfa, "run_candidate_validation", lambda pattern_repo, candidate_path: {"passed": False, "limited_validation": False, "validation_command": "python broken.py"})
    monkeypatch.setattr(lfa, "repair_training_candidate", lambda pattern_repo, candidate_path: {"ok": False, "output": "still broken", "command": ["python", "local_fix_agent.py"]})
    monkeypatch.setattr(
        lfa,
        "extract_script_patterns_with_metadata",
        lambda repo, script_path, source_record: [{"pattern_type": "unknown", "family": "unknown", "confidence": 0.42, "applicability_context": []}],
    )

    result = lfa.interactive_prepare_training_import(
        source_value=str(tmp_path / "broken.py"),
        source_type="local",
        target_repo=tmp_path / "pattern-repo",
        requested_trust="trusted",
        sanitize_before_import=True,
        validate_before_promote=True,
        allow_auto_fix=True,
        pattern_tags=[],
        pattern_type_hint="",
    )

    assert result["validation_result"] == "blocked"
    assert result["repair_attempted"] is True
    assert result["repair_result"] == "failed"
    assert result["confidence_level"] == "low"
    assert result["recommended_trust"] == "experimental"
    assert result["safe_for_trusted"] is False


def test_interactive_import_training_action_builds_clean_trusted_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_prepare_training_import",
        lambda **kwargs: {
            "ok": True,
            "source_type": "local",
            "source_origin": str(tmp_path / "tool.py"),
            "acquisition_method": "direct",
            "proxy_used": False,
            "sanitization_applied": True,
            "sanitized_changed": True,
            "validation_result": "success",
            "validation_command": "python tool.py --help",
            "repair_result": "not_needed",
            "pattern_type": "cli_style",
            "applicability_context": ["cli"],
            "confidence_level": "high",
            "warnings": [],
            "safe_for_trusted": True,
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "1",
                str(tmp_path / "tool.py"),
                "1",
                "cli,utility",
                "cli_style",
                "1",
                "y",
                "y",
                "y",
                "n",
            ]
        ),
    )

    action = lfa.interactive_import_training_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["trust_level"] == "trusted"
    assert action["inputs"]["validation_result"] == "success"
    assert action["inputs"]["validation_command"] == "python tool.py --help"
    assert action["inputs"]["pattern_type"] == "cli_style"
    assert action["inputs"]["applicability_context"] == ["cli"]
    assert action["commands"][0]["compact_preview"] == "Import into training as trusted"
    assert action["commands"][0]["args"] == [
        "--repo",
        str(repo),
        "--import-pattern-files",
        str(tmp_path / "tool.py"),
        "--pattern-trust",
        "trusted",
        "--pattern-repo",
        "default",
        "--pattern-tags",
        "cli,utility",
        "--pattern-note",
        "pattern_type_hint=cli_style",
    ]


def test_interactive_import_training_action_falls_back_to_experimental(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_prepare_training_import",
        lambda **kwargs: {
            "ok": True,
            "source_type": "local",
            "source_origin": str(tmp_path / "broken.py"),
            "acquisition_method": "direct",
            "proxy_used": False,
            "sanitization_applied": True,
            "sanitized_changed": False,
            "validation_result": "blocked",
            "validation_command": "python broken.py",
            "repair_result": "failed",
            "pattern_type": "request_session",
            "applicability_context": ["network"],
            "confidence_level": "low",
            "warnings": ["low confidence suggests experimental trust"],
            "safe_for_trusted": False,
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "2",
                "ssh://user@example.com/path/broken.py",
                "2",
                "/tmp/pattern-repo",
                "",
                "",
                "1",
                "y",
                "y",
                "y",
                "n",
                "1",
            ]
        ),
    )

    action = lfa.interactive_import_training_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["trust_level"] == "experimental"
    assert action["inputs"]["confidence"] == "low"
    assert "--pattern-trust" in action["commands"][0]["args"]
    assert "experimental" in action["commands"][0]["args"]


def test_interactive_import_training_action_create_new_repo_and_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created_repo = tmp_path / "new-pattern-repo"
    monkeypatch.setattr(
        lfa,
        "interactive_prepare_training_import",
        lambda **kwargs: {
            "ok": True,
            "source_type": "http",
            "source_origin": "https://example.com/tool.py",
            "acquisition_method": "curl",
            "proxy_used": True,
            "sanitization_applied": True,
            "sanitized_changed": False,
            "validation_result": "success",
            "validation_command": "python -m py_compile tool.py",
            "repair_result": "not_needed",
            "pattern_type": "request_session",
            "applicability_context": ["network"],
            "confidence_level": "medium",
            "warnings": [],
            "safe_for_trusted": True,
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "3",
                "https://example.com/tool.py",
                "3",
                str(created_repo),
                "net,api",
                "request_session",
                "2",
                "y",
                "y",
                "y",
                "y",
                "seed import",
            ]
        ),
    )

    action = lfa.interactive_import_training_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["inputs"]["training_repo"] == str(created_repo)
    assert action["inputs"]["target_repo"] == str(created_repo)
    assert action["inputs"]["trust_level"] == "experimental"
    assert action["commands"][0]["args"] == [
        "--repo",
        str(repo),
        "--import-pattern-files",
        "https://example.com/tool.py",
        "--pattern-trust",
        "experimental",
        "--pattern-repo",
        str(created_repo),
        "--pattern-tags",
        "net,api",
        "--pattern-note",
        "pattern_type_hint=request_session; seed import",
    ]


def test_interactive_import_training_action_back_after_acquisition_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_prepare_training_import",
        lambda **kwargs: {
            "ok": False,
            "source_type": "ssh",
            "source_origin": "ssh://user@example.com/path/tool.py",
            "acquisition_method": "scp",
            "proxy_used": False,
            "blocked_reason": "ssh fetch failed",
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                str(repo),
                "2",
                "ssh://user@example.com/path/tool.py",
                "1",
                "",
                "",
                "1",
                "y",
                "y",
                "y",
                "n",
                "2",
            ]
        ),
    )

    action = lfa.interactive_import_training_action({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    assert action["app_navigation"] == "back"
    assert action["commands"] == []
    assert "Import precheck failed" in action["notes"][0]


def test_interactive_import_training_result_display(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        lfa,
        "interactive_prepare_training_import",
        lambda **kwargs: {
            "ok": True,
            "source_type": "local",
            "source_origin": str(tmp_path / "tool.py"),
            "acquisition_method": "direct",
            "proxy_used": False,
            "sanitization_applied": True,
            "sanitized_changed": True,
            "validation_result": "success",
            "repair_result": "success",
            "pattern_type": "cli_style",
            "confidence_level": "high",
            "warnings": ["sanitization removed or redacted sensitive content"],
            "safe_for_trusted": True,
        },
    )
    monkeypatch.setattr(
        lfa,
        "run_interactive_backend_command",
        lambda args: {
            "returncode": 0,
            "output": (
                "training_repo: /tmp/pattern-repo\n"
                "imported_source: id=trusted-demo source=candidates/demo.py promoted_path=curated/trusted/demo.py trust=trusted sanitized=true validated=true repaired=true promoted_to_training=true\n"
                "learned_pattern_delta: 3\n"
            ),
        },
    )
    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(
        "builtins.input",
        InputFeeder(
            [
                "5",
                str(repo),
                "1",
                str(tmp_path / "tool.py"),
                "1",
                "",
                "",
                "1",
                "y",
                "y",
                "y",
                "n",
                "1",
                "n",
            ]
        ),
    )

    exit_code = lfa.run_interactive_app({"repo": str(repo), "http_proxy": "", "https_proxy": "", "output": "human"})

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "import_success: true" in output
    assert "target_repo_path: /tmp/pattern-repo" in output
    assert "trust_level_applied: trusted" in output
    assert "learned_pattern_count_change: 3" in output
    assert "validation_result: success" in output
    assert "repair_result: success" in output
    assert "what_happened: The script required repair before being accepted into training." in output


def test_interactive_import_training_result_display_experimental_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    action = {
        "workflow": "Import a script into training",
        "inputs": {
            "target_repo": "/tmp/pattern-repo",
            "trust_level": "experimental",
            "validation_result": "blocked",
            "repair_result": "failed",
        },
        "result_context": {
            "final_trust": "experimental",
            "warnings": ["low confidence suggests experimental trust"],
            "validation_result": "blocked",
            "repair_result": "failed",
        },
    }
    executed = [
        {
            "returncode": 0,
            "output": (
                "training_repo: /tmp/pattern-repo\n"
                "imported_source: id=exp-demo source=candidates/demo.py promoted_path=curated/experimental/demo.py trust=experimental sanitized=true validated=false repaired=false promoted_to_training=true\n"
                "learned_pattern_delta: 0\n"
            ),
        }
    ]

    lfa.render_interactive_import_training_result(action, executed)

    output = capsys.readouterr().out
    assert "trust_level_applied: experimental" in output
    assert "validation_result: blocked" in output
    assert "repair_result: failed" in output
    assert "what_happened: The script failed validation and was added as experimental." in output
