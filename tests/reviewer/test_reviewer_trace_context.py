from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_credentials import LangSmithCredentials
from agent.review.trace_context import (
    PRTraceContext,
    format_pr_trace_context_prompt,
    prepare_pr_trace_context,
    resolve_pr_trace,
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


def _thread_id_from_filter(filter_expr: str) -> str | None:
    if "metadata_value" not in filter_expr:
        return None
    match = re.search(r'eq\(metadata_value, "([^"]+)"\)', filter_expr)
    return match.group(1) if match else None


class _FakeLangSmithClient:
    def __init__(self, search_results: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.filters: list[str] = []
        self.search_results = (
            search_results
            if search_results is not None
            else {
                'search("feature/trace-resolution")': [_run("branch", "thread-1")],
                'search("abc1234567890abcdef")': [_run("sha", "thread-1")],
            }
        )

    def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        filter_expr = kwargs["filter"]
        self.filters.append(filter_expr)
        for needle, runs in self.search_results.items():
            if needle in filter_expr:
                return runs
        thread_id = _thread_id_from_filter(filter_expr)
        if thread_id:
            return [
                _run(
                    f"turn-{thread_id}",
                    thread_id,
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


def _patches(client: _FakeLangSmithClient) -> Any:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    return (
        patch(
            "agent.review.trace_context.get_team_review_tracing_project",
            AsyncMock(return_value="pajuha"),
        ),
        patch(
            "agent.review.trace_context.get_langsmith_credentials", AsyncMock(return_value=creds)
        ),
        patch("agent.review.trace_context._client", return_value=client),
        patch(
            "agent.review.trace_context.get_langsmith_trace_url",
            return_value="https://smith/t/thread-1",
        ),
    )


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_resolves_on_branch_alone() -> None:
    fake_client = _FakeLangSmithClient()
    sandbox = _CapturingSandbox()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await prepare_pr_trace_context(
            configurable=_config(),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
        )

    assert result is not None
    assert result.file_path == "/workspace/.open-swe/review-author-trace.json"
    assert sandbox.uploaded_path == "/workspace/.open-swe/review-author-trace.json"
    assert result.thread_id == "thread-1"
    assert result.confidence == 0.9
    assert result.evidence == ["branch:feature/trace-resolution"]
    assert sandbox.payload is not None
    assert sandbox.payload["resolution"]["thread_id"] == "thread-1"
    assert sandbox.payload["runs"][0]["outputs"]["message"].startswith("Edited reviewer.py")
    assert any('search("feature/trace-resolution")' in f for f in fake_client.filters)
    # Branch search is scoped to the repo so a same-named branch elsewhere can't match.
    assert any(
        'search("feature/trace-resolution")' in f and 'search("langchain-ai/open-swe")' in f
        for f in fake_client.filters
    )
    # Thread runs use documented metadata key/value filter syntax, not has(metadata, ...).
    assert any('eq(metadata_value, "thread-1")' in f for f in fake_client.filters)
    assert not any("has(metadata" in f for f in fake_client.filters)
    # Full-text searches are bounded to a recent window to avoid LangSmith rate limits.
    assert all("gt(start_time" in f for f in fake_client.filters if "search(" in f)


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_picks_dominant_thread() -> None:
    fake_client = _FakeLangSmithClient(
        {
            'search("feature/dom")': [
                _run("a1", "thread-A"),
                _run("a2", "thread-A"),
                _run("b1", "thread-B"),
            ]
        }
    )
    sandbox = _CapturingSandbox()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await prepare_pr_trace_context(
            configurable=_config(branch_name="feature/dom"),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
        )

    assert result is not None
    assert result.thread_id == "thread-A"


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_falls_back_to_head_sha() -> None:
    fake_client = _FakeLangSmithClient()
    sandbox = _CapturingSandbox()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await prepare_pr_trace_context(
            configurable=_config(branch_name="main"),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
        )

    assert result is not None
    assert result.thread_id == "thread-1"
    assert result.confidence == 0.85
    assert result.evidence == ["sha:abc1234567"]
    assert not any('search("main")' in f for f in fake_client.filters)


@pytest.mark.asyncio
async def test_prepare_pr_trace_context_returns_none_without_match() -> None:
    fake_client = _FakeLangSmithClient()
    sandbox = _CapturingSandbox()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await prepare_pr_trace_context(
            configurable=_config(branch_name="main", head_sha=""),
            sandbox_backend=sandbox,  # type: ignore[arg-type]
            work_dir="/workspace",
        )

    assert result is None
    assert sandbox.payload is None


@pytest.mark.asyncio
async def test_resolve_pr_trace_returns_resolution() -> None:
    fake_client = _FakeLangSmithClient()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await resolve_pr_trace(configurable=_config())

    assert result.resolved is True
    assert result.thread_id == "thread-1"
    assert result.confidence == 0.9
    assert result.evidence == ["branch:feature/trace-resolution"]
    assert result.project == "pajuha"
    assert result.run_count == 1
    assert result.trace_url == "https://smith/t/thread-1"


@pytest.mark.asyncio
async def test_resolve_pr_trace_reports_reason_when_unresolved() -> None:
    fake_client = _FakeLangSmithClient()
    p1, p2, p3, p4 = _patches(fake_client)
    with p1, p2, p3, p4:
        result = await resolve_pr_trace(configurable=_config(branch_name="main", head_sha=""))

    assert result.resolved is False
    assert result.thread_id is None
    assert result.project == "pajuha"
    assert "No coding-agent thread matched" in result.detail


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

    assert "grep" in prompt
    assert "read_file" in prompt
    assert "/workspace/.open-swe/review-author-trace.json" in prompt
    assert "do not publish a trace summary" in prompt
