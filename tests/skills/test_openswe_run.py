from __future__ import annotations

import argparse
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SKILL = ROOT / ".claude/skills/openswe-run"
WAVE_SKILL = ROOT / ".claude/skills/openswe-wave"
SCRIPT_PATH = SKILL / "scripts/openswe-run"

# The script is deliberately extensionless (it is the skill's CLI entry point),
# so spec_from_file_location cannot infer a loader for it.
LOADER = importlib.machinery.SourceFileLoader("openswe_run", str(SCRIPT_PATH))
SPEC = importlib.util.spec_from_loader("openswe_run", LOADER)
assert SPEC is not None
run = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run
LOADER.exec_module(run)


def test_entry_point_is_executable_and_self_describes() -> None:
    result = subprocess.run(
        [str(SCRIPT_PATH), "--help"], text=True, capture_output=True, check=False
    )

    assert SCRIPT_PATH.stat().st_mode & 0o111
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_every_reference_named_in_skill_md_resolves() -> None:
    """A doc that points at a missing reference is a broken skill, not a typo.

    Tracked references live here; the adjudication checklist resolves from the
    sibling openswe-wave, the same fallback the scripts use.
    """
    skill_md = (SKILL / "SKILL.md").read_text()

    for name in sorted(set(re.findall(r"references/([A-Za-z0-9._-]+\.md)", skill_md))):
        local = SKILL / "references" / name
        sibling = WAVE_SKILL / "references" / name
        assert local.is_file() or sibling.is_file(), (
            f"SKILL.md names references/{name}, which exists neither here nor in openswe-wave"
        )


def test_wave_assets_resolve_from_the_sibling_skill_in_a_checkout() -> None:
    """The wave assets come from the sibling skill; without this fallback the
    skill is unrunnable from a checkout."""
    assert not (SKILL / "scripts/openswe_wave.py").exists()

    resolved = run.wave_scripts_dir()

    # wave_scripts_dir() derives from Path(__file__).resolve(); compare resolved
    # paths so a checkout reached through a symlink (macOS /tmp) still matches.
    assert resolved.resolve() == (WAVE_SKILL / "scripts").resolve()
    assert (resolved / "openswe_wave.py").is_file()
    assert (resolved / "wave-monitor").is_file()


def test_wave_symbols_the_script_calls_still_exist() -> None:
    """Guards against a rename in openswe-wave silently breaking this skill."""
    wave = run.import_wave_module()

    assert callable(wave.derive_linear_thread_id)


@pytest.mark.parametrize(
    "body",
    [
        "@openswe repo owner/name — Execute ABC-1 only. See <https://example.com>.",
        "@openswe plain body with no placeholders",
    ],
)
def test_placeholder_guard_allows_filled_bodies(body: str) -> None:
    run.guard_placeholders("ABC-1", body, False)


def test_placeholder_guard_rejects_unfilled_bodies() -> None:
    with pytest.raises(run.RunError, match="placeholder"):
        run.guard_placeholders("ABC-1", "@openswe Execute <TICKET> only.", False)


def test_dispatch_template_leaves_no_placeholder_its_own_guard_would_reject() -> None:
    body = run.DISPATCH_TEMPLATE.format(
        repo="owner/name",
        ticket="ABC-1",
        ref="main",
        scope="do the thing",
        boundaries="nothing else",
        verify="focused tests",
    )

    run.guard_placeholders("ABC-1", body, False)


def _comment(
    comment_id: str,
    body: str,
    created_at: str,
    user_id: str,
    user_name: str,
) -> dict:
    return {
        "id": comment_id,
        "body": body,
        "createdAt": created_at,
        "user": {"id": user_id, "name": user_name},
    }


