from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from agent.utils import run_usage
from agent.utils.run_usage import aggregate_run_usage


def test_aggregate_run_usage_includes_multiple_models_and_deduplicates() -> None:
    runs = [
        {
            "id": "1",
            "extra": {"metadata": {"ls_model_name": "claude-opus-5"}},
            "prompt_tokens": 10_000,
            "completion_tokens": 500,
            "total_cost": Decimal("0.21"),
        },
        {
            "id": "2",
            "extra": {"metadata": {"ls_model_name": "gpt-5.6-sol"}},
            "total_tokens": 2_000,
            "prompt_cost": Decimal("0.02"),
            "completion_cost": Decimal("0.01"),
        },
        {
            "id": "2",
            "extra": {"metadata": {"ls_model_name": "gpt-5.6-sol"}},
            "total_tokens": 2_000,
            "total_cost": Decimal("0.03"),
        },
    ]

    summary = aggregate_run_usage(runs)

    assert summary is not None
    assert summary.models == ("claude-opus-5", "gpt-5.6-sol")
    assert summary.total_tokens == 12_500
    assert summary.total_cost == Decimal("0.24")


def test_aggregate_run_usage_omits_partial_cost() -> None:
    summary = aggregate_run_usage(
        [
            {
                "id": "1",
                "extra": {"metadata": {"ls_model_name": "claude-opus-5"}},
                "total_tokens": 100,
                "total_cost": Decimal("0.01"),
            },
            {
                "id": "2",
                "extra": {"metadata": {"ls_model_name": "gpt-5.6-sol"}},
                "total_tokens": 50,
            },
        ]
    )

    assert summary is not None
    assert summary.total_tokens == 150
    assert summary.total_cost is None


def test_aggregate_run_usage_omits_totals_when_any_call_is_pending() -> None:
    summary = aggregate_run_usage(
        [
            {
                "id": "1",
                "extra": {"metadata": {"ls_model_name": "model-a"}},
                "total_tokens": 100,
                "total_cost": Decimal("0.01"),
            },
            {
                "id": "2",
                "extra": {"metadata": {"ls_model_name": "model-b"}},
            },
        ]
    )

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.total_tokens is None
    assert summary.total_cost is None


def test_aggregate_run_usage_returns_none_without_llm_runs() -> None:
    assert aggregate_run_usage([]) is None


@pytest.mark.asyncio
async def test_fetch_run_usage_reads_only_llm_runs_in_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        def read_run(self, run_id: str, load_child_runs: bool) -> Any:
            captured["read"] = (run_id, load_child_runs)
            return SimpleNamespace(id="root", trace_id="trace-1")

        def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
            captured["list"] = kwargs
            return [
                {
                    "id": "llm-1",
                    "extra": {"metadata": {"ls_model_name": "claude-opus-5"}},
                    "total_tokens": 100,
                    "total_cost": Decimal("0.01"),
                }
            ]

    monkeypatch.setattr(run_usage, "_build_prod_langsmith_client", lambda: _Client())

    summary = await run_usage.fetch_run_usage("run-1")

    assert summary is not None
    assert captured["read"] == ("run-1", False)
    assert captured["list"]["project_name"] == "open-swe-agent"
    assert captured["list"]["trace_id"] == "trace-1"
    assert captured["list"]["run_type"] == "llm"


@pytest.mark.asyncio
async def test_fetch_run_usage_queries_stable_metadata_across_retry_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        def read_run(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("stable usage lookup must not read a single trace")

        def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
            captured.update(kwargs)
            return [
                {
                    "id": "attempt-1",
                    "extra": {"metadata": {"ls_model_name": "model-a"}},
                    "total_tokens": 100,
                    "total_cost": Decimal("0.01"),
                },
                {
                    "id": "attempt-2",
                    "extra": {"metadata": {"ls_model_name": "model-b"}},
                    "total_tokens": 200,
                    "total_cost": Decimal("0.02"),
                },
            ]

    monkeypatch.setattr(run_usage, "_build_prod_langsmith_client", lambda: _Client())

    summary = await run_usage.fetch_run_usage("durable-run", usage_run_id="usage-1")

    assert summary is not None
    assert summary.total_tokens == 300
    assert summary.total_cost == Decimal("0.03")
    assert captured["run_type"] == "llm"
    assert "open_swe_run_id" in captured["filter"]
    assert "usage-1" in captured["filter"]
    assert "trace_id" not in captured
