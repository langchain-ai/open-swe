from __future__ import annotations

import importlib
from unittest.mock import AsyncMock

import pytest

from agent.review.findings import ReviewerThreadMissingError

stackability_tool = importlib.import_module("agent.tools.set_stackability_review")


def _step(title: str, *, depends_on: str | None = None) -> dict[str, object]:
    return {
        "title": title,
        "purpose": f"Land {title}",
        "include": [f"src/{title}.py"],
        "exclude_or_defer": [],
        "depends_on": depends_on,
        "independently_testable_because": "Its unit tests exercise the isolated behavior.",
        "suggested_checks": [f"pytest tests/{title}"],
    }


def _arguments(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "verdict": "split_recommended",
        "confidence": "high",
        "rationale": "The two changes have independent behavior and test boundaries.",
        "proposed_stack": [_step("foundation"), _step("feature", depends_on="foundation")],
        "harness_prompt": "Create two ordered pull requests on new branches.",
        "risks_or_human_decisions": [],
    }
    arguments.update(overrides)
    return arguments


@pytest.mark.asyncio
async def test_set_stackability_review_persists_live_head(monkeypatch) -> None:
    persist = AsyncMock()
    monkeypatch.setattr(
        stackability_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "reviewer-1", "head_sha": "frozen-head"}},
    )
    monkeypatch.setattr(
        stackability_tool,
        "resolve_review_head_sha",
        AsyncMock(return_value="live-head"),
    )
    monkeypatch.setattr(stackability_tool, "persist_stackability_review", persist)

    result = await stackability_tool.set_stackability_review(**_arguments())

    assert result == {
        "success": True,
        "verdict": "split_recommended",
        "reviewed_head_sha": "live-head",
        "proposed_step_count": 2,
    }
    call = persist.await_args
    assert call is not None
    record = call.args[1]
    assert record["reviewed_head_sha"] == "live-head"
    assert record["publication"] == {
        "mode": None,
        "state": "unpublished",
        "github_comment_id": None,
        "github_review_id": None,
        "github_review_thread_id": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "error_prefix"),
    [
        ({"verdict": "maybe"}, "verdict:"),
        ({"rationale": ""}, "rationale:"),
        ({"proposed_stack": [_step("only")]}, "proposed_stack:"),
    ],
)
async def test_set_stackability_review_returns_validation_errors(
    monkeypatch, overrides: dict[str, object], error_prefix: str
) -> None:
    persist = AsyncMock()
    resolve_head = AsyncMock()
    monkeypatch.setattr(stackability_tool, "persist_stackability_review", persist)
    monkeypatch.setattr(stackability_tool, "resolve_review_head_sha", resolve_head)

    result = await stackability_tool.set_stackability_review(**_arguments(**overrides))

    assert result["success"] is False
    assert result["error"] == "invalid_stackability_review"
    assert any(error.startswith(error_prefix) for error in result["errors"])
    resolve_head.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_stackability_review_rejects_unavailable_head(monkeypatch) -> None:
    persist = AsyncMock()
    monkeypatch.setattr(
        stackability_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "reviewer-1"}},
    )
    monkeypatch.setattr(
        stackability_tool,
        "resolve_review_head_sha",
        AsyncMock(return_value=""),
    )
    monkeypatch.setattr(stackability_tool, "persist_stackability_review", persist)

    result = await stackability_tool.set_stackability_review(**_arguments())

    assert result == {"success": False, "error": "review_head_unavailable"}
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_stackability_review_handles_missing_reviewer_thread(monkeypatch) -> None:
    monkeypatch.setattr(
        stackability_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "missing-reviewer"}},
    )
    monkeypatch.setattr(
        stackability_tool,
        "resolve_review_head_sha",
        AsyncMock(side_effect=ReviewerThreadMissingError("missing-reviewer", RuntimeError())),
    )

    result = await stackability_tool.set_stackability_review(**_arguments())

    assert result["success"] is False
    assert result["error"] == "thread_not_found"
    assert result["thread_id"] == "missing-reviewer"


@pytest.mark.asyncio
async def test_set_stackability_review_requires_reviewer_thread_config(monkeypatch) -> None:
    monkeypatch.setattr(stackability_tool, "get_config", lambda: {"configurable": {}})

    result = await stackability_tool.set_stackability_review(**_arguments())

    assert result == {"success": False, "error": "reviewer_thread_unavailable"}
