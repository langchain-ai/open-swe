from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SKILL = ROOT / ".claude/skills/openswe-wave"
FIXTURES = Path(__file__).parent / "fixtures/openswe_wave"
MODULE_PATH = SKILL / "scripts/openswe_wave.py"
SPEC = importlib.util.spec_from_file_location("openswe_wave", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
wave = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wave
SPEC.loader.exec_module(wave)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _watch_snapshot(
    state: str | None,
    *,
    observed_at: str,
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "linear": {
            "viewer": {"id": "session"},
            "issue": {"comments": {"nodes": comments or []}},
        },
        "langgraph": {"thread": {"metadata": {}}, "runs": []},
        "pr": {"state": state} if state else {},
        "pr_number": 53 if state else None,
        "unresolved_review_thread_ids": [],
        "latest_run_status": None,
        "latest_run_at": None,
        "error_run_ids": [],
        "observed_at": observed_at,
    }


def _watch_args(*, pr_number: int | None, iterations: int) -> SimpleNamespace:
    return SimpleNamespace(
        issue_id="issue",
        repo="owner/repo",
        thread_id="thread",
        pr_number=pr_number,
        session_user_id="session",
        iterations=iterations,
        interval=0,
        run_stall_seconds=1800,
        apply=False,
    )


def test_wave_two_replay_reduces_wakes_and_suppresses_self() -> None:
    recorded = fixture("oswe-79-events.json")

    result = wave.replay_events(recorded["events"], recorded["session_user_id"])

    assert result["raw_events"] == 15
    assert result["wake_count"] == 5
    assert result["wake_count"] <= 6
    assert result["self_authored_suppressed"] == 2
    assert [item["wake_node"] for item in result["wakes"]] == [
        "plan_posted",
        "terminal_run_error",
        "review_findings_posted",
        "review_findings_posted",
        "terminal_merged",
    ]


@pytest.mark.parametrize("name", ["oswe-89-events.json", "oswe-90-events.json"])
def test_happy_path_replays_stay_within_five_wakes(name: str) -> None:
    recorded = fixture(name)

    result = wave.replay_events(recorded["events"], recorded["session_user_id"])

    assert result["wake_count"] <= 5
    assert {item["wake_node"] for item in result["wakes"]} <= set(wave.WAKE_NODES)


def test_replay_coalesces_actionable_state_dump() -> None:
    events = [
        {"poll_id": "same", "kind": "review_findings", "summary": "finding"},
        {"poll_id": "same", "kind": "run_error", "summary": "error"},
    ]

    result = wave.replay_events(events, "session")

    assert result["wake_count"] == 1
    assert result["wakes"][0]["wake_node"] == "terminal_run_error"
    assert len(result["wakes"][0]["evidence"]) == 2


def test_unhandled_and_terminal_observations_beat_plan_in_same_poll() -> None:
    events = [
        {"poll_id": "same", "kind": "plan_posted"},
        {"poll_id": "same", "kind": "merged"},
        {"poll_id": "same", "kind": "unhandled"},
    ]

    result = wave.replay_events(events, "session")

    assert result["wake_count"] == 1
    assert result["wakes"][0]["wake_node"] == "unhandled_condition"


def test_live_poll_assigns_one_id_to_every_observation() -> None:
    events = [
        {"kind": "review_findings", "poll_id": "comment-time"},
        {"kind": "merged"},
        {"kind": "run_error"},
    ]

    assigned = wave.assign_poll_id(events, "poll-now")
    result = wave.replay_events(assigned, "session")

    assert {event["poll_id"] for event in assigned} == {"poll-now"}
    assert result["wake_count"] == 1
    assert result["wakes"][0]["wake_node"] == "terminal_run_error"


def test_persistent_unhandled_fingerprint_ignores_poll_id() -> None:
    first = {
        "kind": "unhandled",
        "source": "langgraph",
        "summary": "busy thread has no recent activity",
        "poll_id": "poll-1",
    }
    second = {**first, "poll_id": "poll-2"}

    assert wave.event_fingerprint(first) == wave.event_fingerprint(second)


def test_transition_detection_uses_new_thread_and_error_ids() -> None:
    previous = {
        "pr": {"state": "OPEN"},
        "unresolved_review_thread_ids": ["old"],
        "error_run_ids": ["run-old"],
    }
    current = {
        "pr": {"state": "OPEN"},
        "unresolved_review_thread_ids": ["old", "new"],
        "error_run_ids": ["run-old", "run-new"],
    }

    events = wave.snapshot_transition_events(previous, current)

    assert [event["kind"] for event in events] == ["review_findings", "run_error"]


def test_liveness_wakes_only_when_silence_bound_is_crossed() -> None:
    previous = {
        "langgraph": {"thread": {"status": "busy"}},
        "latest_run_at": "2026-07-23T00:00:00Z",
        "observed_at": "2026-07-23T00:29:59Z",
    }
    current = {
        "langgraph": {"thread": {"status": "busy"}},
        "latest_run_at": "2026-07-23T00:00:00Z",
        "observed_at": "2026-07-23T00:30:01Z",
    }

    event = wave.liveness_event(previous, current, 1800)

    assert event is not None
    assert event["kind"] == "unhandled"
    assert wave.liveness_event(current, current, 1800) is None


def test_github_snapshot_paginates_complete_actor_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        calls.append(variables)
        cursor = variables.get("cursor")
        if "WaveLabels" in query:
            connection = {"labels": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        elif "WaveReviewThreads" in query:
            connection = {"reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        else:
            connection = {
                "timelineItems": {
                    "nodes": [
                        {
                            "actor": {
                                "__typename": "Bot",
                                "login": wave.AGENT_BOT_LOGIN,
                            },
                            "createdAt": "now",
                        }
                    ],
                    "pageInfo": {
                        "hasNextPage": cursor is None,
                        "endCursor": "next" if cursor is None else None,
                    },
                }
            }
        return {
            "repository": {
                "defaultBranchRef": {"name": "main"},
                "pullRequest": {"headRefOid": "head", **connection},
            }
        }

    monkeypatch.setattr(wave, "gh_graphql", fake_graphql)

    pr = wave.github_pr_snapshot("owner/repo", 7)

    assert calls == [
        {"owner": "owner", "repo": "repo", "number": 7},
        {"owner": "owner", "repo": "repo", "number": 7, "cursor": "next"},
        {"owner": "owner", "repo": "repo", "number": 7},
        {"owner": "owner", "repo": "repo", "number": 7},
    ]
    assert pr["timeline_complete"] is True
    assert len(pr["timelineItems"]["nodes"]) == 2


def test_linear_snapshot_paginates_all_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_linear(_query: str, variables: dict[str, Any]) -> dict[str, Any]:
        calls.append(variables)
        cursor = variables.get("cursor")
        return {
            "viewer": {"id": "session"},
            "issue": {
                "id": "issue",
                "comments": {
                    "nodes": [{"id": "first" if cursor is None else "second"}],
                    "pageInfo": {
                        "hasNextPage": cursor is None,
                        "endCursor": "next" if cursor is None else None,
                    },
                },
            },
        }

    monkeypatch.setattr(wave, "_linear_graphql", fake_linear)

    snapshot = wave.linear_snapshot("issue")

    assert calls == [{"id": "issue"}, {"id": "issue", "cursor": "next"}]
    assert [item["id"] for item in snapshot["issue"]["comments"]["nodes"]] == [
        "first",
        "second",
    ]


def test_green_draft_blocks_incomplete_timeline() -> None:
    snapshot = fixture("pr-43-green-draft.json")
    snapshot.pop("convert_to_draft_events")
    snapshot["pr"]["timelineItems"] = {
        "nodes": [{"actor": {"__typename": "Bot", "login": wave.AGENT_BOT_LOGIN}}]
    }

    decision = wave.recovery_decision(snapshot)

    assert not decision.eligible
    assert "timeline evidence is incomplete" in " ".join(decision.blockers)


def test_green_draft_fixture_is_eligible_and_dry_run_only() -> None:
    snapshot = fixture("pr-43-green-draft.json")

    decision = wave.recovery_decision(snapshot)

    assert decision.eligible
    assert decision.reason == "green_draft"
    assert decision.commands[0][:3] == ("gh", "pr", "ready")
    assert "--auto" in decision.commands[1]
    assert "--squash" in decision.commands[1]
    assert decision.commands[1][-2:] == ("--match-head-commit", snapshot["pr"]["headRefOid"])
    assert snapshot["inferred_fields"]


def test_queue_stall_fixture_is_eligible_and_uses_arm_cycle() -> None:
    snapshot = fixture("pr-44-queue-stall.json")

    decision = wave.recovery_decision(snapshot)

    assert decision.eligible
    assert decision.reason == "queue_stall"
    assert "--disable-auto" in decision.commands[0]
    assert decision.commands[0][-2:] == ("--match-head-commit", snapshot["pr"]["headRefOid"])
    assert "--auto" in decision.commands[1]
    assert "--squash" in decision.commands[1]
    assert decision.commands[1][-2:] == ("--match-head-commit", snapshot["pr"]["headRefOid"])
    assert "isInMergeQueue" in wave.PR_QUERY


def test_green_draft_requires_canonical_bot_actor() -> None:
    snapshot = fixture("pr-43-green-draft.json")
    snapshot["convert_to_draft_events"][0]["actor"] = {
        "__typename": "User",
        "login": "ericlitman",
    }

    decision = wave.recovery_decision(snapshot)

    assert not decision.eligible
    assert "canonical agent Bot" in " ".join(decision.blockers)


def test_recoveries_respect_merge_hold_and_action_dedupe() -> None:
    snapshot = fixture("pr-44-queue-stall.json")
    snapshot["pr"]["labels"]["nodes"] = [{"name": "hold-merge"}]
    held = wave.recovery_decision(snapshot)
    assert not held.eligible
    assert "merge-hold label" in " ".join(held.blockers)

    snapshot["pr"]["labels"]["nodes"] = []
    marker = wave.recovery_decision(snapshot).marker
    snapshot["linear_comments"] = [{"body": marker}]
    duplicate = wave.recovery_decision(snapshot)
    assert not duplicate.eligible
    assert "already has an action log" in " ".join(duplicate.blockers)


def test_apply_recovery_rechecks_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = fixture("pr-43-green-draft.json")
    decision = wave.recovery_decision(initial)
    after_ready = deepcopy(initial)
    after_ready["pr"]["isDraft"] = False
    applied = deepcopy(after_ready)
    applied["pr"]["autoMergeRequest"] = {"enabledAt": "now"}
    states = iter(
        [deepcopy(initial), deepcopy(initial), after_ready, deepcopy(after_ready), applied]
    )
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(wave, "_run", lambda command, **_kwargs: commands.append(tuple(command)))
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)

    result = wave.apply_recovery(initial, decision, lambda: next(states))

    assert result == {"status": "applied", "verified": True}
    assert commands == list(decision.commands)


def test_apply_recovery_logs_start_before_first_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = fixture("pr-44-queue-stall.json")
    decision = wave.recovery_decision(initial)
    after_disable = deepcopy(initial)
    after_disable["pr"]["autoMergeRequest"] = None
    applied = deepcopy(initial)
    applied["pr"]["autoMergeRequest"] = {"enabledAt": "now"}
    states = iter([deepcopy(initial), deepcopy(initial), after_disable, applied])
    order: list[str] = []
    monkeypatch.setattr(wave, "_run", lambda _command, **_kwargs: order.append("command"))
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)

    result = wave.apply_recovery(
        initial,
        decision,
        lambda: next(states),
        before_actions=lambda: order.append("log"),
    )

    assert result["status"] == "applied"
    assert order[0] == "log"
    assert order[1:] == ["command", "command"]


def test_apply_recovery_blocks_stale_head(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = fixture("pr-44-queue-stall.json")
    decision = wave.recovery_decision(initial)
    stale = deepcopy(initial)
    stale["pr"]["headRefOid"] = "new-head"
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(wave, "_run", lambda command, **_kwargs: commands.append(tuple(command)))

    result = wave.apply_recovery(initial, decision, lambda: stale)

    assert result["status"] == "blocked_after_recheck"
    assert commands == []


def test_monitor_recovery_dry_run_has_no_wake_node() -> None:
    snapshot = fixture("pr-43-green-draft.json")

    event = wave.monitor_recovery(
        snapshot,
        apply=False,
        refresh=lambda: snapshot,
        post_log=lambda _body: None,
    )

    assert event is not None
    assert event["kind"] == "recovery_dry_run"
    assert "wake_node" not in event


def test_monitor_recovery_logs_failure_and_wakes_unhandled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = fixture("pr-44-queue-stall.json")
    logs: list[str] = []
    monkeypatch.setattr(
        wave,
        "apply_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(wave.WaveOpsError("boom")),
    )

    event = wave.monitor_recovery(
        snapshot,
        apply=True,
        refresh=lambda: snapshot,
        post_log=logs.append,
    )

    assert event is not None
    assert event["kind"] == "unhandled"
    assert "boom" in event["summary"]
    assert len(logs) == 1
    assert "action_failed" in logs[0]


def test_monitor_can_start_before_pr_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wave,
        "linear_snapshot",
        lambda _issue: {"viewer": {"id": "session"}, "issue": {"comments": {"nodes": []}}},
    )
    monkeypatch.setattr(
        wave,
        "langgraph_snapshot",
        lambda _thread: {"thread": {"metadata": {}}, "runs": []},
    )

    snapshot = wave.live_snapshot("issue", "thread", "owner/repo", None)

    assert snapshot["pr"] == {}
    assert snapshot["pr_number"] is None


def test_terminal_state_ignores_absent_pr_without_latching() -> None:
    emitted_states: set[str] = set()

    event = wave.terminal_pr_state_event({"pr": {}}, emitted_states)

    assert event is None
    assert emitted_states == set()


def test_watch_emits_terminal_merged_once_for_explicit_already_merged_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_comments = [
        {
            "id": "baseline-plan",
            "body": "/plan ready for review",
            "createdAt": "baseline-comment",
            "user": {"id": "operator"},
        }
    ]
    snapshots = iter(
        [
            _watch_snapshot("MERGED", observed_at="baseline", comments=baseline_comments),
            _watch_snapshot("MERGED", observed_at="poll-1", comments=baseline_comments),
            _watch_snapshot("MERGED", observed_at="poll-2", comments=baseline_comments),
        ]
    )
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args: next(snapshots))
    monkeypatch.setattr(wave, "monitor_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_watch(_watch_args(pr_number=53, iterations=2))

    assert result == 0
    assert [item["wake_node"] for item in emitted] == ["terminal_merged"]


def test_watch_discovers_already_merged_pr_from_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_numbers: list[int] = []
    recovery_numbers: list[int] = []
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        wave,
        "linear_snapshot",
        lambda _issue: {"viewer": {"id": "session"}, "issue": {"comments": {"nodes": []}}},
    )
    monkeypatch.setattr(
        wave,
        "langgraph_snapshot",
        lambda _thread: {"thread": {"metadata": {"pr_number": 53}}, "runs": []},
    )

    def github_snapshot(_repo: str, number: int) -> dict[str, Any]:
        github_numbers.append(number)
        return {"state": "MERGED", "reviewThreads": {"nodes": []}}

    def monitor_recovery(snapshot: dict[str, Any], **_kwargs: Any) -> None:
        recovery_numbers.append(snapshot["pr_number"])

    monkeypatch.setattr(wave, "github_pr_snapshot", github_snapshot)
    monkeypatch.setattr(wave, "monitor_recovery", monitor_recovery)
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_watch(_watch_args(pr_number=None, iterations=1))

    assert result == 0
    assert github_numbers == [53, 53]
    assert recovery_numbers == [53]
    assert [item["wake_node"] for item in emitted] == ["terminal_merged"]


