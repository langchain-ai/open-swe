from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import timedelta
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


def _watch_args(**overrides: Any) -> SimpleNamespace:
    values = {
        "issue_id": "issue",
        "thread_id": "thread",
        "repo": "owner/repo",
        "pr_number": None,
        "apply": False,
        "session_user_id": "session",
        "interval": 0.05,
        "iterations": 1,
        "run_stall_seconds": 1800,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def test_bear_41_blocker_comments_replay_as_run_blocked() -> None:
    recorded = fixture("bear-41-blocker-comments.json")

    events = wave.comments_to_events(
        recorded["comments"], recorded["session_user_id"], set(recorded["known_ids"])
    )
    result = wave.replay_events(events, recorded["session_user_id"])

    assert [event["kind"] for event in events] == ["run_blocked", "run_blocked"]
    assert result["wake_count"] == 2
    assert [item["wake_node"] for item in result["wakes"]] == [
        "run_blocked",
        "run_blocked",
    ]


def test_bear_41_healthy_quiet_comments_produce_zero_wakes() -> None:
    recorded = fixture("bear-41-healthy-quiet-comments.json")

    events = wave.comments_to_events(
        recorded["comments"], recorded["session_user_id"], set(recorded["known_ids"])
    )
    result = wave.replay_events(events, recorded["session_user_id"])

    assert [event["kind"] for event in events] == ["progress", "progress", "run_blocked"]
    assert result["wake_count"] == 0
    assert result["self_authored_suppressed"] == 1
    assert result["non_actionable_ignored"] == 2


@pytest.mark.parametrize(
    "body",
    [
        "Execution failed and I can't continue.",
        "Pushing the branch failed with permission denied; stopping here.",
    ],
)
def test_equivalent_execution_and_delivery_blockers_wake(body: str) -> None:
    events = wave.comments_to_events(
        [{"id": "comment", "body": body, "user": {"id": "agent"}}],
        "operator",
        set(),
    )

    assert events[0]["kind"] == "run_blocked"
    assert wave.replay_events(events, "operator")["wakes"][0]["wake_node"] == "run_blocked"


@pytest.mark.parametrize(
    "body",
    [
        "The push failed once, but the retry succeeded and the PR is open.",
        "Delivery is blocked on CI while required checks continue.",
        "Holding for review; no delivery failure occurred.",
    ],
)
def test_incomplete_blocker_language_stays_progress(body: str) -> None:
    events = wave.comments_to_events(
        [{"id": "comment", "body": body, "user": {"id": "agent"}}],
        "operator",
        set(),
    )

    assert events[0]["kind"] == "progress"
    assert wave.replay_events(events, "operator")["wake_count"] == 0


def test_replay_coalesces_actionable_state_dump() -> None:
    events = [
        {"poll_id": "same", "kind": "review_findings", "summary": "finding"},
        {"poll_id": "same", "kind": "run_blocked", "summary": "blocked"},
        {"poll_id": "same", "kind": "run_error", "summary": "error"},
    ]

    result = wave.replay_events(events, "session")

    assert result["wake_count"] == 1
    assert result["wakes"][0]["wake_node"] == "terminal_run_error"
    assert len(result["wakes"][0]["evidence"]) == 3


def test_unhandled_and_terminal_observations_beat_plan_in_same_poll() -> None:
    events = [
        {"poll_id": "same", "kind": "plan_posted"},
        {"poll_id": "same", "kind": "run_blocked"},
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

    def fake_graphql(query: str, variables: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
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

    def fake_linear(_query: str, variables: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
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


def test_run_uses_default_timeout_and_wraps_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(wave.subprocess, "run", fake_run)

    assert wave._run(["echo", "ok"]).stdout == "ok"
    with pytest.raises(wave.WaveOpsError, match=r"Command timed out after 60\.0s"):
        wave._run(["sleep", "forever"])

    assert [call["timeout"] for call in calls] == [60.0, 60.0]


@pytest.mark.parametrize(
    ("deadline", "expected_timeout"),
    [(112.5, 12.5), (140.0, 30.0)],
)
def test_linear_graphql_bounds_httpx_timeout_without_network(
    monkeypatch: pytest.MonkeyPatch, deadline: float, expected_timeout: float
) -> None:
    import httpx

    calls: list[dict[str, Any]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"data": {"ok": True}}

    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.setattr(wave.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **kwargs: calls.append(kwargs) or Response(),
    )

    assert wave._linear_graphql("query", {}, deadline=deadline) == {"ok": True}
    assert calls[0]["timeout"] == expected_timeout


def test_langgraph_snapshot_reraises_poll_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langgraph_sdk

    deadline = wave._PollDeadlineError("deadline")

    async def get_thread(_thread_id: str) -> dict[str, Any]:
        raise deadline

    monkeypatch.setenv("LANGGRAPH_URL", "https://langgraph.invalid")
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda **_kwargs: SimpleNamespace(
            threads=SimpleNamespace(get=get_thread), runs=SimpleNamespace()
        ),
    )

    with pytest.raises(wave._PollDeadlineError) as exc_info:
        wave.asyncio.run(wave._langgraph_snapshot("thread", 1.0))

    assert exc_info.value is deadline


def test_langgraph_snapshot_passes_remaining_timeout_and_is_aggregate_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langgraph_sdk

    client_calls: list[dict[str, Any]] = []

    class Threads:
        async def get(self, _thread_id: str) -> dict[str, Any]:
            await wave.asyncio.sleep(0.03)
            return {"status": "busy"}

    class Runs:
        async def list(self, _thread_id: str, *, limit: int) -> list[Any]:
            assert limit == 1000
            await wave.asyncio.sleep(60)
            return []

    def get_client(**kwargs: Any) -> SimpleNamespace:
        client_calls.append(kwargs)
        return SimpleNamespace(threads=Threads(), runs=Runs())

    monkeypatch.setenv("LANGGRAPH_URL", "https://langgraph.invalid")
    monkeypatch.setattr(langgraph_sdk, "get_client", get_client)
    started = time.perf_counter()

    with pytest.raises(wave.WaveOpsError, match="request timed out"):
        wave.langgraph_snapshot("thread", deadline=time.monotonic() + 0.05)

    assert time.perf_counter() - started < 0.5
    assert client_calls[0]["url"] == "https://langgraph.invalid"
    assert 0 < client_calls[0]["timeout"] <= 0.05


@pytest.mark.parametrize("interval", [0, -1])
def test_watch_rejects_non_positive_interval_before_snapshot(
    monkeypatch: pytest.MonkeyPatch, interval: float
) -> None:
    monkeypatch.setattr(
        wave,
        "live_snapshot",
        lambda *_args, **_kwargs: pytest.fail("snapshot should not run"),
    )

    with pytest.raises(wave.WaveOpsError, match="interval greater than zero"):
        wave.cmd_watch(_watch_args(interval=interval))


@pytest.mark.parametrize(
    ("target", "connection_name", "expected"),
    [
        ("linear", None, "Linear comments pagination exceeded 2 pages"),
        ("timeline", None, "GitHub timelineItems pagination exceeded 2 pages"),
        ("connection", "labels", "GitHub labels pagination exceeded 2 pages"),
        ("connection", "reviewThreads", "GitHub reviewThreads pagination exceeded 2 pages"),
    ],
)
def test_pagination_guards_raise_named_errors(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    connection_name: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(wave, "MAX_PAGINATION_PAGES", 2)

    if target == "linear":
        monkeypatch.setattr(
            wave,
            "_linear_graphql",
            lambda *_args, **_kwargs: {
                "viewer": {},
                "issue": {
                    "comments": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                },
            },
        )
    else:
        name = connection_name or "timelineItems"
        monkeypatch.setattr(
            wave,
            "gh_graphql",
            lambda *_args, **_kwargs: {
                "repository": {
                    "defaultBranchRef": {"name": "main"},
                    "pullRequest": {
                        "headRefOid": "head",
                        name: {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                        },
                    },
                }
            },
        )

    def invoke() -> Any:
        if target == "linear":
            return wave.linear_snapshot("issue")
        if target == "timeline":
            return wave.github_pr_snapshot("owner/repo", 7)
        assert connection_name is not None
        return wave._paginate_pr_connection("query", connection_name, "owner", "repo", 7, "head")

    with pytest.raises(wave.WaveOpsError, match=expected):
        invoke()


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
        lambda _issue, **_kwargs: {
            "viewer": {"id": "session"},
            "issue": {"comments": {"nodes": []}},
        },
    )
    monkeypatch.setattr(
        wave,
        "langgraph_snapshot",
        lambda _thread, **_kwargs: {"thread": {"metadata": {}}, "runs": []},
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
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args, **_kwargs: next(snapshots))
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
        lambda _issue, **_kwargs: {
            "viewer": {"id": "session"},
            "issue": {"comments": {"nodes": []}},
        },
    )
    monkeypatch.setattr(
        wave,
        "langgraph_snapshot",
        lambda _thread, **_kwargs: {"thread": {"metadata": {"pr_number": 53}}, "runs": []},
    )

    def github_snapshot(_repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
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
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args, **_kwargs: next(snapshots))
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
    monkeypatch.setattr(wave, "live_snapshot", lambda *_args, **_kwargs: next(snapshots))
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

    def live_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
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


def test_bounded_timeout_raises_dedicated_poll_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wave.time, "monotonic", lambda: 10.0)

    with pytest.raises(wave._PollDeadlineError):
        wave._bounded_timeout(1.0, 9.0, "operation")


def test_wall_clock_deadline_restores_elapsed_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = iter([10.0, 10.0, 12.0])
    timer_calls: list[tuple[float, float]] = []
    handlers: list[Any] = []

    monkeypatch.setattr(wave.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(wave.signal, "getsignal", lambda _signal: "previous")
    monkeypatch.setattr(
        wave.signal,
        "signal",
        lambda _signal, handler: handlers.append(handler),
    )

    def setitimer(_which: int, delay: float, interval: float = 0.0) -> tuple[float, float]:
        timer_calls.append((delay, interval))
        return (1.0, 0.0) if len(timer_calls) == 1 else (0.0, 0.0)

    monkeypatch.setattr(wave.signal, "setitimer", setitimer)

    with wave._wall_clock_deadline(20.0, "poll"):
        pass

    assert handlers[-1] == "previous"
    assert timer_calls[-1] == pytest.approx((1e-6, 0.0))


def test_wall_clock_deadline_preserves_periodic_timer_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = iter([10.0, 10.0, 14.5])
    timer_calls: list[tuple[float, float]] = []

    monkeypatch.setattr(wave.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(wave.signal, "getsignal", lambda _signal: "previous")
    monkeypatch.setattr(wave.signal, "signal", lambda _signal, _handler: None)

    def setitimer(_which: int, delay: float, interval: float = 0.0) -> tuple[float, float]:
        timer_calls.append((delay, interval))
        return (1.0, 2.0) if len(timer_calls) == 1 else (0.0, 0.0)

    monkeypatch.setattr(wave.signal, "setitimer", setitimer)

    with wave._wall_clock_deadline(20.0, "poll"):
        pass

    assert timer_calls[-1] == pytest.approx((0.5, 2.0))


def test_monitor_recovery_reraises_poll_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = fixture("pr-44-queue-stall.json")
    monkeypatch.setattr(
        wave,
        "apply_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(wave._PollDeadlineError("deadline")),
    )

    with pytest.raises(wave._PollDeadlineError, match="deadline"):
        wave.monitor_recovery(
            snapshot,
            apply=True,
            refresh=lambda: snapshot,
            post_log=lambda _body: None,
        )


@pytest.mark.skipif(
    not all(hasattr(wave.signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer")),
    reason="POSIX setitimer is unavailable",
)
def test_watch_apply_recovery_deadline_uses_outer_unhandled_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = fixture("pr-44-queue-stall.json")
    snapshot = {
        "linear": {
            "viewer": {"id": "session"},
            "issue": {"comments": {"nodes": []}},
        },
        "langgraph": {
            "thread": {"metadata": recovery["thread_metadata"]},
            "runs": [],
        },
        "pr": recovery["pr"],
        "pr_number": recovery["pr_number"],
        "observed_at": "2026-01-01T00:00:00+00:00",
    }
    snapshots = 0
    blocked_call_finished = False
    emitted: list[dict[str, Any]] = []

    def blocking_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal snapshots, blocked_call_finished
        snapshots += 1
        if snapshots <= 2:
            return deepcopy(snapshot)
        time.sleep(0.8)
        blocked_call_finished = True
        return deepcopy(snapshot)

    monkeypatch.setattr(wave, "live_snapshot", blocking_snapshot)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))
    started = time.perf_counter()

    assert wave.cmd_watch(_watch_args(apply=True)) == 0

    elapsed = time.perf_counter() - started
    assert not blocked_call_finished
    assert elapsed < 0.7
    assert emitted == [
        {
            "wake_node": "unhandled_condition",
            "summary": "wave monitor poll failed: Wave monitor poll exceeded the poll deadline",
            "evidence": {"issue_id": "issue", "thread_id": "thread"},
        }
    ]


@pytest.mark.skipif(
    not all(hasattr(wave.signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer")),
    reason="POSIX setitimer is unavailable",
)
def test_watch_interrupts_blocking_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked_call_finished = False

    def blocking_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal blocked_call_finished
        time.sleep(0.8)
        blocked_call_finished = True
        return {}

    monkeypatch.setattr(wave, "live_snapshot", blocking_snapshot)
    started = time.perf_counter()

    with pytest.raises(
        wave.WaveOpsError,
        match="wave monitor baseline failed: Wave monitor baseline exceeded the poll deadline",
    ):
        wave.cmd_watch(_watch_args())

    elapsed = time.perf_counter() - started
    assert not blocked_call_finished
    assert elapsed < 0.7


STATUS_ISSUE_IDS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
)


def _status_ticket(identifier: str = "OSWE-1", index: int = 0) -> dict[str, str]:
    return {
        "identifier": identifier,
        "issue_id": STATUS_ISSUE_IDS[index],
        "thread_id": f"thread-{index}",
    }


def _status_snapshot(
    status: str | None = None,
    *,
    created_at: str = "2026-07-27T00:00:00Z",
    updated_at: str | None = "2026-07-27T01:00:00Z",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = None
    if status is not None:
        plan = {"value": {"status": status}, "updated_at": updated_at}
    return {
        "thread": {
            "status": "busy",
            "created_at": created_at,
            "updated_at": "2026-07-27T09:00:00Z",
            "metadata": metadata if metadata is not None else {"plan_status": status},
        },
        "plan": plan,
        "errors": [],
    }


@pytest.mark.parametrize(
    "payload,match",
    [
        ({}, "non-empty JSON list"),
        ([], "non-empty JSON list"),
        (["OSWE-1"], "row 0 must be an object"),
        ([{"identifier": "", "issue_id": "issue"}], "malformed identifier"),
        ([{"identifier": "OSWE-1", "issue_id": "  "}], "malformed issue_id"),
        (
            [{"identifier": "OSWE-1", "issue_id": "issue", "thread_id": ""}],
            "malformed thread_id",
        ),
    ],
)
def test_status_ticket_validation_keeps_only_required_shape_checks(
    tmp_path: Path, payload: Any, match: str
) -> None:
    tickets = tmp_path / "tickets.json"
    tickets.write_text(json.dumps(payload))

    with pytest.raises(wave.WaveOpsError, match=match):
        wave.load_status_tickets(tickets)


def test_status_ticket_normalizes_issue_before_deriving_thread(tmp_path: Path) -> None:
    tickets = tmp_path / "tickets.json"
    tickets.write_text(
        json.dumps(
            [
                {
                    "identifier": " odd identifier ",
                    "issue_id": "  NOT-A-UUID  ",
                    "extra": True,
                },
                {
                    "identifier": "odd identifier",
                    "issue_id": "not-a-uuid",
                    "thread_id": "  Custom-Thread-ID  ",
                },
            ]
        )
    )

    assert wave.load_status_tickets(tickets) == [
        {
            "identifier": "ODD IDENTIFIER",
            "issue_id": "not-a-uuid",
            "thread_id": wave.derive_linear_thread_id("not-a-uuid"),
        },
        {
            "identifier": "ODD IDENTIFIER",
            "issue_id": "not-a-uuid",
            "thread_id": "Custom-Thread-ID",
        },
    ]
    args = wave.parser().parse_args(
        ["status-sweep", "--repo", "owner/repo", "--tickets", str(tickets)]
    )
    assert args.func is wave.cmd_status_sweep
    assert args.divergence_minutes == 15


def test_github_pr_list_is_exactly_one_repository_wide_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("GH_TOKEN", "dummy")

    def run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(stdout="[]")

    monkeypatch.setattr(wave, "_run", run)

    assert wave.github_pr_list("owner/repo") == []
    assert len(calls) == 1
    assert calls[0][:8] == [
        "gh",
        "pr",
        "list",
        "--repo",
        "owner/repo",
        "--state",
        "all",
        "--limit",
    ]
    assert calls[0][8] == "1000"


def test_github_pr_list_defers_per_item_shape_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "dummy")
    monkeypatch.setattr(wave, "_run", lambda *_args, **_kwargs: SimpleNamespace(stdout="[null]"))

    prs = wave.github_pr_list("owner/repo")

    assert prs == [None]
    with pytest.raises(AttributeError):
        wave.match_status_pr("OSWE-1", {}, prs)


def test_langgraph_sweep_reads_thread_and_plan_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langgraph_sdk

    calls: list[tuple[Any, ...]] = []

    class Threads:
        async def get(self, thread_id: str) -> dict[str, Any]:
            calls.append(("thread", thread_id))
            return {"status": "idle"}

    class Store:
        async def get_item(self, namespace: list[str], key: str) -> dict[str, Any]:
            calls.append(("plan", namespace, key))
            return {"value": {"status": "ready"}}

    monkeypatch.setenv("LANGGRAPH_URL", "https://langgraph.invalid")
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda **_kwargs: SimpleNamespace(threads=Threads(), store=Store()),
    )

    snapshot = wave.langgraph_sweep_snapshot("thread")

    assert snapshot["errors"] == []
    assert calls == [
        ("thread", "thread"),
        ("plan", ["plan", "content"], "thread"),
    ]


def test_status_pr_matching_prefers_metadata_then_requires_one_closing_line() -> None:
    prs = [
        {"number": 1, "body": "Closes OSWE-1"},
        {"number": 2, "body": "prefix Closes OSWE-1"},
    ]

    assert wave.match_status_pr("OSWE-1", {"pr_number": "2"}, prs) == (prs[1], [])
    assert wave.match_status_pr("OSWE-1", {}, prs) == (prs[0], [])
    assert wave.match_status_pr("OSWE-1", {}, [prs[0], {**prs[0], "number": 3}])[0] is None
    assert (
        "multiple PRs"
        in wave.match_status_pr("OSWE-1", {}, [prs[0], {**prs[0], "number": 3}])[1][0]
    )
    assert wave.match_status_pr("OSWE-1", {"pr_number": 99}, prs)[0] is None


@pytest.mark.parametrize(
    "pr,status,expected",
    [
        (None, None, "dispatched"),
        (None, "shared", "dispatched"),
        (None, "ready", "planned"),
        (None, "revising", "planned"),
        (None, "approved", "approved"),
        (
            {"number": 1, "state": "OPEN", "createdAt": "2026-07-27T02:00:00Z"},
            "approved",
            "pr-open",
        ),
        (
            {"number": 1, "state": "CLOSED", "closedAt": "2026-07-27T03:00:00Z"},
            "approved",
            "closed",
        ),
        (
            {"number": 1, "state": "MERGED", "mergedAt": "2026-07-27T04:00:00Z"},
            "approved",
            "merged",
        ),
    ],
)
def test_status_lifecycle_precedence_and_plan_store_timestamp(
    pr: dict[str, Any] | None, status: str | None, expected: str
) -> None:
    snapshot = _status_snapshot(
        status, metadata={"plan_status": status, "pr_number": 1} if pr else None
    )

    row = wave.classify_status_ticket(_status_ticket(), snapshot, [pr] if pr else [])

    assert row["lifecycle_stage"] == expected
    if expected in {"planned", "approved"}:
        assert row["stage_at"] == "2026-07-27T01:00:00Z"
    assert row["stage_at"] != "2026-07-27T09:00:00Z"


def test_status_classification_reports_ambiguous_and_missing_evidence() -> None:
    snapshot = _status_snapshot("ready", updated_at=None, metadata={"plan_status": "approved"})

    row = wave.classify_status_ticket(_status_ticket(), snapshot, [])

    assert row["lifecycle_stage"] == "approved"
    assert row["stage_at"] is None
    assert any("different lifecycle stages" in error for error in row["errors"])
    assert any("plan-store item" in error for error in row["errors"])


def test_status_sweep_calls_once_per_boundary_and_emits_input_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tickets = tmp_path / "tickets.json"
    tickets.write_text(
        json.dumps(
            [
                {**_status_ticket("OSWE-1", 0)},
                {**_status_ticket("OSWE-2", 1)},
            ]
        )
    )
    github_calls: list[str] = []
    langgraph_calls: list[str] = []
    emitted: list[tuple[dict[str, Any], bool]] = []
    monkeypatch.setattr(
        wave,
        "github_pr_list",
        lambda repo: github_calls.append(repo) or [],
    )

    def snapshot(thread_id: str) -> dict[str, Any]:
        langgraph_calls.append(thread_id)
        return _status_snapshot()

    monkeypatch.setattr(wave, "langgraph_sweep_snapshot", snapshot)
    monkeypatch.setattr(
        wave, "emit", lambda payload, pretty=True: emitted.append((payload, pretty))
    )

    result = wave.cmd_status_sweep(
        SimpleNamespace(repo="owner/repo", tickets=str(tickets), divergence_minutes=15)
    )

    assert result == 0
    assert github_calls == ["owner/repo"]
    assert langgraph_calls == ["thread-0", "thread-1"]
    assert [item[0]["identifier"] for item in emitted] == ["OSWE-1", "OSWE-2"]
    assert all(pretty is False for _, pretty in emitted)


@pytest.mark.parametrize(
    "age_seconds,expected",
    [(899, False), (900, False), (901, True)],
)
def test_sibling_divergence_threshold_is_strict(age_seconds: int, expected: bool) -> None:
    now = wave.datetime(2026, 7, 27, 0, 15, 1, tzinfo=wave.UTC)
    leader_at = now - timedelta(seconds=age_seconds)
    rows = [
        {
            **_status_ticket("OSWE-1", 0),
            "lifecycle_stage": "approved",
            "stage_at": wave._format_timestamp(leader_at),
            "sibling_divergence": False,
        },
        {
            **_status_ticket("OSWE-2", 1),
            "lifecycle_stage": "planned",
            "stage_at": "2026-07-27T00:00:00Z",
            "sibling_divergence": False,
        },
    ]

    wave.add_sibling_divergence(rows, 15, now=now)

    assert rows[1]["sibling_divergence"] is expected


def test_sibling_divergence_terminal_peers_and_missing_timestamps() -> None:
    now = wave.datetime(2026, 7, 27, 1, 0, tzinfo=wave.UTC)
    rows = [
        {
            **_status_ticket("OSWE-1", 0),
            "lifecycle_stage": "merged",
            "stage_at": "2026-07-27T00:00:00Z",
            "sibling_divergence": False,
        },
        {
            **_status_ticket("OSWE-2", 1),
            "lifecycle_stage": "closed",
            "stage_at": None,
            "sibling_divergence": False,
        },
    ]

    wave.add_sibling_divergence(rows, 15, now=now)

    assert not rows[0]["sibling_divergence"]
    assert not rows[1]["sibling_divergence"]

    rows[0]["lifecycle_stage"] = "approved"
    rows[1]["lifecycle_stage"] = "planned"
    rows[0]["stage_at"] = None
    wave.add_sibling_divergence(rows, 15, now=now)
    assert not rows[1]["sibling_divergence"]
    assert "no usable stage timestamp" in rows[1]["divergence_diagnostics"][0]


def test_status_pr_matching_trusts_number_despite_repository_metadata() -> None:
    prs = [{"number": 7, "body": "Closes OSWE-1"}]
    metadata = {
        "pr_number": 7,
        "pr_owner": "other",
        "pr_repo": "elsewhere",
        "repo_owner": "conflicting-owner",
        "repo_name": "conflicting-repo",
    }

    assert wave.match_status_pr("OSWE-1", metadata, prs) == (prs[0], [])


@pytest.mark.parametrize(
    "metadata,prs,error_fragment",
    [
        ({"plan_status": "approved", "pr_number": "bad"}, [], "malformed"),
        ({"plan_status": "approved", "pr_number": 99}, [], "not found"),
        (
            {"plan_status": "approved"},
            [
                {"number": 1, "body": "Closes OSWE-1"},
                {"number": 2, "body": "Closes OSWE-1"},
            ],
            "multiple PRs",
        ),
    ],
)
def test_bad_pr_evidence_is_indeterminate_and_never_diverges(
    metadata: dict[str, Any], prs: list[dict[str, Any]], error_fragment: str
) -> None:
    row = wave.classify_status_ticket(
        _status_ticket(),
        _status_snapshot("approved", metadata=metadata),
        prs,
    )
    sibling = {
        **_status_ticket("OSWE-2", 1),
        "lifecycle_stage": "merged",
        "stage_at": "2026-07-27T00:00:00Z",
        "sibling_divergence": False,
    }

    wave.add_sibling_divergence(
        [row, sibling], 15, now=wave.datetime(2026, 7, 27, 1, 0, tzinfo=wave.UTC)
    )

    assert row["thread_status"] == "busy"
    assert row["lifecycle_stage"] is None
    assert row["stage_at"] is None
    assert row["sibling_divergence"] is False
    assert any(error_fragment in error for error in row["errors"])


def test_status_sweep_inventory_failure_emits_every_ticket_indeterminate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tickets = tmp_path / "tickets.json"
    tickets.write_text(json.dumps([_status_ticket("OSWE-1", 0), _status_ticket("OSWE-2", 1)]))
    calls: list[str] = []
    emitted: list[dict[str, Any]] = []

    def fail_inventory(_repo: str) -> list[dict[str, Any]]:
        raise wave.WaveOpsError("inventory unavailable")

    def snapshot(thread_id: str) -> dict[str, Any]:
        calls.append(thread_id)
        if thread_id == "thread-1":
            return {"thread": None, "plan": None, "errors": ["thread failed"]}
        return _status_snapshot("approved")

    monkeypatch.setattr(wave, "github_pr_list", fail_inventory)
    monkeypatch.setattr(wave, "langgraph_sweep_snapshot", snapshot)
    monkeypatch.setattr(wave, "emit", lambda payload, **_kwargs: emitted.append(payload))

    result = wave.cmd_status_sweep(
        SimpleNamespace(repo="owner/repo", tickets=str(tickets), divergence_minutes=15)
    )

    assert result == 2
    assert calls == ["thread-0", "thread-1"]
    assert [row["identifier"] for row in emitted] == ["OSWE-1", "OSWE-2"]
    assert [row["thread_status"] for row in emitted] == ["busy", None]
    assert all(row["lifecycle_stage"] is None for row in emitted)
    assert all(row["stage_at"] is None for row in emitted)
    assert all(row["sibling_divergence"] is False for row in emitted)
    assert all(
        "GitHub PR inventory read failed: inventory unavailable" in row["errors"] for row in emitted
    )


def test_status_sweep_skill_documents_only_deterministic_contract() -> None:
    skill = (SKILL / "SKILL.md").read_text()

    for phrase in (
        "status-sweep",
        "`identifier`",
        "`issue_id`",
        "`thread_id`",
        "one repository-wide `gh pr list --state all` read",
        "`sibling_divergence`",
        "defaults to 15",
        "every operator contact",
        "deadline",
    ):
        assert phrase in skill