def _plan_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    comments: list[dict],
    *,
    last: int | None = None,
    viewer_id: str = "viewer-1",
) -> str:
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: [])
    monkeypatch.setattr(run, "resolve_issue", lambda ticket: {"id": "issue-1"})
    monkeypatch.setattr(
        run,
        "linear_snapshot",
        lambda issue_id: {"viewer": {"id": viewer_id}, "comments": comments},
    )

    assert run.cmd_plan(argparse.Namespace(ticket="ABC-1", last=last)) == 0
    return capsys.readouterr().out


def test_plan_defaults_to_all_non_viewer_comments_since_latest_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    comments = [
        _comment(
            "approval", "@openswe Plan approved.", "2026-07-27T21:38:30Z", "viewer-1", "Operator"
        ),
        _comment("plan", "## Plan\nImplement it", "2026-07-27T21:37:58Z", "agent-1", "Open SWE"),
        _comment(
            "progress", "Re-anchoring against main", "2026-07-27T21:34:58Z", "agent-1", "Open SWE"
        ),
        _comment("ack", "On it!", "2026-07-27T21:34:41Z", "agent-1", "Open SWE"),
        _comment(
            "dispatch",
            "@openswe repo owner/name — Execute ABC-1 only.\n\nRequired scope: fix it.",
            "2026-07-27T21:34:40Z",
            "viewer-1",
            "Operator",
        ),
        _comment("old", "Earlier run", "2026-07-27T20:00:00Z", "agent-1", "Open SWE"),
    ]

    output = _plan_output(monkeypatch, capsys, comments)

    assert "Earlier run" not in output
    assert output.index("On it!") < output.index("Re-anchoring against main")
    assert output.index("Re-anchoring against main") < output.index("## Plan")
    assert output.count("----- Open SWE at") == 3


def test_plan_scopes_after_custom_repo_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    comments = [
        _comment("old", "Earlier run", "2026-07-27T20:00:00Z", "agent-1", "Open SWE"),
        _comment(
            "dispatch",
            "@openswe repo owner/name\n\nCustom dispatch body for ABC-1.",
            "2026-07-27T21:34:40Z",
            "viewer-1",
            "Operator",
        ),
        _comment("ack", "On it!", "2026-07-27T21:34:41Z", "agent-1", "Open SWE"),
        _comment("plan", "## Plan", "2026-07-27T21:37:58Z", "agent-1", "Open SWE"),
    ]

    output = _plan_output(monkeypatch, capsys, comments)

    assert "Earlier run" not in output
    assert output.index("On it!") < output.index("## Plan")
    assert output.count("----- Open SWE at") == 2


def test_plan_without_dispatch_falls_back_to_all_comments_with_true_authors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    comments = [
        _comment("agent", "## Plan", "2026-07-27T21:37:58Z", "agent-1", "Open SWE"),
        _comment(
            "operator", "Please revise", "2026-07-27T21:35:00Z", "operator-1", "Mobilyze Agents"
        ),
    ]

    output = _plan_output(monkeypatch, capsys, comments, viewer_id="service-viewer")

    assert output.index("Mobilyze Agents") < output.index("Open SWE")
    assert "----- Mobilyze Agents at 2026-07-27T21:35:00Z -----" in output
    assert "----- Open SWE at 2026-07-27T21:37:58Z -----" in output


@pytest.mark.parametrize(
    ("last", "expected", "excluded"),
    [
        (2, ["Progress", "## Plan"], ["On it!"]),
        (10, ["On it!", "Progress", "## Plan"], []),
    ],
)
def test_plan_last_narrows_the_dispatch_scoped_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    last: int,
    expected: list[str],
    excluded: list[str],
) -> None:
    comments = [
        _comment("plan", "## Plan", "2026-07-27T21:37:58Z", "agent-1", "Open SWE"),
        _comment("progress", "Progress", "2026-07-27T21:34:58Z", "agent-1", "Open SWE"),
        _comment("ack", "On it!", "2026-07-27T21:34:41Z", "agent-1", "Open SWE"),
        _comment(
            "dispatch",
            "@openswe repo owner/name — Execute ABC-1 only.",
            "2026-07-27T21:34:40Z",
            "viewer-1",
            "Operator",
        ),
    ]

    output = _plan_output(monkeypatch, capsys, comments, last=last)

    positions = [output.index(body) for body in expected]
    assert positions == sorted(positions)
    assert all(body not in output for body in excluded)


