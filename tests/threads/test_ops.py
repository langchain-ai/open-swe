"""Thread metadata reads must distinguish "missing" from "unreachable"."""

from collections.abc import Callable

import httpx
import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent.threads import ops


async def test_fetch_thread_metadata_returns_the_stored_metadata(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> None:
    client = patched_langgraph_client(
        ops, client=FakeLangGraphClient(thread_metadata={"source": "slack"})
    )

    assert await ops.fetch_thread_metadata("t1") == {"source": "slack"}
    assert client.calls == [("threads.get", {"thread_id": "t1"})]


async def test_fetch_thread_metadata_returns_empty_dict_for_a_thread_without_metadata(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> None:
    patched_langgraph_client(ops, client=FakeLangGraphClient(threads=[{"thread_id": "t1"}]))

    assert await ops.fetch_thread_metadata("t1") == {}


async def test_fetch_thread_metadata_returns_none_when_the_thread_does_not_exist(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> None:
    patched_langgraph_client(ops, client=FakeLangGraphClient())

    assert await ops.fetch_thread_metadata("missing") is None


async def test_fetch_thread_metadata_raises_on_a_transport_error(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> None:
    client = patched_langgraph_client(ops, client=FakeLangGraphClient(thread_metadata={}))
    client.threads.get_error = httpx.ConnectError("langgraph unreachable")

    with pytest.raises(httpx.ConnectError, match="langgraph unreachable"):
        await ops.fetch_thread_metadata("t1")


async def test_fetch_thread_metadata_raises_on_a_server_error(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> None:
    client = patched_langgraph_client(ops, client=FakeLangGraphClient(thread_metadata={}))
    request = httpx.Request("GET", "http://langgraph.test/threads/t1")
    client.threads.get_error = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(503, request=request)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await ops.fetch_thread_metadata("t1")


def test_is_not_found_error_reads_the_status_off_either_shape() -> None:
    request = httpx.Request("GET", "http://langgraph.test/threads/t1")
    raw_404 = httpx.HTTPStatusError(
        "missing", request=request, response=httpx.Response(404, request=request)
    )
    raw_503 = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(503, request=request)
    )
    typed_404 = httpx.HTTPStatusError("missing", request=request, response=None)  # type: ignore[arg-type]
    typed_404.status_code = 404  # type: ignore[attr-defined]

    assert ops.is_not_found_error(raw_404) is True
    assert ops.is_not_found_error(typed_404) is True
    assert ops.is_not_found_error(raw_503) is False
    assert ops.is_not_found_error(httpx.ConnectError("down")) is False
