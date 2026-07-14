from __future__ import annotations

import importlib
from typing import Any

import pytest

from agent.utils.thread_ids import generate_thread_id_from_slack_thread

slack_breakout_tool = importlib.import_module("agent.tools.slack_start_new_thread")


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "github_login": "alice",
            "user_email": "alice@example.com",
            "agent_model_id": "anthropic:claude-sonnet-4-5",
            "agent_effort": "high",
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1700000000.000001",
                "triggering_user_id": "U1",
                "triggering_user_name": "Alice",
                "triggering_user_email": "alice@example.com",
                "triggering_event_ts": "1700000000.000002",
            },
        }
    }


class _FakeThreadsClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.captured = captured

    async def create(self, *, thread_id: str, if_exists: str, metadata: dict[str, Any]) -> None:
        self.captured["thread_create"] = {
            "thread_id": thread_id,
            "if_exists": if_exists,
            "metadata": metadata,
        }

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.captured["thread_update"] = {"thread_id": thread_id, "metadata": metadata}


class _FakeClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.threads = _FakeThreadsClient(captured)


async def test_slack_start_new_thread_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {"stored_mappings": []}
    new_ts = "1700000000.111111"

    async def fake_post_top_level(
        channel_id: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, str | None]:
        captured["top_level_post"] = {
            "channel_id": channel_id,
            "text": text,
            "unfurl_links": unfurl_links,
            "unfurl_media": unfurl_media,
            "blocks": blocks,
        }
        return new_ts, None

    async def fake_dispatch_agent_run(
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        client: Any,
        **kwargs: Any,
    ) -> dict[str, str]:
        captured["dispatch"] = {
            "thread_id": thread_id,
            "content": content,
            "configurable": configurable,
            "source": source,
            "client": client,
            "kwargs": kwargs,
        }
        return {"run_id": "run-123"}

    async def fake_store_mapping(
        client: Any,
        channel_id: str,
        thread_ts: str,
        run_id: str,
        *,
        message_ts: str | None = None,
        triggering_user_id: str | None = None,
    ) -> None:
        captured["stored_mappings"].append(
            {
                "client": client,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "run_id": run_id,
                "message_ts": message_ts,
                "triggering_user_id": triggering_user_id,
            }
        )

    fake_client = _FakeClient(captured)
    monkeypatch.setattr(slack_breakout_tool, "get_config", _config)
    monkeypatch.setattr(slack_breakout_tool, "get_client", lambda url: fake_client)
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_top_level_message_with_ts", fake_post_top_level
    )
    monkeypatch.setattr(slack_breakout_tool, "dispatch_agent_run", fake_dispatch_agent_run)
    monkeypatch.setattr(slack_breakout_tool, "store_slack_run_mapping", fake_store_mapping)
    monkeypatch.setattr(
        slack_breakout_tool, "get_langsmith_trace_url", lambda thread_id: "https://smith/x"
    )
    monkeypatch.setattr(
        slack_breakout_tool,
        "dashboard_thread_url",
        lambda thread_id: f"https://dashboard.example/agents/{thread_id}",
    )

    result = await slack_breakout_tool.slack_start_new_thread(
        "Investigate follow-up",
        "Use the same repo and investigate the follow-up aspect in detail.",
    )

    expected_thread_id = generate_thread_id_from_slack_thread("C1", new_ts)
    assert result == {
        "success": True,
        "thread_id": expected_thread_id,
        "thread_ts": new_ts,
        "dashboard_url": f"https://dashboard.example/agents/{expected_thread_id}",
    }
    assert captured["top_level_post"]["channel_id"] == "C1"
    assert "Investigate follow-up" in captured["top_level_post"]["text"]
    assert "langchain-ai/open-swe" in captured["top_level_post"]["text"]
    assert captured["top_level_post"]["unfurl_links"] is False
    assert captured["thread_create"]["if_exists"] == "do_nothing"
    assert captured["thread_create"]["thread_id"] == expected_thread_id
    metadata = captured["thread_update"]["metadata"]
    assert metadata["source"] == "slack"
    assert metadata["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert metadata["github_login"] == "alice"
    assert metadata["triggering_user_email"] == "alice@example.com"
    assert metadata["source_context"]["slack_thread"]["thread_ts"] == new_ts
    assert metadata["source_context"]["slack_thread"]["triggering_user_id"] == "U1"
    assert metadata["source_context"]["breakout_from"] == {
        "channel_id": "C1",
        "thread_ts": "1700000000.000001",
        "message_ts": "1700000000.000002",
    }
    dispatch = captured["dispatch"]
    assert dispatch["thread_id"] == expected_thread_id
    assert dispatch["source"] == "slack"
    assert dispatch["configurable"]["slack_thread"]["thread_ts"] == new_ts
    assert dispatch["configurable"]["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert dispatch["configurable"]["github_login"] == "alice"
    assert dispatch["configurable"]["agent_model_id"] == "anthropic:claude-sonnet-4-5"
    assert "Breakout Instructions" in dispatch["content"]
    assert "## Open SWE Links" in dispatch["content"]
    assert f"- Web: https://dashboard.example/agents/{expected_thread_id}" in dispatch["content"]
    assert "- Trace: https://smith/x" in dispatch["content"]
    assert "do not duplicate it manually" in dispatch["content"]
    assert "trace" not in captured
    assert [item["message_ts"] for item in captured["stored_mappings"]] == [new_ts]
    assert all(item["triggering_user_id"] == "U1" for item in captured["stored_mappings"])


async def test_slack_start_new_thread_requires_slack_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_breakout_tool, "get_config", lambda: {"configurable": {}})

    result = await slack_breakout_tool.slack_start_new_thread("Title", "Instructions")

    assert result == {"success": False, "error": "Missing slack_thread config"}


@pytest.mark.parametrize(
    ("title", "instructions", "error"),
    [
        ("", "Instructions", "title is required"),
        ("Title", "", "instructions is required"),
        ("x" * 161, "Instructions", "title is too long"),
        ("Title", "x" * 12001, "instructions is too long"),
    ],
)
async def test_slack_start_new_thread_validates_text(
    title: str,
    instructions: str,
    error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_breakout_tool, "get_config", _config)

    result = await slack_breakout_tool.slack_start_new_thread(title, instructions)

    assert result["success"] is False
    assert result["error"] == error


async def test_slack_start_new_thread_rejects_invalid_repo_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_breakout_tool, "get_config", _config)

    result = await slack_breakout_tool.slack_start_new_thread(
        "Title", "Instructions", default_repo="https://github.com/langchain-ai/open-swe"
    )

    assert result == {
        "success": False,
        "error": "default_repo must be a simple owner/name repository string",
    }


async def test_slack_start_new_thread_returns_slack_failure_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, bool] = {"dispatched": False}

    async def fake_post_top_level(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        return None, "msg_too_long"

    async def fake_dispatch_agent_run(*args: Any, **kwargs: Any) -> dict[str, str]:
        captured["dispatched"] = True
        return {"run_id": "run-123"}

    monkeypatch.setattr(slack_breakout_tool, "get_config", _config)
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_top_level_message_with_ts", fake_post_top_level
    )
    monkeypatch.setattr(slack_breakout_tool, "dispatch_agent_run", fake_dispatch_agent_run)

    result = await slack_breakout_tool.slack_start_new_thread("Title", "Instructions")

    assert result["success"] is False
    assert result["error"] == "msg_too_long"
    assert result["slack_error"] == "msg_too_long"
    assert "shorter" in result["hint"]
    assert captured["dispatched"] is False
