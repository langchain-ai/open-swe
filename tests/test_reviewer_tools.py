"""Unit tests for the add_finding / update_finding / list_findings tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from agent.tools.add_finding import add_finding
from agent.tools.list_findings import list_findings
from agent.tools.resolve_finding_thread import resolve_finding_thread
from agent.tools.update_finding import update_finding


def _config(**configurable_overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "configurable": {
            "thread_id": "tid-1",
            "head_sha": "sha-head",
            "diff_text": "",
            "diff_line_set": {
                "foo.py": {"RIGHT": set(range(10, 41)), "LEFT": set()},
            },
        },
        "metadata": {},
    }
    base["configurable"].update(configurable_overrides)
    return base


def _existing_finding(**overrides: Any) -> dict[str, Any]:
    finding: dict[str, Any] = {"id": "f_a", "status": "open"}
    finding.update(overrides)
    return finding


def test_add_finding_rejects_invalid_severity() -> None:
    with patch("agent.tools.add_finding.get_config", return_value=_config()):
        result = add_finding(
            severity="trivial",
            confidence="high",
            category="x",
            file="foo.py",
            description="d",
            start_line=11,
            end_line=11,
        )
    assert result["success"] is False
    assert "severity" in result["error"].lower()


def test_add_finding_rejects_out_of_diff_lines() -> None:
    with patch("agent.tools.add_finding.get_config", return_value=_config()):
        result = add_finding(
            severity="high",
            confidence="high",
            category="correctness",
            file="foo.py",
            description="d",
            start_line=99,
            end_line=99,
        )
    assert result["success"] is False
    assert "not part of the PR diff" in result["error"]


def test_add_finding_accepts_left_side_anchor_on_old_line() -> None:
    """A finding on a deleted (LEFT-side) line must validate against the
    old-side line set, not the new-side. With only RIGHT lines in 10..40,
    a LEFT anchor at the same number should still pass when the line is in
    the old-side set."""
    config = {
        "configurable": {
            "thread_id": "tid-1",
            "head_sha": "sha-head",
            "diff_text": "",
            "diff_line_set": {
                "foo.py": {"RIGHT": {10, 11, 12}, "LEFT": {50, 51}},
            },
        },
        "metadata": {},
    }
    with (
        patch("agent.tools.add_finding.get_config", return_value=config),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.add_finding.append_finding", new_callable=AsyncMock),
    ):
        result = add_finding(
            severity="high",
            confidence="high",
            category="correctness",
            file="foo.py",
            description="deleted call to releaseResources()",
            start_line=51,
            end_line=51,
            side="LEFT",
        )
    assert result["success"] is True


def test_add_finding_rejects_left_anchor_outside_old_side_set() -> None:
    """A LEFT anchor on a line that's not in the old-side hunk must be
    rejected — same guard, just on the correct side."""
    config = {
        "configurable": {
            "thread_id": "tid-1",
            "head_sha": "sha-head",
            "diff_text": "",
            "diff_line_set": {
                "foo.py": {"RIGHT": {10, 11, 12}, "LEFT": {50, 51}},
            },
        },
        "metadata": {},
    }
    with patch("agent.tools.add_finding.get_config", return_value=config):
        result = add_finding(
            severity="high",
            confidence="high",
            category="correctness",
            file="foo.py",
            description="d",
            start_line=99,
            end_line=99,
            side="LEFT",
        )
    assert result["success"] is False
    assert "not part of the PR diff" in result["error"]


def test_add_finding_rejects_invalid_confidence() -> None:
    with patch("agent.tools.add_finding.get_config", return_value=_config()):
        result = add_finding(
            severity="high",
            confidence="certain",
            category="correctness",
            file="foo.py",
            description="d",
            start_line=11,
            end_line=11,
        )
    assert result["success"] is False
    assert "confidence" in result["error"].lower()


def test_add_finding_persists_to_thread_metadata() -> None:
    captured: list[Any] = []

    async def fake_append(thread_id: str, finding: Any) -> Any:
        captured.append((thread_id, finding))
        return finding

    with (
        patch("agent.tools.add_finding.get_config", return_value=_config()),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.add_finding.append_finding", side_effect=fake_append),
    ):
        result = add_finding(
            severity="medium",
            confidence="high",
            category="style",
            file="foo.py",
            description="rename",
            start_line=11,
            end_line=12,
            suggestion="renamed = 1",
        )

    assert result["success"] is True
    assert "finding_id" in result
    persisted_thread, persisted = captured[0]
    assert persisted_thread == "tid-1"
    assert persisted["file"] == "foo.py"
    assert persisted["start_line"] == 11
    assert persisted["end_line"] == 12
    assert persisted["suggestion"] == "renamed = 1"
    assert persisted["status"] == "open"
    assert persisted["first_seen_sha"] == "sha-head"
    assert persisted["confidence"] == "high"


def test_add_finding_allows_file_level_with_no_lines() -> None:
    with (
        patch("agent.tools.add_finding.get_config", return_value=_config()),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.add_finding.append_finding",
            new_callable=AsyncMock,
            side_effect=lambda _t, f: f,
        ),
    ):
        result = add_finding(
            severity="low",
            confidence="medium",
            category="style",
            file="missing.py",
            description="file-level note",
        )
    assert result["success"] is True


def test_update_finding_rejects_invalid_status() -> None:
    with patch("agent.tools.update_finding.get_config", return_value=_config()):
        result = update_finding(finding_id="f_x", status="archived")
    assert result["success"] is False


def test_resolve_finding_thread_resolves_all_known_threads() -> None:
    finding = {
        "id": "f1",
        "status": "open",
        "github_review_thread_ids": ["THREAD_1", "THREAD_2"],
        "github_review_comment_ids": [11, 12],
    }
    update = AsyncMock(return_value={**finding, "status": "resolved"})
    resolve = AsyncMock(return_value=True)

    with (
        patch(
            "agent.tools.resolve_finding_thread.get_config",
            return_value=_config(repo={"owner": "o", "name": "r"}, pr_number=7),
        ),
        patch("agent.tools.resolve_finding_thread.get_github_token", return_value="token"),
        patch("agent.tools.resolve_finding_thread.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.resolve_finding_thread.get_finding", AsyncMock(return_value=finding)),
        patch("agent.tools.resolve_finding_thread.resolve_review_thread", resolve),
        patch("agent.tools.resolve_finding_thread.update_finding_fields", update),
    ):
        result = resolve_finding_thread("f1", status="resolved")

    assert result["success"] is True
    assert result["resolved_thread_count"] == 2
    assert [call.kwargs["thread_node_id"] for call in resolve.await_args_list] == [
        "THREAD_1",
        "THREAD_2",
    ]
    updates = update.await_args.args[2]
    assert updates["github_thread_resolved"] is True
    assert updates["github_resolved_thread_ids"] == ["THREAD_1", "THREAD_2"]


def test_update_finding_rejects_empty_update() -> None:
    with patch("agent.tools.update_finding.get_config", return_value=_config()):
        result = update_finding(finding_id="f_x")
    assert result["success"] is False
    assert "No fields" in result["error"]


def test_add_finding_drops_long_suggestion() -> None:
    captured: list[Any] = []

    async def fake_append(thread_id: str, finding: Any) -> Any:
        captured.append(finding)
        return finding

    long_suggestion = "\n".join(f"line_{i}" for i in range(6))
    with (
        patch("agent.tools.add_finding.get_config", return_value=_config()),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.add_finding.append_finding", side_effect=fake_append),
    ):
        result = add_finding(
            severity="medium",
            confidence="high",
            category="style",
            file="foo.py",
            description="rewrite",
            start_line=11,
            end_line=12,
            suggestion=long_suggestion,
        )

    assert result["success"] is True
    assert result.get("suggestion_dropped") is True
    assert "warning" in result
    assert captured[0]["suggestion"] is None


def test_add_finding_keeps_short_suggestion() -> None:
    captured: list[Any] = []

    async def fake_append(thread_id: str, finding: Any) -> Any:
        captured.append(finding)
        return finding

    short_suggestion = "a\nb\nc\nd"  # exactly 4 lines — at the cap
    with (
        patch("agent.tools.add_finding.get_config", return_value=_config()),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.add_finding.append_finding", side_effect=fake_append),
    ):
        result = add_finding(
            severity="medium",
            confidence="medium",
            category="style",
            file="foo.py",
            description="rename",
            start_line=11,
            end_line=12,
            suggestion=short_suggestion,
        )

    assert result["success"] is True
    assert "suggestion_dropped" not in result
    assert captured[0]["suggestion"] == short_suggestion


def test_add_finding_preserves_multi_line_range() -> None:
    """Multi-line ranges are preserved end-to-end (no collapse to start_line)."""
    captured: list[Any] = []

    async def fake_append(thread_id: str, finding: Any) -> Any:
        captured.append(finding)
        return finding

    with (
        patch("agent.tools.add_finding.get_config", return_value=_config()),
        patch("agent.tools.add_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.add_finding.append_finding", side_effect=fake_append),
    ):
        result = add_finding(
            severity="low",
            confidence="low",
            category="style",
            file="foo.py",
            description="span the relevant range",
            start_line=15,
            end_line=19,
        )

    assert result["success"] is True
    assert captured[0]["start_line"] == 15
    assert captured[0]["end_line"] == 19


def test_update_finding_rejects_long_suggestion_without_clobbering() -> None:
    """Over-cap suggestion alongside other fields: drop suggestion, keep the rest."""
    captured: list[Any] = []

    async def fake_update(thread_id: str, finding_id: str, updates: Any) -> Any:
        captured.append(updates)
        return {"id": finding_id, **updates}

    long_suggestion = "\n".join(f"line_{i}" for i in range(6))
    with (
        patch("agent.tools.update_finding.get_config", return_value=_config()),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding()]),
        ),
        patch("agent.tools.update_finding.update_finding_fields", side_effect=fake_update),
    ):
        result = update_finding(
            finding_id="f_a",
            description="updated description",
            suggestion=long_suggestion,
        )

    assert result["success"] is True
    assert result.get("suggestion_dropped") is True
    assert "suggestion" not in captured[0]
    assert captured[0]["description"] == "updated description"


def test_update_finding_long_suggestion_only_returns_failure() -> None:
    """Over-cap suggestion as the only field: fail outright rather than no-op."""
    long_suggestion = "\n".join(f"line_{i}" for i in range(6))
    with (
        patch("agent.tools.update_finding.get_config", return_value=_config()),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
    ):
        result = update_finding(finding_id="f_a", suggestion=long_suggestion)

    assert result["success"] is False
    assert result.get("suggestion_dropped") is True
    assert "cap" in result["error"]


def test_update_finding_empty_string_clears_suggestion() -> None:
    captured: list[Any] = []

    async def fake_update(thread_id: str, finding_id: str, updates: Any) -> Any:
        captured.append(updates)
        return {"id": finding_id, **updates}

    with (
        patch("agent.tools.update_finding.get_config", return_value=_config()),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding()]),
        ),
        patch("agent.tools.update_finding.update_finding_fields", side_effect=fake_update),
    ):
        result = update_finding(finding_id="f_a", suggestion="")

    assert result["success"] is True
    assert captured[0]["suggestion"] is None


def test_update_finding_passes_through_fields() -> None:
    captured: list[Any] = []

    async def fake_update(thread_id: str, finding_id: str, updates: Any) -> Any:
        captured.append((thread_id, finding_id, updates))
        return {"id": finding_id, **updates}

    with (
        patch("agent.tools.update_finding.get_config", return_value=_config()),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding()]),
        ),
        patch("agent.tools.update_finding.update_finding_fields", side_effect=fake_update),
    ):
        result = update_finding(
            finding_id="f_a",
            status="resolved",
            note="addressed by new commit",
        )

    assert result["success"] is True
    _t, fid, updates = captured[0]
    assert fid == "f_a"
    assert updates["status"] == "resolved"
    assert updates["last_update_note"] == "addressed by new commit"


def test_update_finding_resolves_github_thread_when_pr_context_available() -> None:
    cfg = _config(repo={"owner": "o", "name": "r"}, pr_number=7)
    with (
        patch("agent.tools.update_finding.get_config", return_value=cfg),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding(github_review_thread_id="THREAD_1")]),
        ),
        patch("agent.tools.resolve_finding_thread.get_config", return_value=cfg),
        patch("agent.tools.resolve_finding_thread.get_github_token", return_value="token"),
        patch("agent.tools.update_finding.update_finding_fields", AsyncMock()) as update,
        patch(
            "agent.tools.resolve_finding_thread._resolve_finding_thread_async",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "finding": {"id": "f_a", "status": "resolved", "github_thread_resolved": True},
                "resolved_thread_count": 1,
            },
        ) as resolve_async,
    ):
        result = update_finding(finding_id="f_a", status="resolved")

    assert result["success"] is True
    assert result["github_resolution"]["success"] is True
    assert result["finding"]["github_thread_resolved"] is True
    resolve_async.assert_awaited_once()
    update.assert_not_awaited()


def test_update_finding_leaves_open_when_github_resolution_fails() -> None:
    cfg = _config(repo={"owner": "o", "name": "r"}, pr_number=7)
    with (
        patch("agent.tools.update_finding.get_config", return_value=cfg),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding(github_review_thread_id="THREAD_1")]),
        ),
        patch("agent.tools.resolve_finding_thread.get_config", return_value=cfg),
        patch("agent.tools.resolve_finding_thread.get_github_token", return_value="token"),
        patch("agent.tools.update_finding.update_finding_fields", AsyncMock()) as update,
        patch(
            "agent.tools.resolve_finding_thread._resolve_finding_thread_async",
            new_callable=AsyncMock,
            return_value={
                "success": False,
                "error": "Could not resolve GitHub review thread id",
            },
        ) as resolve_async,
    ):
        result = update_finding(finding_id="f_a", status="resolved")

    assert result["success"] is False
    assert "left open" in result["error"]
    assert result["github_resolution"]["error"] == "Could not resolve GitHub review thread id"
    resolve_async.assert_awaited_once()
    update.assert_not_awaited()


def test_update_finding_resolves_hidden_finding_locally() -> None:
    captured: list[Any] = []

    async def fake_update(thread_id: str, finding_id: str, updates: Any) -> Any:
        captured.append((thread_id, finding_id, updates))
        return {"id": finding_id, **updates}

    cfg = _config(repo={"owner": "o", "name": "r"}, pr_number=7)
    with (
        patch("agent.tools.update_finding.get_config", return_value=cfg),
        patch("agent.tools.update_finding.get_thread_id_from_runtime", return_value="tid-1"),
        patch(
            "agent.tools.update_finding.list_findings",
            AsyncMock(return_value=[_existing_finding()]),
        ),
        patch("agent.tools.update_finding.update_finding_fields", side_effect=fake_update),
        patch(
            "agent.tools.resolve_finding_thread._resolve_finding_thread_async",
            new_callable=AsyncMock,
        ) as resolve_async,
    ):
        result = update_finding(finding_id="f_a", status="resolved")

    assert result["success"] is True
    _thread_id, _finding_id, updates = captured[0]
    assert updates["status"] == "resolved"
    resolve_async.assert_not_awaited()


def test_list_findings_filters_by_status() -> None:
    findings = [
        {"id": "f_a", "status": "open"},
        {"id": "f_b", "status": "resolved"},
        {"id": "f_c", "status": "open"},
    ]

    async def fake_list(_thread_id: str) -> list[Any]:
        return findings

    cfg = _config()
    with (
        patch("agent.tools.list_findings.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.list_findings.list_findings_async", side_effect=fake_list),
        patch("agent.tools.add_finding.get_config", return_value=cfg),
    ):
        result = list_findings(status_filter="open")

    assert result["count"] == 2
    assert [f["id"] for f in result["findings"]] == ["f_a", "f_c"]


def test_list_findings_returns_all_when_filter_omitted() -> None:
    findings = [{"id": "f_a", "status": "open"}, {"id": "f_b", "status": "resolved"}]

    async def fake_list(_thread_id: str) -> list[Any]:
        return findings

    with (
        patch("agent.tools.list_findings.get_thread_id_from_runtime", return_value="tid-1"),
        patch("agent.tools.list_findings.list_findings_async", side_effect=fake_list),
    ):
        result = list_findings()

    assert result["count"] == 2
