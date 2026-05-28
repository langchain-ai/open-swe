"""Unit tests for the Finding schema + thread-metadata helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.reviewer_findings import (
    SEVERITY_ORDER,
    Finding,
    append_finding,
    filter_findings_for_publish,
    list_findings,
    new_finding,
    new_finding_id,
    replace_findings,
    set_reviewer_thread_metadata,
    update_finding_fields,
)


def _f(**overrides: Any) -> Finding:
    base = new_finding(
        severity="high",
        confidence="high",
        category="correctness",
        file="foo.py",
        start_line=10,
        end_line=10,
        description="boom",
        sha="abc123",
    )
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_new_finding_id_format() -> None:
    fid = new_finding_id()
    assert fid.startswith("f_")
    assert len(fid) == len("f_") + 10


def test_new_finding_defaults() -> None:
    finding = _f()
    assert finding["status"] == "open"
    assert finding["side"] == "RIGHT"
    assert finding["first_seen_sha"] == "abc123"
    assert finding["last_confirmed_sha"] == "abc123"
    assert finding["github_review_id"] is None
    assert finding["github_review_comment_id"] is None
    assert finding["github_review_comment_ids"] == []
    assert finding["github_review_thread_id"] is None
    assert finding["github_review_thread_ids"] == []
    assert finding["github_review_run_id"] is None
    assert finding["github_thread_resolved"] is False
    assert finding["github_resolved_thread_ids"] == []
    assert finding["last_human_reply_at"] is None
    assert finding["resolution_note"] is None
    assert finding["title"] is None
    assert finding["suggestion"] is None


def test_new_finding_normalizes_title() -> None:
    finding = new_finding(
        severity="high",
        confidence="high",
        category="correctness",
        file="foo.py",
        start_line=10,
        end_line=10,
        description="boom",
        sha="abc123",
        title="  Avoid lost updates\nwhen retrying  ",
    )
    assert finding["title"] == "Avoid lost updates when retrying"


def test_severity_order_monotonic() -> None:
    assert (
        SEVERITY_ORDER["low"]
        < SEVERITY_ORDER["medium"]
        < SEVERITY_ORDER["high"]
        < SEVERITY_ORDER["critical"]
    )


def test_filter_findings_for_publish_drops_below_threshold_and_resolved() -> None:
    findings = [
        _f(id="f_a", severity="high", file="a.py", start_line=1, end_line=1),
        _f(id="f_b", severity="low", file="b.py"),
        _f(id="f_c", severity="critical", file="c.py", start_line=2, end_line=2),
        _f(id="f_d", severity="high", file="d.py", status="resolved"),
    ]
    surfaced = filter_findings_for_publish(findings, severity_threshold="medium", cap=10)
    assert [f["id"] for f in surfaced] == ["f_c", "f_a"]


def test_filter_findings_for_publish_caps_results() -> None:
    findings = [_f(id=f"f_{i}", severity="high", file=f"f{i}.py") for i in range(20)]
    surfaced = filter_findings_for_publish(findings, severity_threshold="medium", cap=5)
    assert len(surfaced) == 5


@pytest.mark.asyncio
async def test_list_findings_returns_empty_on_missing_metadata() -> None:
    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {"metadata": {}}
    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        findings = await list_findings("tid")
    assert findings == []


@pytest.mark.asyncio
async def test_list_findings_coerces_bad_entries() -> None:
    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {
        "metadata": {
            "findings": [
                {"id": "f_ok", "severity": "high", "file": "x.py"},
                {"missing_id": True},
                "not-a-dict",
            ]
        }
    }
    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        findings = await list_findings("tid")
    assert [f["id"] for f in findings] == ["f_ok"]


@pytest.mark.asyncio
async def test_replace_findings_calls_threads_update() -> None:
    fake_client = AsyncMock()
    findings = [_f(id="f_x")]
    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        await replace_findings("tid", findings)
    fake_client.threads.update.assert_awaited_once_with(
        thread_id="tid", metadata={"findings": findings}
    )


@pytest.mark.asyncio
async def test_append_finding_appends_to_existing_list() -> None:
    existing = _f(id="f_a")
    new = _f(id="f_b")

    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {"metadata": {"findings": [existing]}}

    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        result = await append_finding("tid", new)

    assert result["id"] == "f_b"
    args = fake_client.threads.update.await_args
    persisted = args.kwargs["metadata"]["findings"]
    assert [f["id"] for f in persisted] == ["f_a", "f_b"]


@pytest.mark.asyncio
async def test_update_finding_fields_mutates_only_target() -> None:
    a = _f(id="f_a", description="orig-a")
    b = _f(id="f_b", description="orig-b")

    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {"metadata": {"findings": [a, b]}}

    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        updated = await update_finding_fields("tid", "f_b", {"status": "resolved"})

    assert updated is not None
    assert updated["status"] == "resolved"
    persisted = fake_client.threads.update.await_args.kwargs["metadata"]["findings"]
    by_id = {f["id"]: f for f in persisted}
    assert by_id["f_a"]["status"] == "open"
    assert by_id["f_b"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_update_finding_fields_returns_none_for_unknown_id() -> None:
    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {"metadata": {"findings": [_f(id="f_a")]}}
    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        result = await update_finding_fields("tid", "f_missing", {"status": "resolved"})
    assert result is None
    fake_client.threads.update.assert_not_called()


@pytest.mark.asyncio
async def test_set_reviewer_thread_metadata_includes_kind() -> None:
    fake_client = AsyncMock()
    with patch("agent.reviewer_findings.get_client", return_value=fake_client):
        await set_reviewer_thread_metadata("tid", watch=True, last_reviewed_sha="sha")
    metadata = fake_client.threads.update.await_args.kwargs["metadata"]
    assert metadata["kind"] == "reviewer"
    assert metadata["watch"] is True
    assert metadata["last_reviewed_sha"] == "sha"
    assert "pr" not in metadata
    assert "findings" not in metadata
