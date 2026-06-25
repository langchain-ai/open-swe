from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_credentials import LangSmithCredentials
from agent.tools import pr_trace_resolution as trace_tools


def _run(
    run_id: str,
    thread_id: str,
    *,
    start: str = "2026-01-01T00:00:00+00:00",
    end: str = "2026-01-01T00:01:00+00:00",
    metadata: dict[str, Any] | None = None,
    inputs: Any = None,
    outputs: Any = None,
) -> dict[str, Any]:
    meta = {"thread_id": thread_id}
    if metadata:
        meta.update(metadata)
    return {
        "id": run_id,
        "metadata": meta,
        "start_time": start,
        "end_time": end,
        "inputs": inputs or {},
        "outputs": outputs or {},
    }


class _FakeClient:
    def __init__(self) -> None:
        self.filters: list[str] = []

    def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        filter_expr = kwargs["filter"]
        self.filters.append(filter_expr)
        if 'search("feature/trace-resolution")' in filter_expr:
            return [_run("branch", "thread-1")]
        if 'search("abc1234567")' in filter_expr:
            return [_run("sha", "thread-1")]
        if 'search("langchain-ai/open-swe")' in filter_expr:
            return [_run("repo", "thread-1", metadata={"repository_name": "langchain-ai/open-swe"})]
        if '"repository_name":"langchain-ai/open-swe"' in filter_expr:
            return [
                _run("repo-meta", "thread-1", metadata={"repository_name": "langchain-ai/open-swe"})
            ]
        if '"thread_id":"thread-1"' in filter_expr:
            return [
                _run(
                    "turn-1",
                    "thread-1",
                    metadata={
                        "repository_name": "langchain-ai/open-swe",
                        "local_username": "alice",
                    },
                    start="2026-01-01T00:00:00+00:00",
                    end="2026-01-01T00:02:00+00:00",
                )
            ]
        return []


def _config(**overrides: Any) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "pr_number": 7,
        "pr_url": "https://github.com/langchain-ai/open-swe/pull/7",
        "branch_name": "feature/trace-resolution",
        "head_sha": "abc1234567890abcdef",
        "github_login": "alice",
    }
    configurable.update(overrides)
    return {"configurable": configurable}


@pytest.mark.asyncio
async def test_resolve_pr_to_threads_scores_strong_and_repo_evidence() -> None:
    fake_client = _FakeClient()
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with (
        patch.object(
            trace_tools, "get_team_review_tracing_project", AsyncMock(return_value="pajuha")
        ),
        patch.object(trace_tools, "get_langsmith_credentials", AsyncMock(return_value=creds)),
        patch.object(trace_tools, "_client", return_value=fake_client),
        patch.object(trace_tools, "get_config", return_value=_config()),
        patch.object(trace_tools, "get_github_token", return_value=None),
        patch.object(trace_tools, "get_thread_id_from_runtime", return_value=""),
        patch.object(
            trace_tools, "get_langsmith_trace_url", return_value="https://smith/t/thread-1"
        ),
    ):
        result = await trace_tools._resolve_pr_to_threads_async()

    assert result["success"] is True
    assert result["candidates"][0]["thread_id"] == "thread-1"
    assert result["candidates"][0]["confidence"] >= 0.70
    assert "branch:feature/trace-resolution" in result["candidates"][0]["evidence"]
    assert "repo:langchain-ai/open-swe" in result["candidates"][0]["evidence"]
    assert any('search("feature/trace-resolution")' in f for f in fake_client.filters)


@pytest.mark.asyncio
async def test_resolve_pr_to_threads_skips_generic_branch_without_strong_hits() -> None:
    fake_client = _FakeClient()
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with (
        patch.object(
            trace_tools, "get_team_review_tracing_project", AsyncMock(return_value="pajuha")
        ),
        patch.object(trace_tools, "get_langsmith_credentials", AsyncMock(return_value=creds)),
        patch.object(trace_tools, "_client", return_value=fake_client),
        patch.object(
            trace_tools, "get_config", return_value=_config(branch_name="main", head_sha="")
        ),
        patch.object(trace_tools, "get_github_token", return_value=None),
        patch.object(trace_tools, "get_thread_id_from_runtime", return_value=""),
    ):
        result = await trace_tools._resolve_pr_to_threads_async()

    assert result["success"] is True
    assert result["candidates"] == []
    assert any("Skipped generic branch name" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_summarize_agent_session_returns_compact_digest() -> None:
    class DigestClient:
        def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert '"thread_id":"thread-1"' in kwargs["filter"]
            return [
                _run(
                    "turn-1",
                    "thread-1",
                    inputs={"message": "Considered an alternative batch path in src/app.py."},
                    outputs={
                        "message": (
                            "Implemented src/app.py because empty input needs a fallback. "
                            "Dismissed the timeout concern as not an issue."
                        )
                    },
                )
            ]

    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with (
        patch.object(
            trace_tools, "get_team_review_tracing_project", AsyncMock(return_value="pajuha")
        ),
        patch.object(trace_tools, "get_langsmith_credentials", AsyncMock(return_value=creds)),
        patch.object(trace_tools, "_client", return_value=DigestClient()),
        patch.object(
            trace_tools, "get_langsmith_trace_url", return_value="https://smith/t/thread-1"
        ),
    ):
        result = await trace_tools._summarize_agent_session_async("thread-1")

    assert result["success"] is True
    digest = result["digest"]
    assert "src/app.py" in digest["files_touched"]
    assert digest["alternatives_considered"]
    assert digest["concerns_dismissed"]
    assert digest["edge_cases_handled"]
