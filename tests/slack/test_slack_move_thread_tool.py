import importlib
from typing import Any

import pytest

move_tool = importlib.import_module("agent.tools.slack_move_thread")


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "agent-thread",
            "slack_thread": {
                "channel_id": "C_SOURCE",
                "thread_ts": "1.0",
                "triggering_user_id": "U1",
                "triggering_user_name": "Alice",
                "triggering_user_email": "alice@example.com",
                "triggering_event_ts": "1.1",
            },
        }
    }


class _Threads:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "sandbox_id": "sandbox-1",
            "source": "slack",
            "source_context": {"slack_thread": dict(_config()["configurable"]["slack_thread"])},
        }
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})
        self.metadata.update(metadata)


class _Client:
    def __init__(self) -> None:
        self.threads = _Threads()


@pytest.mark.asyncio
async def test_move_rebinds_metadata_and_deletes_source_associations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    captured: dict[str, Any] = {"bindings": [], "deletions": [], "run_mappings": []}

    async def fake_post(channel_id: str, text: str, **kwargs: Any) -> tuple[str, None]:
        captured["post"] = {"channel_id": channel_id, "text": text, "kwargs": kwargs}
        return "2.0", None

    async def fake_bind(_client: Any, channel_id: str, thread_ts: str, thread_id: str) -> str:
        captured["bindings"].append((channel_id, thread_ts, thread_id))
        return thread_id

    async def fake_delete(_client: Any, channel_id: str, thread_ts: str, **kwargs: Any) -> None:
        captured["deletions"].append((channel_id, thread_ts))

    async def fake_lookup_run(*args: Any) -> dict[str, str]:
        return {"run_id": "run-1", "triggering_user_id": "U1"}

    async def fake_store_run(*args: Any, **kwargs: Any) -> None:
        captured["run_mappings"].append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(move_tool, "get_config", _config)
    monkeypatch.setattr(move_tool, "get_client", lambda url: client)
    monkeypatch.setattr(move_tool, "post_slack_top_level_message_with_ts", fake_post)
    monkeypatch.setattr(move_tool, "bind_slack_thread_id", fake_bind)
    monkeypatch.setattr(move_tool, "delete_slack_thread_associations", fake_delete)
    monkeypatch.setattr(move_tool, "lookup_slack_thread_run_mapping", fake_lookup_run)
    monkeypatch.setattr(move_tool, "store_slack_run_mapping", fake_store_run)

    result = await move_tool.slack_move_thread("Continue the current task here.", "C_TARGET")

    assert result["success"] is True
    assert result["thread_id"] == "agent-thread"
    assert result["channel_id"] == "C_TARGET"
    assert result["thread_ts"] == "2.0"
    assert captured["post"]["channel_id"] == "C_TARGET"
    assert "Continue the current task here." in captured["post"]["text"]
    assert captured["bindings"] == [("C_TARGET", "2.0", "agent-thread")]
    assert captured["deletions"] == [("C_SOURCE", "1.0")]
    update = client.threads.updates[-1]["metadata"]
    assert update["source_context"] == {
        "slack_thread": {
            "channel_id": "C_TARGET",
            "thread_ts": "2.0",
            "triggering_user_id": "U1",
            "triggering_user_name": "Alice",
            "triggering_user_email": "alice@example.com",
            "triggering_event_ts": "2.0",
        }
    }
    assert client.threads.metadata["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert client.threads.metadata["sandbox_id"] == "sandbox-1"
    assert captured["run_mappings"][0]["args"][1:4] == ("C_TARGET", "2.0", "run-1")


@pytest.mark.asyncio
async def test_move_retry_finishes_source_cleanup_without_posting_another_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    client.threads.metadata["source_context"] = {
        "slack_thread": {"channel_id": "C_TARGET", "thread_ts": "2.0"}
    }
    deleted: list[tuple[str, str]] = []

    async def fake_bind(*args: Any) -> str:
        return "agent-thread"

    async def fake_delete(_client: Any, channel_id: str, thread_ts: str, **kwargs: Any) -> None:
        deleted.append((channel_id, thread_ts))

    async def fail_post(*args: Any, **kwargs: Any) -> tuple[str, None]:
        raise AssertionError("retry must not create another Slack root")

    monkeypatch.setattr(move_tool, "get_config", _config)
    monkeypatch.setattr(move_tool, "get_client", lambda url: client)
    monkeypatch.setattr(move_tool, "bind_slack_thread_id", fake_bind)
    monkeypatch.setattr(move_tool, "delete_slack_thread_associations", fake_delete)
    monkeypatch.setattr(move_tool, "post_slack_top_level_message_with_ts", fail_post)

    result = await move_tool.slack_move_thread("Continue here", "C_TARGET")

    assert result["success"] is True
    assert result["channel_id"] == "C_TARGET"
    assert deleted == [("C_SOURCE", "1.0")]


@pytest.mark.asyncio
async def test_move_does_not_change_state_when_slack_rejects_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[None, str]:
        return None, "not_in_channel"

    monkeypatch.setattr(move_tool, "get_config", _config)
    monkeypatch.setattr(move_tool, "get_client", lambda url: client)
    monkeypatch.setattr(move_tool, "post_slack_top_level_message_with_ts", fake_post)

    result = await move_tool.slack_move_thread("Continue here", "C_TARGET")

    assert result["success"] is False
    assert result["slack_error"] == "not_in_channel"
    assert client.threads.updates == []
