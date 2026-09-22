import json
from collections.abc import AsyncIterator

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

from agent.utils.background_task_state import update_background_task_state

type TaskStateServer = tuple[LangGraphClient, dict[str, object], list[httpx.Request]]


@pytest.fixture
async def task_state_server() -> AsyncIterator[TaskStateServer]:
    metadata: dict[str, object] = {"running_background_tasks": ["cmd-1"], "source": "slack"}
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/threads/search":
            return httpx.Response(200, json=[{"thread_id": "thread-1", "metadata": metadata}])
        if request.method == "GET":
            return httpx.Response(200, json={"thread_id": "thread-1", "metadata": metadata})
        if request.method == "PATCH":
            metadata.update(json.loads(request.content)["metadata"])
            return httpx.Response(204)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"thread_id": "lock"})

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        yield LangGraphClient(http), metadata, requests


@pytest.mark.parametrize("running,finished", [(["cmd-1"], []), ([], ["already-finished"])])
async def test_unchanged_tasks_do_not_write_metadata(
    task_state_server: TaskStateServer, running: list[str], finished: list[str]
) -> None:
    client, metadata, requests = task_state_server
    await update_background_task_state(client, "thread-1", running=running, finished=finished)
    assert metadata == {"running_background_tasks": ["cmd-1"], "source": "slack"}
    assert not any(request.method == "PATCH" for request in requests)


async def test_task_transition_uses_metadata_projection_and_minimal_write(
    task_state_server: TaskStateServer,
) -> None:
    client, metadata, requests = task_state_server
    await update_background_task_state(client, "thread-1", running=["cmd-2"], finished=["cmd-1"])
    assert metadata == {"running_background_tasks": ["cmd-2"], "source": "slack"}
    assert not any(request.method == "GET" for request in requests)
    reads = [request for request in requests if request.url.path == "/threads/search"]
    assert len(reads) == 1
    assert json.loads(reads[0].content) == {
        "ids": ["thread-1"],
        "select": ["metadata"],
        "limit": 1,
        "offset": 0,
    }
    writes = [request for request in requests if request.method == "PATCH"]
    assert len(writes) == 1
    assert writes[0].headers.get("prefer") == "return=minimal"


async def test_reset_clears_running_tasks(task_state_server: TaskStateServer) -> None:
    client, metadata, _ = task_state_server
    await update_background_task_state(client, "thread-1", reset=True)
    assert metadata == {"running_background_tasks": [], "source": "slack"}


async def test_task_transition_preserves_concurrent_launch(
    task_state_server: TaskStateServer,
) -> None:
    client, metadata, _ = task_state_server
    metadata["running_background_tasks"] = ["cmd-old", "cmd-concurrent"]
    await update_background_task_state(client, "thread-1", finished=["cmd-old"])
    assert metadata == {"running_background_tasks": ["cmd-concurrent"], "source": "slack"}


async def test_repeated_reset_does_not_write_metadata(task_state_server: TaskStateServer) -> None:
    client, metadata, requests = task_state_server
    metadata["running_background_tasks"] = []
    await update_background_task_state(client, "thread-1", reset=True)
    assert not any(request.method == "PATCH" for request in requests)
