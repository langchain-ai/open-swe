"""Execution failures and patch transport are observable target results."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from evals.agent import target

_INPUTS: dict[str, object] = {
    "instance_id": "probe-1",
    "repo": "probe/repo",
    "base_commit": "a" * 40,
    "problem_statement": "Fix addition",
}


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    monkeypatch.delenv("AGENT_EVAL_RUN_SOURCE", raising=False)
    client = AsyncMock()
    client.threads.create.return_value = {"thread_id": "thread-1"}
    monkeypatch.setattr(target, "get_client", lambda **kwargs: client)
    target.drain_thread_ids()
    yield client
    target.drain_thread_ids()


async def test_patch_is_collected_without_a_second_model_run(sdk: AsyncMock) -> None:
    patch = "diff --git a/x b/x\n"
    sdk.runs.wait.return_value = {
        "messages": [
            {
                "type": "ai",
                "content": "I did not print a patch",
                "usage_metadata": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                },
            },
        ],
        "agent_eval_result": {"status": "collected", "patch": patch},
    }
    output = await target.solve_task(_INPUTS)
    assert output["patch"] == patch
    assert output["failure_stage"] == "none"
    assert output["steps"] == 1
    assert output["input_tokens"] == 100
    assert output["usage_complete"] is True
    assert output["compaction"] is None
    assert sdk.runs.wait.await_count == 1
    assert target.drain_thread_ids() == {"thread-1"}


async def test_compaction_totals_are_reported_from_graph_state(sdk: AsyncMock) -> None:
    sdk.runs.wait.return_value = {
        "messages": [],
        "agent_eval_result": {"status": "collected", "patch": ""},
        "conversation_offloading": {
            "status": "completed",
            "trigger": "automatic",
            "count": 2,
            "total_messages_summarized": 12,
            "total_summarized_tokens": 4_000,
            "total_summary_tokens": 300,
            "total_duration_ms": 900,
            "overflow_count": 1,
            "file_path": "/conversation_history/session.md",
            "cutoff_index": 10,
        },
    }
    output = await target.solve_task(_INPUTS)
    assert output["compaction"] == {
        "count": 2,
        "messages_summarized": 12,
        "summarized_tokens": 4_000,
        "summary_tokens": 300,
        "duration_ms": 900,
        "overflow_count": 1,
    }


@pytest.mark.parametrize("artifact", [None, {"status": "collection_error", "error": "git failed"}])
async def test_missing_or_failed_artifact_is_not_a_no_change_run(
    sdk: AsyncMock, artifact: dict[str, str] | None
) -> None:
    sdk.runs.wait.return_value = {"messages": [], "agent_eval_result": artifact}
    output = await target.solve_task(_INPUTS)
    assert output["failure_stage"] == "patch_collection"
    assert output["error"]
    assert output["patch"] == ""


async def test_execution_error_keeps_the_thread_available_for_diagnosis(sdk: AsyncMock) -> None:
    sdk.runs.wait.side_effect = RuntimeError("provider unavailable")
    output = await target.solve_task(_INPUTS)
    assert output["failure_stage"] == "agent_execution"
    assert "provider unavailable" in str(output["error"])
    assert output["thread_id"] == "thread-1"


async def test_partial_usage_is_not_reported_as_complete(sdk: AsyncMock) -> None:
    sdk.runs.wait.return_value = {
        "messages": [
            {"type": "ai", "usage_metadata": {"input_tokens": 12, "output_tokens": 3}},
            {"type": "ai"},
        ],
        "agent_eval_result": {"status": "collected", "patch": ""},
    }
    output = await target.solve_task(_INPUTS)
    assert output["input_tokens"] == 12
    assert output["usage_complete"] is False


async def test_compaction_arm_is_sent_to_the_graph(
    sdk: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_EVAL_COMPACTION_TRIGGER_TOKENS", "12000")
    monkeypatch.setenv("AGENT_EVAL_COMPACTION_SUMMARY_PROMPT", "structured")
    sdk.runs.wait.return_value = {
        "messages": [],
        "agent_eval_result": {"status": "collected", "patch": ""},
    }
    await target.solve_task(_INPUTS)
    configurable = sdk.runs.wait.await_args.kwargs["config"]["configurable"]
    compaction = configurable["agent_eval"]["compaction"]
    assert compaction["trigger_tokens"] == 12000
    assert compaction["summary_prompt"] == "structured"
    assert compaction["trigger_fraction"] is None


async def test_invalid_instance_does_not_dispatch_a_run(sdk: AsyncMock) -> None:
    output = await target.solve_task({**_INPUTS, "base_commit": "not-a-sha"})
    assert output["failure_stage"] == "invalid_instance"
    sdk.threads.create.assert_not_awaited()
