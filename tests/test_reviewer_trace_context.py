from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_credentials import LangSmithCredentials
from agent.reviewer_trace_context import (
    PRTraceContext,
    format_pr_trace_context_prompt,
    prepare_pr_trace_context,
)


def _run(
    run_id: str,
    thread_id: str,
    *,
    metadata: dict[str, Any] | None = None,
    inputs: Any = None,
    outputs: Any = None,
) -> dict[str, Any]:
    run_metadata = {"thread_id": thread_id}
    if metadata:
        run_metadata.update(metadata)
    return {
        "id": run_id,
        "name": "Claude Code Turn",
        "run_type": "chain",
        "status": "success",
        "trace_id": f"trace-{run_id}",
        "metadata": run_metadata,
        "start_time": "2026-01-01T00:00:00+00:00",
        "end_time": "2026-01-01T00:01:00+00:00",
        "inputs": inputs or {},
        "outputs": outputs or {},
    }


class _FakeLangSmithClient:
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
            return [_run("repo-meta", "thread-1")]
        if '"thread_id":"thread-1"' in filter_expr:
            return [
                _run(
                    "turn-1",
                    "thread-1",
                    metadata={"repository_name": "langchain-ai/open-swe"},
                    inputs={"message": "Need to update reviewer.py"},
                    outputs={"message": "Edited reviewer.py after checking edge cases."},
                )
            ]
        return []


class _CapturingSandbox:
    def __init__(self) -> None:
        self.uploaded_path = ""
        self.payload: dict[str, Any] | None = None

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        self.uploaded_path, content = files[0]
        self.payload = json.loads(content.decode())
        return [type("Result", (), {"error": None})()]


def _config(**overrides: Any) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "pr_number": 7,
        "pr_url": "https://github.com/langchain-ai/open-swe/pull/7",
        "branch_name": "feature/trace-resolution",
        "head_sha": "abc1234567890abcdef",
        "base_sha": "def1234567890abcdef",
    }
    configurable.update(overrides)
    return configurable


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_writes_raw_trace_json_to_sandbox() -> None:
    fake_client = _FakeLangSmithClient()
    sandbox = _CapturingSandbox()
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with (
        patch(
            "agent.reviewer_trace_context.get_team_review_tracing_project",
            AsyncMock(return_value="pajuha"),
        ),
        patch(
            "agent.reviewer_trace_context.get_langsmith_credentials", AsyncMock(return_value=creds)
        ),
        patch("agent.reviewer_trace_context._client", return_value=fake_client),
        patch(
            "agent.reviewer_trace_context.get_langsmith_trace_url",
            return_value="https://smith/t/thread-1",
        ),
    ):
        result = await prepare_pr_trace_context(
            configurable=_config(),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
            github_token=None,
        )

    assert result is not None
    assert result.file_path == "/workspace/.open-swe/review-author-trace.json"
    assert sandbox.uploaded_path == "/workspace/.open-swe/review-author-trace.json"
    assert result.thread_id == "thread-1"
    assert result.confidence >= 0.70
    assert sandbox.payload is not None
    assert sandbox.payload["resolution"]["thread_id"] == "thread-1"
    assert sandbox.payload["runs"][0]["outputs"]["message"].startswith("Edited reviewer.py")
    assert any('search("feature/trace-resolution")' in f for f in fake_client.filters)


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_returns_none_without_strong_match() -> None:
    sandbox = _CapturingSandbox()
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with (
        patch(
            "agent.reviewer_trace_context.get_team_review_tracing_project",
            AsyncMock(return_value="pajuha"),
        ),
        patch(
            "agent.reviewer_trace_context.get_langsmith_credentials", AsyncMock(return_value=creds)
        ),
        patch("agent.reviewer_trace_context._client", return_value=_FakeLangSmithClient()),
    ):
        result = await prepare_pr_trace_context(
            configurable=_config(branch_name="main", head_sha=""),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
            github_token=None,
        )

    assert result is None
    assert sandbox.payload is None


def test_format_pr_trace_context_prompt_points_reviewer_at_file() -> None:
    prompt = format_pr_trace_context_prompt(
        PRTraceContext(
            file_path="/workspace/.open-swe/review-author-trace.json",
            thread_id="thread-1",
            confidence=0.87,
            evidence=["branch:feature/x"],
            trace_url="https://smith/t/thread-1",
            run_count=3,
        )
    )

    assert "read_file" in prompt
    assert "/workspace/.open-swe/review-author-trace.json" in prompt
    assert "do not publish a trace summary" in prompt