def test_locked_plan_statuses_match_the_products_refusals() -> None:
    """The dashboard refuses these; this path must not be more permissive."""
    assert set(run.PLAN_STATUS_LOCKED) == {"shared", "cancelled"}
    assert run.PLAN_STATUS_LOCKED[0] in run.PLAN_STATUS_SNIPPET
    assert run.PLAN_STATUS_LOCKED[1] in run.PLAN_STATUS_SNIPPET


def test_no_operator_home_path_is_hardcoded_in_the_source() -> None:
    """This repo is public. The resolved value contains a home path by design;
    what must not appear is a literal one baked into the source."""
    assert run.DEFAULT_STABLE_ROOT.startswith(str(Path.home()))

    for path in (SCRIPT_PATH, SKILL / "SKILL.md"):
        assert not re.search(r"/(Users|home)/[a-z]", path.read_text()), (
            f"{path.name} hardcodes an operator home directory"
        )


@pytest.mark.parametrize(
    "body",
    [
        "@openswe Plan approved.",
        "@openswe repo owner/name — Execute ABC-1 only.",
        "@openswe repo:owner/name — Execute ABC-1 only.",
        "@OpenSWE Plan approved.",
        "@OpenSWE Repo owner/name — Execute ABC-1 only.",
        "@OpEnSwE rEpO:owner/name — Execute ABC-1 only.",
    ],
)
def test_body_hygiene_allows_only_the_first_line_directive(body: str) -> None:
    run.guard_body_hygiene(body)


@pytest.mark.parametrize(
    "body",
    [
        " @openswe Plan approved.",
        "@openswe Plan approved.\n@openswe Continue.",
        "@openswe Plan approved.\n@OpenSWE Continue.",
        "@openswe Plan approved.\nExample: repo owner/name",
        "@openswe Plan approved.\nExample: RePo owner/name",
        "@openswe Plan approved.\nExample: repo:owner/name",
        "@openswe Plan approved.\nExample: repo: owner/name",
        "@openswe Continue in repo owner/name.",
    ],
)
def test_body_hygiene_rejects_ambiguous_directives(body: str) -> None:
    with pytest.raises(run.RunError):
        run.guard_body_hygiene(body)


def test_force_cannot_bypass_body_hygiene(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run,
        "_post_with_handoff",
        lambda *args, **kwargs: pytest.fail("handoff child must not start after a hygiene refusal"),
    )
    args = argparse.Namespace(ticket="ABC-1", force=True)

    with pytest.raises(run.RunError, match="begin exactly"):
        run._post_prepared(
            args,
            "comment",
            "not a directive",
            issue={"id": "issue-1", "identifier": "ABC-1"},
        )


def test_handoff_monitor_uses_one_sdk_import_and_recent_run_window() -> None:
    assert run.HANDOFF_MONITOR_SNIPPET.count("from langgraph_sdk import get_client") == 1
    assert "client = get_client(url=URL)" in run.HANDOFF_MONITOR_SNIPPET
    assert "snapshot(client)" in run.HANDOFF_MONITOR_SNIPPET
    assert "sys.stdin.readline()" in run.HANDOFF_MONITOR_SNIPPET
    assert "runs.list(THREAD, limit=100)" in run.HANDOFF_MONITOR_SNIPPET
    assert "limit=1000" not in run.HANDOFF_MONITOR_SNIPPET


