"""Unit tests for the add_finding / update_finding / list_findings tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from agent.tools.add_finding import add_finding
from agent.tools.list_findings import list_findings
from agent.tools.update_finding import update_finding


def _config(**configurable_overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "configurable": {
            "thread_id": "tid-1",
            "head_sha": "sha-head",
            "diff_text": "",
            "diff_line_set": {"foo.py": list(range(10, 41))},
        },
        "metadata": {},
    }
    base["configurable"].update(configurable_overrides)
    return base


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
    assert persisted["end_line"] == 11
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


def test_add_finding_always_collapses_to_single_line() -> None:
    """Multi-line ranges are always collapsed to ``start_line``."""
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
            description="anchor on start_line",
            start_line=15,
            end_line=19,
        )

    assert result["success"] is True
    assert captured[0]["start_line"] == 15
    assert captured[0]["end_line"] == 15


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
