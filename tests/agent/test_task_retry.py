import asyncio
import json
import sys
from pathlib import Path

import httpx2
import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from openswe.middleware.model_call_timeout import ModelCallTimeoutError
from openswe.middleware.task_retry import TaskConcurrencyMiddleware, task_on_failure, task_retry_on

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.middleware import TaskConcurrencyMiddleware as RuntimeTaskConcurrencyMiddleware


class _HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _InvalidPromptError(Exception):
    body = {"error": {"type": "invalid_request_error", "code": "invalid_prompt"}}


def test_task_retry_on_transient_status() -> None:
    assert task_retry_on(_HTTPError(408)) is True
    assert task_retry_on(_HTTPError(409)) is True
    assert task_retry_on(_HTTPError(425)) is True
    assert task_retry_on(_HTTPError(429)) is True
    assert task_retry_on(_HTTPError(503)) is True
    assert task_retry_on(_HTTPError(529)) is True
    assert task_retry_on(_HTTPError(400)) is False


def test_task_retry_on_subagent_model_deadline() -> None:
    # A subagent has no fallback middleware, so retrying the delegated task is
    # how a wedged model call inside it recovers.
    assert task_retry_on(ModelCallTimeoutError("wedged")) is True


def test_task_retry_on_httpx_transport_subclasses() -> None:
    assert task_retry_on(httpx2.RemoteProtocolError("stream dropped")) is True
    assert task_retry_on(httpx2.ConnectError("connect failed")) is True


def test_task_on_failure_returns_model_fixable_error() -> None:
    payload = json.loads(task_on_failure(_InvalidPromptError("bad prompt")))

    assert payload["status"] == "failed"
    assert payload["source"] == "subagent"
    assert payload["error"]["code"] == "invalid_prompt"


def test_task_on_failure_reraises_unrecoverable_error() -> None:
    exc = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        task_on_failure(exc)


@pytest.mark.parametrize(
    "middleware_type", [TaskConcurrencyMiddleware, RuntimeTaskConcurrencyMiddleware]
)
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_task_batch_queues_until_a_slot_is_released(
    middleware_type: type[TaskConcurrencyMiddleware] | type[RuntimeTaskConcurrencyMiddleware],
    outcome: str,
) -> None:
    middleware = middleware_type()
    entered: asyncio.Queue[int] = asyncio.Queue()
    release = [asyncio.Event() for _ in range(10)]
    active = 0
    peak = 0

    async def execute(request: ToolCallRequest) -> ToolMessage:
        nonlocal active, peak
        index = int(request.tool_call["id"])
        active += 1
        peak = max(peak, active)
        entered.put_nowait(index)
        try:
            await release[index].wait()
            if index == 0 and outcome == "failure":
                raise RuntimeError("subagent failed")
            return ToolMessage(content="done", tool_call_id=str(index))
        finally:
            active -= 1

    calls = [
        asyncio.create_task(
            middleware.awrap_tool_call(
                ToolCallRequest(
                    tool_call={"name": "task", "args": {}, "id": str(index)},
                    tool=None,
                    state={"messages": []},
                    runtime=None,
                ),
                execute,
            )
        )
        for index in range(10)
    ]
    try:
        async with asyncio.timeout(5):
            running = [await entered.get() for _ in range(3)]
            await asyncio.sleep(0)
            assert entered.empty()
            assert active == 3
            if outcome == "cancel":
                calls[0].cancel()
            else:
                release[0].set()
            running.remove(0)
            running.append(await entered.get())
            for _ in range(6):
                release[running.pop(0)].set()
                running.append(await entered.get())
            for index in running:
                release[index].set()
            results = await asyncio.gather(*calls, return_exceptions=True)
            assert peak == 3
            assert active == 0
            assert sum(isinstance(result, ToolMessage) for result in results) == (
                10 if outcome == "success" else 9
            )
    finally:
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