def test_handoff_start_spawns_one_child_and_waits_for_baseline_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    class Stdout:
        def readline(self) -> str:
            return run.HANDOFF_BASELINE_SENTINEL + "child-owned baseline"

    class Process:
        stdout = Stdout()

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(run, "resolve_monitor_python", lambda: ["python-with-sdk"])
    monkeypatch.setattr(run.subprocess, "Popen", popen)
    monkeypatch.setattr(run.select, "select", lambda *args: ([Process.stdout], [], []))

    process = run._start_handoff_process("comment", "ABC-1", "thread-1")

    assert isinstance(process, Process)
    assert len(calls) == 1
    assert calls[0][0] == ["python-with-sdk", "-c", run.HANDOFF_MONITOR_SNIPPET]


@pytest.mark.parametrize("baseline_status", ["missing", "idle", "busy"])
def test_child_preserves_handoff_success_rules(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    baseline_status: str,
) -> None:
    class Missing(Exception):
        status_code = 404

    if baseline_status == "missing":
        statuses = [Missing(), {"status": "busy"}]
        run_lists = [[]]
    elif baseline_status == "idle":
        statuses = [{"status": "idle"}, {"status": "busy"}]
        run_lists = [[{"run_id": "run-1"}], [{"run_id": "run-1"}]]
    else:
        statuses = [{"status": "busy"}, {"status": "busy"}, {"status": "busy"}]
        run_lists = [
            [{"run_id": "run-1"}],
            [{"run_id": "run-1"}],
            [{"run_id": "run-1"}, {"run_id": "run-2"}],
        ]

    class Threads:
        async def get(self, thread_id):
            value = statuses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

    class Runs:
        async def list(self, thread_id, *, limit):
            assert limit == 100
            return run_lists.pop(0)

    client = types.SimpleNamespace(threads=Threads(), runs=Runs())
    sdk = types.ModuleType("langgraph_sdk")
    sdk.get_client = lambda *, url: client
    monkeypatch.setitem(sys.modules, "langgraph_sdk", sdk)
    monkeypatch.setenv("OPENSWE_HANDOFF_THREAD", "thread-1")
    monkeypatch.setenv("OPENSWE_HANDOFF_ACTION", "comment")
    monkeypatch.setenv("OPENSWE_HANDOFF_TICKET", "ABC-1")
    monkeypatch.setenv("OPENSWE_HANDOFF_PLAN_CONTEXT", "null")
    monkeypatch.setenv("OPENSWE_HANDOFF_TIMEOUT", "1")
    monkeypatch.setenv("OPENSWE_HANDOFF_POLL_INTERVAL", "0")
    monkeypatch.setattr(sys, "stdin", io.StringIO("posted\n"))

    exec(run.HANDOFF_MONITOR_SNIPPET, {})

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    result = json.loads(lines[-1][len(run.HANDOFF_RESULT_SENTINEL) :])
    assert result["handoff"]["thread_status"] == "busy"
    if baseline_status == "busy":
        assert result["handoff"]["run_ids"] == ["run-1", "run-2"]


def test_child_poll_timeout_is_aggregate_and_cancels_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cancelled = False
    thread_reads = 0

    class Threads:
        async def get(self, thread_id):
            nonlocal cancelled, thread_reads
            thread_reads += 1
            if thread_reads == 1:
                return {"status": "idle"}
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled = True
                raise

    class Runs:
        async def list(self, thread_id, *, limit):
            return []

    client = types.SimpleNamespace(threads=Threads(), runs=Runs())
    sdk = types.ModuleType("langgraph_sdk")
    sdk.get_client = lambda *, url: client
    monkeypatch.setitem(sys.modules, "langgraph_sdk", sdk)
    monkeypatch.setenv("OPENSWE_HANDOFF_THREAD", "thread-1")
    monkeypatch.setenv("OPENSWE_HANDOFF_ACTION", "comment")
    monkeypatch.setenv("OPENSWE_HANDOFF_TICKET", "ABC-1")
    monkeypatch.setenv("OPENSWE_HANDOFF_PLAN_CONTEXT", "null")
    monkeypatch.setenv("OPENSWE_HANDOFF_TIMEOUT", "0.01")
    monkeypatch.setenv("OPENSWE_HANDOFF_POLL_INTERVAL", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO("posted\n"))

    exec(run.HANDOFF_MONITOR_SNIPPET, {})

    lines = capsys.readouterr().out.splitlines()
    result = json.loads(lines[-1][len(run.HANDOFF_RESULT_SENTINEL) :])
    assert cancelled is True
    assert "LangGraph handoff timeout" in result["error"]
    assert '"final": {"error": ""}' in result["error"]
    assert "async with asyncio.timeout(remaining)" in run.HANDOFF_MONITOR_SNIPPET
    assert (
        "await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))" in run.HANDOFF_MONITOR_SNIPPET
    )
    assert "time.sleep(" not in run.HANDOFF_MONITOR_SNIPPET


