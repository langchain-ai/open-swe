"""Unit tests for the publish_review rendering and orchestration helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.reviewer_findings import Finding, new_finding
from agent.reviewer_publish import (
    render_inline_comment_body,
    render_inline_comment_payload,
    render_review_body,
    resolve_review_thread,
)


def _f(**overrides: Any) -> Finding:
    base = new_finding(
        severity="high",
        category="correctness",
        file="src/foo.py",
        start_line=10,
        end_line=10,
        description="boom",
        sha="abc",
    )
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_render_inline_comment_body_without_suggestion() -> None:
    body = render_inline_comment_body(_f(description="just text"))
    assert body == "just text"


def test_render_inline_comment_body_with_suggestion_appends_block() -> None:
    body = render_inline_comment_body(
        _f(description="needs fix", suggestion="x = 1\nx += 1"),
    )
    assert "needs fix" in body
    assert "```suggestion" in body
    assert "x = 1\nx += 1" in body


def test_render_inline_comment_payload_single_line() -> None:
    payload = render_inline_comment_payload(_f(start_line=10, end_line=10))
    assert payload == {
        "path": "src/foo.py",
        "line": 10,
        "side": "RIGHT",
        "body": "boom",
    }


def test_render_inline_comment_payload_multi_line_uses_start_fields() -> None:
    payload = render_inline_comment_payload(_f(start_line=8, end_line=12))
    assert payload is not None
    assert payload["start_line"] == 8
    assert payload["start_side"] == "RIGHT"
    assert payload["line"] == 12


def test_render_inline_comment_payload_returns_none_for_file_level() -> None:
    payload = render_inline_comment_payload(_f(start_line=None, end_line=None))
    assert payload is None


def test_render_review_body_includes_summary_and_marker() -> None:
    body = render_review_body(
        pr_number=123,
        surfaced_count=2,
        total_open_count=3,
        severity_threshold="medium",
        summary="LGTM with two notes",
    )
    assert "LGTM with two notes" in body
    assert "<!-- open-swe-reviewer pr=123 -->" in body
    assert "1 lower-severity finding hidden" in body


def test_render_review_body_surfaces_no_findings_message() -> None:
    body = render_review_body(
        pr_number=99,
        surfaced_count=0,
        total_open_count=0,
        severity_threshold="medium",
        summary=None,
    )
    assert "No issues at or above" in body


@pytest.mark.asyncio
async def test_resolve_review_thread_returns_true_on_success() -> None:
    response = MagicMock()
    response.json.return_value = {
        "data": {"resolveReviewThread": {"thread": {"id": "T_1", "isResolved": True}}}
    }
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        ok = await resolve_review_thread(thread_node_id="T_1", token="t")
    assert ok is True


@pytest.mark.asyncio
async def test_resolve_review_thread_returns_false_on_graphql_errors() -> None:
    response = MagicMock()
    response.json.return_value = {"errors": [{"message": "no perms"}]}
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        ok = await resolve_review_thread(thread_node_id="T_1", token="t")
    assert ok is False
