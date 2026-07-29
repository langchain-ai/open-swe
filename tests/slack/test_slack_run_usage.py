from typing import Any

import pytest

from agent.utils.slack import (
    lookup_slack_message_for_run,
    store_slack_message_run_mapping,
    store_slack_run_mapping,
)


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.mark.asyncio
async def test_message_mapping_is_scoped_to_explicit_run() -> None:
    client: Any = _FakeClient()
    await store_slack_run_mapping(client, "C1", "1.0", "new-run")

    await store_slack_message_run_mapping(client, "C1", "1.0", "2.0", run_id="old-run")

    mapping = await lookup_slack_message_for_run(client, "C1", "old-run")
    assert mapping == {"run_id": "old-run", "thread_ts": "1.0", "message_ts": "2.0"}
    thread_item = client.store.items[(("slack_run_map", "C1"), "thread:1.0")]
    assert thread_item["value"]["run_id"] == "new-run"


@pytest.mark.asyncio
async def test_retry_trace_aliases_message_to_matching_durable_run() -> None:
    client: Any = _FakeClient()
    await store_slack_run_mapping(client, "C1", "1.0", "durable-run", usage_run_id="usage-1")

    await store_slack_message_run_mapping(
        client,
        "C1",
        "1.0",
        "2.0",
        run_id="retry-trace",
        usage_run_id="usage-1",
    )

    durable = await lookup_slack_message_for_run(client, "C1", "durable-run")
    retry = await lookup_slack_message_for_run(client, "C1", "retry-trace")
    assert durable is not None
    assert retry is not None
    assert durable["message_ts"] == "2.0"
    assert retry["message_ts"] == "2.0"
    assert durable["open_swe_run_id"] == "usage-1"