def test_shared_post_helper_uses_one_child_for_baseline_post_and_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Stdin:
        def write(self, value: str) -> None:
            assert value == "posted\n"
            events.append("signal")

        def flush(self) -> None:
            return None

    process = types.SimpleNamespace(stdin=Stdin())

    def start(*args, **kwargs):
        events.append("baseline")
        return process

    monkeypatch.setattr(run, "_start_handoff_process", start)
    monkeypatch.setattr(run, "post_comment", lambda *args: events.append("post"))
    monkeypatch.setattr(
        run,
        "_await_handoff",
        lambda actual_process, thread_id: (
            events.append("poll") or {"thread_status": "busy", "run_ids": ["run-1"]}
        ),
    )

    final = run._post_with_handoff("comment", "ABC-1", "issue-1", "@openswe Continue", "thread-1")

    assert final == {"thread_status": "busy", "run_ids": ["run-1"]}
    assert events == ["baseline", "post", "signal", "poll"]


@pytest.mark.parametrize(
    ("command", "action", "status", "plan_mode"),
    [
        (run.cmd_approve, "approval", "approved", False),
        (run.cmd_reject, "rejection", "revising", True),
    ],
)
def test_plan_actions_guard_transition_shared_baseline_post_then_poll(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command,
    action: str,
    status: str,
    plan_mode: bool,
) -> None:
    events: list[str] = []
    logs: list[str] = []
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(run, "read_body", lambda args: "@openswe Adjudication body")
    monkeypatch.setattr(run, "guard_body_hygiene", lambda body: events.append("body_hygiene"))
    monkeypatch.setattr(
        run,
        "guard_placeholders",
        lambda ticket, body, force: events.append("placeholders"),
    )
    monkeypatch.setattr(
        run,
        "resolve_issue",
        lambda ticket: {"id": "issue-1", "identifier": "ABC-1"},
    )
    monkeypatch.setattr(
        run,
        "import_wave_module",
        lambda: type("Wave", (), {"derive_linear_thread_id": lambda issue_id: "thread-1"}),
    )

    def set_status(thread_id: str, actual_status: str, *, plan_mode: bool) -> dict:
        assert actual_status == status
        assert plan_mode is expected_plan_mode
        events.append("plan_transition")
        return {"previous": "ready", "status": actual_status, "metadata_ok": True}

    expected_plan_mode = plan_mode
    monkeypatch.setattr(run, "set_plan_status", set_status)
    monkeypatch.setattr(run, "dogfood", lambda ticket, tag, message: logs.append(message))

    def post_with_handoff(actual_action: str, *args, **kwargs) -> dict:
        assert actual_action == action
        events.extend(["baseline", "post_comment", "poll"])
        return {"thread_status": "busy", "run_ids": ["run-1", "run-2"]}

    monkeypatch.setattr(run, "_post_with_handoff", post_with_handoff)
    args = argparse.Namespace(ticket="ABC-1", body_file="body.md", force=False, adjudicated=True)

    assert command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "identifier": "ABC-1",
        "posted": action,
        "handoff": {"thread_status": "busy", "run_ids": ["run-1", "run-2"]},
    }
    assert any("plan record 'ready' ->" in message for message in logs)
    assert any(
        f"{action} posted on ABC-1; handoff status=busy runs=2" in message for message in logs
    )
    assert events == [
        "body_hygiene",
        "placeholders",
        "plan_transition",
        "baseline",
        "post_comment",
        "poll",
    ]