def test_watch_emits_terminal_closed_for_already_closed_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(
        [
            _watch_snapshot("CLOSED", observed_at="baseline"),
            _watch_snapshot("CLOSED", observed_at="poll-1"),
        ]
    )
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args: next(snapshots))
    monkeypatch.setattr(wave, "monitor_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_watch(_watch_args(pr_number=53, iterations=1))

    assert result == 0
    assert [item["wake_node"] for item in emitted] == ["terminal_closed"]


def test_watch_emits_live_open_to_merged_transition_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter(
        [
            _watch_snapshot("OPEN", observed_at="baseline"),
            _watch_snapshot("MERGED", observed_at="poll-1"),
            _watch_snapshot("MERGED", observed_at="poll-2"),
        ]
    )
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args: next(snapshots))
    monkeypatch.setattr(wave, "monitor_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_watch(_watch_args(pr_number=53, iterations=2))

    assert result == 0
    assert [item["wake_node"] for item in emitted] == ["terminal_merged"]


def test_failed_poll_does_not_latch_later_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots: list[dict[str, Any] | Exception] = [
        _watch_snapshot("OPEN", observed_at="baseline"),
        wave.WaveOpsError("transient"),
        _watch_snapshot("MERGED", observed_at="poll-2"),
    ]
    emitted: list[dict[str, Any]] = []

    def live_snapshot(*_args: Any) -> dict[str, Any]:
        snapshot = snapshots.pop(0)
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot

    monkeypatch.setattr(wave, "live_snapshot", live_snapshot)
    monkeypatch.setattr(wave, "monitor_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wave.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_watch(_watch_args(pr_number=53, iterations=2))

    assert result == 0
    assert [item["wake_node"] for item in emitted] == [
        "unhandled_condition",
        "terminal_merged",
    ]


def test_anchor_sweep_reports_present_moved_and_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "new.py").write_text("def moved_symbol():\n    return True\n")
    (repo / "present.py").write_text("def present_symbol():\n    return True\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)

    results = wave.anchor_sweep(
        str(repo),
        "HEAD",
        "Use `present.py:present_symbol`, `old.py:moved_symbol`, and `missing.py:nope`.",
    )

    assert [item["status"] for item in results] == ["present", "moved", "missing"]
    assert results[1]["matches"] == ["new.py"]


def test_anchor_sweep_checks_standalone_symbols(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "module.py").write_text(
        "class RecoveryDecision:\n    pass\n\ndef cited_symbol():\n    return True\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)

    results = wave.anchor_sweep(
        str(repo),
        "HEAD",
        "Call `cited_symbol` and `RecoveryDecision`; avoid `missing_symbol`.",
    )

    assert [item["status"] for item in results] == ["present", "present", "missing"]
    assert results[0]["matches"] == ["module.py"]


def test_trace_digest_reports_tokens_errors_activity_and_prompt_trend() -> None:
    thread = "thread-1"
    runs = [
        {
            "id": "root-1",
            "metadata": {"thread_id": thread},
            "status": "success",
            "start_time": "2026-07-23T01:00:00Z",
            "inputs": {"prompt": "a" * 100},
            "total_tokens": 1000,
        },
        {
            "id": "root-2",
            "metadata": {"thread_id": thread},
            "status": "error",
            "error": "provider 408",
            "start_time": "2026-07-23T02:00:00Z",
            "inputs": {"prompt": "b" * 3000},
            "usage_metadata": {"total_tokens": 2500},
        },
        {
            "id": "other",
            "metadata": {"thread_id": "other"},
            "status": "success",
            "total_tokens": 9999,
        },
    ]

    digest = wave.trace_digest(runs, thread)

    assert digest["total_tokens"] == 3500
    assert digest["errors"][0]["id"] == "root-2"
    assert digest["recent_activity"][0]["id"] == "root-2"
    assert digest["prompt_size_trend"]["direction"] == "up"


def test_trace_digest_falls_back_to_child_llm_tokens() -> None:
    runs = [
        {
            "id": "root",
            "metadata": {"thread_id": "thread"},
            "parent_run_id": None,
            "status": "success",
            "inputs": {},
        },
        {
            "id": "llm",
            "metadata": {"thread_id": "thread"},
            "parent_run_id": "root",
            "status": "success",
            "total_tokens": 777,
            "inputs": {},
        },
    ]

    digest = wave.trace_digest(runs, "thread")

    assert digest["total_tokens"] == 777


def test_trace_digest_applies_child_fallback_per_root() -> None:
    runs = [
        {
            "id": "root-1",
            "trace_id": "trace-1",
            "metadata": {"thread_id": "thread"},
            "parent_run_id": None,
            "status": "success",
            "total_tokens": 100,
            "inputs": {},
        },
        {
            "id": "root-2",
            "trace_id": "trace-2",
            "metadata": {"thread_id": "thread"},
            "parent_run_id": None,
            "status": "error",
            "inputs": {},
        },
        {
            "id": "llm-2",
            "trace_id": "trace-2",
            "metadata": {"thread_id": "thread"},
            "parent_run_id": "root-2",
            "status": "success",
            "total_tokens": 200,
            "inputs": {},
        },
    ]

    digest = wave.trace_digest(runs, "thread")

    assert [root["tokens"] for root in digest["root_runs"]] == [100, 200]
    assert digest["total_tokens"] == 300


def test_trace_digest_reads_token_attributes_from_run_models() -> None:
    run = SimpleNamespace(
        id="root",
        metadata={"thread_id": "thread"},
        parent_run_id=None,
        status="success",
        start_time="now",
        inputs={},
        total_tokens=4321,
        error=None,
        name="agent",
    )

    digest = wave.trace_digest([run], "thread")

    assert digest["total_tokens"] == 4321


def test_missing_credentials_name_exact_exports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LANGGRAPH_URL", raising=False)

    with pytest.raises(wave.WaveOpsError) as exc:
        wave.require_env("LINEAR_API_KEY", "LANGGRAPH_URL")

    message = str(exc.value)
    assert "LINEAR_API_KEY" in message
    assert "LANGGRAPH_URL" in message
    assert "export LINEAR_API_KEY=..." in message


@pytest.mark.parametrize("script", ["wave-monitor", "anchor-sweep", "trace-digest"])
def test_scripts_are_executable_and_offer_help(script: str) -> None:
    target = SKILL / "scripts" / script

    result = subprocess.run([str(target), "--help"], text=True, capture_output=True, check=False)

    assert target.stat().st_mode & 0o111
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_skill_contains_all_deliverables_and_closeout_wording() -> None:
    templates = (SKILL / "references/comment-templates.md").read_text()
    skill = (SKILL / "SKILL.md").read_text()

    for heading in (
        "## Dispatch",
        "## Approval",
        "## Reject",
        "## Nudge",
        "## Spot-audit",
        "## Closeout",
        "## OSWE-100 tally",
    ):
        assert heading in templates
    assert (
        "verify the Linear issue auto-transitioned on merge; flip manually only as fallback"
        in templates
    )
    assert all(f"`{node}`" in skill for node in wave.WAKE_NODES)
