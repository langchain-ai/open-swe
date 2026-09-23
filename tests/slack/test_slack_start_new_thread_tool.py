import importlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.credential_scope import (
    PrAuthorNotAParticipant,
    pr_author_login,
    private_credential_login,
)

slack_breakout_tool = importlib.import_module("agent.slack.tools.start_new_thread")


async def _fake_trace_url(thread_id: str, **kwargs: object) -> str:
    return "https://smith/x"


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "parent-thread",
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

    async def get(self, thread_id: str) -> dict[str, object]:
        return {"metadata": {"visibility": "public", "owner_type": "user", "owner_login": "alice"}}

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


@pytest.fixture(autouse=True)
def parent_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_breakout_tool, "langgraph_client", lambda: _FakeClient({}))


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

    async def fake_post_thread_reply(
        channel_id: str,
        thread_ts: str,
        text: str,
        *,
        unfurl_links: bool = True,
        unfurl_media: bool = True,
        blocks: list[dict[str, Any]] | None = None,
        usage: Any = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured["thread_reply"] = {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "text": text,
            "unfurl_links": unfurl_links,
            "unfurl_media": unfurl_media,
            "blocks": blocks,
            "usage": usage,
        }
        return "1700000000.222222", None

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

    async def fake_bind(client: Any, channel_id: str, thread_ts: str, thread_id: str) -> str:
        captured["binding"] = {
            "client": client,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "thread_id": thread_id,
        }
        return thread_id

    fake_client = _FakeClient(captured)
    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(slack_breakout_tool, "bind_slack_thread_id", fake_bind)
    monkeypatch.setattr(slack_breakout_tool, "langgraph_client", lambda: fake_client)
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_top_level_message_with_ts", fake_post_top_level
    )
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_thread_reply_with_ts", fake_post_thread_reply
    )
    monkeypatch.setattr(slack_breakout_tool, "dispatch_agent_run", fake_dispatch_agent_run)
    monkeypatch.setattr(slack_breakout_tool, "store_slack_run_mapping", fake_store_mapping)
    monkeypatch.setattr(slack_breakout_tool, "get_langsmith_trace_url", _fake_trace_url)
    monkeypatch.setattr(
        slack_breakout_tool,
        "dashboard_thread_url",
        lambda thread_id: f"https://dashboard.example/agents/{thread_id}",
    )

    result = await slack_breakout_tool.slack_start_new_thread(
        "Investigate follow-up",
        "Use the same repo and investigate the follow-up aspect in detail.",
    )

    expected_thread_id = captured["thread_create"]["thread_id"]
    assert uuid.UUID(expected_thread_id).version == 4
    assert result == {
        "success": True,
        "thread_id": expected_thread_id,
        "thread_ts": new_ts,
        "dashboard_url": f"https://dashboard.example/agents/{expected_thread_id}",
    }
    assert captured["top_level_post"]["channel_id"] == "C1"
    assert captured["top_level_post"]["text"] == (
        "*Open SWE breakout thread:* Investigate follow-up"
    )
    assert captured["top_level_post"]["unfurl_links"] is False
    assert captured["thread_reply"] == {
        "channel_id": "C1",
        "thread_ts": new_ts,
        "text": (
            "*Repository:* `langchain-ai/open-swe`\n\n"
            "*Instructions for the new thread:*\n"
            "Use the same repo and investigate the follow-up aspect in detail."
        ),
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": None,
        "usage": None,
    }
    assert captured["thread_create"]["if_exists"] == "do_nothing"
    assert captured["thread_create"]["thread_id"] == expected_thread_id
    assert captured["binding"]["thread_id"] == expected_thread_id
    assert captured["binding"]["channel_id"] == "C1"
    assert captured["binding"]["thread_ts"] == new_ts
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
    assert dispatch["content"].endswith(
        "## Breakout Instructions\n"
        "Use the same repo and investigate the follow-up aspect in detail."
    )
    assert "slack_reply" not in dispatch["content"]
    assert "trace" not in captured
    assert [item["message_ts"] for item in captured["stored_mappings"]] == [new_ts]
    assert all(item["triggering_user_id"] == "U1" for item in captured["stored_mappings"])