def test_timeout_annotation_exists_only_in_child_timeout_evidence() -> None:
    assert run.HANDOFF_MONITOR_SNIPPET.count("plan_status_nontransactional") == 1
    source = SCRIPT_PATH.read_text()
    assert source.count("plan_status_nontransactional") == 1
    assert 'evidence["plan_status_nontransactional"] = PLAN_CONTEXT' in source


def test_child_timeout_result_is_reported_directly() -> None:
    evidence = {
        "action": "approval",
        "ticket": "ABC-1",
        "thread_id": "thread-1",
        "baseline": {"thread_status": "busy", "run_ids": ["run-1"]},
        "final": {"thread_status": "busy", "run_ids": ["run-1"]},
        "timeout_seconds": 60.0,
        "plan_status_nontransactional": {"status": "approved", "rollback": "not automatic"},
    }

    class Process:
        returncode = 0

        def communicate(self, timeout=None):
            payload = {
                "error": "LangGraph handoff timeout: " + json.dumps(evidence, sort_keys=True)
            }
            return run.HANDOFF_RESULT_SENTINEL + json.dumps(payload) + "\n", ""

    with pytest.raises(run.RunError) as raised:
        run._await_handoff(Process(), "thread-1")

    message = str(raised.value)
    assert '"action": "approval"' in message
    assert '"baseline"' in message
    assert '"final"' in message
    assert '"plan_status_nontransactional"' in message


def test_start_dry_run_does_not_capture_or_poll_handoff(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        run,
        "resolve_issue",
        lambda ticket: {
            "id": "issue-1",
            "identifier": "ABC-1",
            "url": "https://linear.example/ABC-1",
        },
    )
    monkeypatch.setattr(
        run,
        "import_wave_module",
        lambda: type("Wave", (), {"derive_linear_thread_id": lambda issue_id: "thread-1"}),
    )
    monkeypatch.setattr(
        run,
        "_post_with_handoff",
        lambda *args, **kwargs: pytest.fail("dry-run must not start a handoff child"),
    )
    monkeypatch.setattr(
        run,
        "post_comment",
        lambda issue_id, body: pytest.fail("dry-run must not post"),
    )
    args = argparse.Namespace(
        ticket="ABC-1",
        repo="owner/name",
        ref="main",
        scope=None,
        boundaries=None,
        verify=None,
        body_file=None,
        dry_run=True,
        force=False,
    )

    assert run.cmd_start(args) == 0
    capsys.readouterr()
    assert calls == [{"langgraph": False, "github": False}]


def test_start_success_records_handoff_in_json_and_dogfood(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logs: list[str] = []
    final = {"thread_status": "busy", "run_ids": ["run-1"]}
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run,
        "resolve_issue",
        lambda ticket: {
            "id": "issue-1",
            "identifier": "ABC-1",
            "url": "https://linear.example/ABC-1",
        },
    )
    monkeypatch.setattr(
        run,
        "import_wave_module",
        lambda: type("Wave", (), {"derive_linear_thread_id": lambda issue_id: "thread-1"}),
    )
    monkeypatch.setattr(run, "_post_with_handoff", lambda *args, **kwargs: final)
    monkeypatch.setattr(run, "dogfood", lambda ticket, tag, message: logs.append(message))
    args = argparse.Namespace(
        ticket="ABC-1",
        repo="owner/name",
        ref="main",
        scope=None,
        boundaries=None,
        verify=None,
        body_file=None,
        dry_run=False,
        force=False,
    )

    assert run.cmd_start(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["handoff"] == final
    assert logs == [
        "dispatched ABC-1 to owner/name (https://linear.example/ABC-1); handoff status=busy runs=1"
    ]


def test_watch_parser_defaults_to_plan_and_keeps_timeout_override_authoritative() -> None:
    parser = run.build_parser()
    default = parser.parse_args(["watch", "--ticket", "ABC-1", "--repo", "owner/name"])
    explicit = parser.parse_args(
        [
            "watch",
            "--ticket",
            "ABC-1",
            "--repo",
            "owner/name",
            "--phase",
            "delivery",
            "--timeout-min",
            "7.5",
        ]
    )

    assert default.phase == "plan"
    assert default.timeout_min is None
    assert default.func is run.cmd_watch
    assert run.watch_timeout_min(default) == 30.0
    assert run.watch_timeout_min(explicit) == 7.5


@pytest.mark.parametrize(
    ("phase", "override", "expected"),
    [("plan", None, 30.0), ("delivery", None, 90.0), ("plan", 7.5, 7.5)],
)
def test_watch_phase_defaults_and_explicit_override(
    phase: str, override: float | None, expected: float
) -> None:
    args = argparse.Namespace(phase=phase, timeout_min=override)

    assert run.watch_timeout_min(args) == expected


@pytest.mark.parametrize("command_name", ["comment", "nudge"])
def test_midrun_posts_require_langgraph_for_handoff(
    monkeypatch: pytest.MonkeyPatch, command_name: str
) -> None:
    environments: list[dict] = []
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: environments.append(kwargs))
    monkeypatch.setattr(
        run,
        "resolve_issue",
        lambda ticket: {"id": "issue-1", "identifier": "ABC-1"},
    )
    monkeypatch.setattr(run, "read_body", lambda args: "@openswe Continue")
    monkeypatch.setattr(run, "_post_prepared", lambda *args, **kwargs: 0)
    if command_name == "comment":
        args = argparse.Namespace(ticket="ABC-1", body_file="body.md", force=False)
        result = run.cmd_comment(args)
    else:
        args = argparse.Namespace(ticket="ABC-1", minutes=30)
        result = run.cmd_nudge(args)

    assert result == 0
    assert environments == [{"langgraph": True, "github": False}]


@pytest.mark.parametrize(
    ("phase", "override", "elapsed", "expected_timeout"),
    [("plan", None, 1801.0, 30.0), ("delivery", 12.0, 721.0, 12.0)],
)
def test_watch_timeout_evidence_includes_phase_and_effective_deadline(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    override: float | None,
    elapsed: float,
    expected_timeout: float,
) -> None:
    wakes: list[dict] = []
    logs: list[str] = []

    class Process:
        def terminate(self) -> None:
            return None

    moments = iter([0.0, 0.0, elapsed])
    monkeypatch.setattr(run.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(run, "ensure_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(run, "import_wave_module", lambda: object())
    monkeypatch.setattr(
        run,
        "resolve_issue",
        lambda ticket: {"id": "issue-1", "identifier": "ABC-1"},
    )
    monkeypatch.setattr(
        run,
        "linear_snapshot",
        lambda issue_id: {"viewer": {"id": "viewer-1"}, "comments": []},
    )
    monkeypatch.setattr(run, "dogfood", lambda ticket, tag, text: logs.append(text))
    monkeypatch.setattr(run, "_spawn_monitor", lambda args, issue_id: (Process(), []))
    monkeypatch.setattr(run, "_emit_wake", lambda ticket, wake, source: wakes.append(wake))
    args = argparse.Namespace(
        ticket="ABC-1",
        repo="owner/name",
        pr_number=None,
        phase=phase,
        interval=60,
        timeout_min=override,
        heartbeat_min=10,
        max_restarts=2,
        follow=False,
    )

    assert run.cmd_watch(args) == run.WAKE_TIMEOUT_EXIT
    assert phase in logs[0]
    assert wakes == [
        {
            "wake_node": "watch_timeout",
            "summary": f"no {phase} wake within {expected_timeout} minutes; monitor stopped",
            "evidence": {
                "issue_id": "issue-1",
                "identifier": "ABC-1",
                "phase": phase,
                "timeout_min": expected_timeout,
            },
        }
    ]