@pytest.mark.parametrize(
    ("visibility", "owner_type", "actor", "background", "allowed"),
    [
        ("public", "user", "bob", False, True),
        ("private", "user", "alice", False, True),
        ("private", "user", "bob", False, False),
        ("public", "system", "bob", False, True),
        ("public", "system", "", False, True),
        ("public", "system", "bob", True, True),
        ("public", "user", "alice", True, False),
        ("private", "user", "alice", True, False),
        ("public", "user", "", False, False),
        ("private", "system", "alice", False, False),
        ("unknown", "user", "alice", False, False),
    ],
)
async def test_breakout_preserves_requester_and_credential_scope(
    monkeypatch: pytest.MonkeyPatch,
    visibility: str,
    owner_type: str,
    actor: str,
    background: bool,
    allowed: bool,
) -> None:
    config = _config()
    config["configurable"].update(
        github_login=actor,
        user_email=f"{actor}@example.com",
        background_task_completion=background,
    )
    saved_slack = dict(config["configurable"]["slack_thread"])
    config["configurable"]["slack_thread"].update(
        triggering_user_id="U2",
        triggering_user_name=actor,
        triggering_user_email=f"{actor}@example.com",
        triggering_event_ts="1700000000.999999",
    )
    metadata = {
        "visibility": visibility,
        "owner_type": owner_type,
        "owner_login": "Alice",
        "source_context": {"slack_thread": saved_slack},
    }
    get_thread = AsyncMock(return_value={"metadata": metadata})
    create = AsyncMock()
    client = SimpleNamespace(
        threads=SimpleNamespace(get=get_thread, create=create, update=AsyncMock())
    )
    post = AsyncMock(return_value=("1700000000.111111", None))
    dispatch = AsyncMock(return_value={"run_id": "run-123"})
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    monkeypatch.setattr(slack_breakout_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(slack_breakout_tool, "post_slack_top_level_message_with_ts", post)
    monkeypatch.setattr(slack_breakout_tool, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(slack_breakout_tool, "bind_slack_thread_id", AsyncMock())
    monkeypatch.setattr(slack_breakout_tool, "store_slack_run_mapping", AsyncMock())
    monkeypatch.setattr(slack_breakout_tool, "get_langsmith_trace_url", _fake_trace_url)
    monkeypatch.setattr(slack_breakout_tool, "dispatch_agent_run", dispatch)

    result = await slack_breakout_tool.slack_start_new_thread("Title", "Instructions")

    assert result["success"] is allowed
    if not allowed:
        post.assert_not_awaited()
        create.assert_not_awaited()
        dispatch.assert_not_awaited()
        return
    child_metadata = create.call_args.kwargs["metadata"]
    child_config = dispatch.call_args.args[2]
    child_config["thread_id"] = result["thread_id"]
    assert child_metadata["visibility"] == visibility
    assert child_metadata["source_context"]["slack_thread"]["triggering_user_id"] == "U2"
    assert child_metadata["source_context"]["slack_thread"]["triggering_user_name"] == actor
    assert child_metadata["source_context"]["breakout_from"]["message_ts"] == "1700000000.999999"
    get_thread.return_value = {"metadata": child_metadata}
    monkeypatch.setattr("langgraph_sdk.get_client", lambda: client)
    monkeypatch.setattr("agent.run_config.get_config", lambda: {"configurable": child_config})
    user_owned = owner_type == "user" or bool(actor and not background)
    assert child_metadata["owner_type"] == ("user" if user_owned else "system")
    assert await pr_author_login() == (actor if user_owned else None)
    assert await private_credential_login() == (actor if visibility == "private" else None)
    if user_owned:
        assert child_metadata["owner_login"] == actor
        assert await pr_author_login(actor) == actor
        if visibility == "public":
            with pytest.raises(PrAuthorNotAParticipant):
                await pr_author_login("alice")
    else:
        assert "owner_login" not in child_metadata
        assert await pr_author_login("alice") is None


async def test_breakout_rejects_unreadable_parent_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    get_thread = AsyncMock(side_effect=RuntimeError("store unavailable"))
    client = SimpleNamespace(threads=SimpleNamespace(get=get_thread))
    post = AsyncMock()
    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(slack_breakout_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(slack_breakout_tool, "post_slack_top_level_message_with_ts", post)

    with pytest.raises(RuntimeError, match="store unavailable"):
        await slack_breakout_tool.slack_start_new_thread("Title", "Instructions")
    post.assert_not_awaited()


async def test_slack_start_new_thread_requires_slack_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.run_config.get_config", lambda: {"configurable": {}})

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
    monkeypatch.setattr("agent.run_config.get_config", _config)

    result = await slack_breakout_tool.slack_start_new_thread(title, instructions)

    assert result["success"] is False
    assert result["error"] == error


async def test_slack_start_new_thread_rejects_invalid_repo_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.run_config.get_config", _config)

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

    monkeypatch.setattr("agent.run_config.get_config", _config)
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


async def test_slack_start_new_thread_returns_detail_failure_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {"dispatched": False, "detail_posts": 0, "sleeps": []}

    async def fake_post_top_level(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        return "1700000000.111111", None

    async def fake_post_thread_reply(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        captured["detail_posts"] += 1
        return None, "rate_limited: 30"

    async def fake_sleep(delay: float) -> None:
        captured["sleeps"].append(delay)

    async def fake_dispatch_agent_run(*args: Any, **kwargs: Any) -> dict[str, str]:
        captured["dispatched"] = True
        return {"run_id": "run-123"}

    monkeypatch.setattr("agent.run_config.get_config", _config)
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_top_level_message_with_ts", fake_post_top_level
    )
    monkeypatch.setattr(
        slack_breakout_tool, "post_slack_thread_reply_with_ts", fake_post_thread_reply
    )
    monkeypatch.setattr(slack_breakout_tool, "dispatch_agent_run", fake_dispatch_agent_run)
    monkeypatch.setattr(slack_breakout_tool.asyncio, "sleep", fake_sleep)

    result = await slack_breakout_tool.slack_start_new_thread("Title", "Instructions")

    assert result["success"] is False
    assert result["error"] == "rate_limited: 30"
    assert result["slack_error"] == "rate_limited: 30"
    assert "30s" in result["hint"]
    assert captured["detail_posts"] == 2
    assert captured["sleeps"] == [30]
    assert captured["dispatched"] is False
