import importlib
from typing import Any
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage

slack_reply_tool = importlib.import_module("agent.slack.tools.thread_reply")


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
            }
        }
    }


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def post(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "message_ts": "2.0"}

    monkeypatch.setattr(slack_reply_tool, "post_session_message", post)
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    return captured


async def test_slack_thread_reply_posts_in_its_own_thread(posted: dict[str, Any]) -> None:
    assert await slack_reply_tool.slack_thread_reply("hello") == {"success": True}
    assert posted["channel_id"] == "C1"
    assert posted["thread_ts"] == "1.0"
    assert posted["post_thread_ts"] == "1.0"
    assert posted["message"] == "hello"
    assert posted["blocks"] is None
    assert posted["agent_thread_id"] is None


async def test_code_channel_reply_stays_in_user_started_thread(
    monkeypatch: pytest.MonkeyPatch, posted: dict[str, Any]
) -> None:
    async def active_thread(_client: Any, thread_id: str, _fallback: Any) -> dict[str, str]:
        assert thread_id == "thread-code"
        return {"channel_id": "C-code", "thread_ts": "0"}

    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-code",
                "slack_thread": {
                    "channel_id": "C-code",
                    "thread_ts": "0",
                    "reply_thread_ts": "9.000",
                },
            }
        },
    )
    monkeypatch.setattr(slack_reply_tool, "get_active_slack_thread", active_thread)

    assert await slack_reply_tool.slack_thread_reply("threaded") == {"success": True}
    assert posted["post_thread_ts"] == "9.000"
    # A code channel is the session, so its transcript needs no per-thread web link.
    assert posted["agent_thread_id"] is None


async def test_slack_thread_reply_surfaces_a_failed_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = {
        "success": False,
        "error": "msg_too_long",
        "slack_error": "msg_too_long",
        "message_chars": 5,
        "hint": "Slack rejected the message as too long; retry with a shorter message.",
    }

    async def post(**_kwargs: Any) -> dict[str, Any]:
        return failure

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "post_session_message", post)

    assert await slack_reply_tool.slack_thread_reply("hello") == failure


async def test_slack_thread_reply_rejects_an_empty_message(
    monkeypatch: pytest.MonkeyPatch, posted: dict[str, Any]
) -> None:
    result = await slack_reply_tool.slack_thread_reply("   ")

    assert result == {"success": False, "error": "Message cannot be empty"}
    assert posted == {}


async def test_slack_thread_reply_builds_option_blocks(posted: dict[str, Any]) -> None:
    assert await slack_reply_tool.slack_thread_reply("Pick one", options=["A", "B"]) == {
        "success": True
    }

    actions = posted["blocks"][1]
    assert actions["type"] == "actions"
    assert [button["text"]["text"] for button in actions["elements"]] == ["A", "B"]


async def test_slack_thread_reply_passes_the_executing_run_and_user(
    monkeypatch: pytest.MonkeyPatch, posted: dict[str, Any]
) -> None:
    config = _config()
    config["run_id"] = UUID("12345678-1234-5678-1234-567812345678")
    config["configurable"]["slack_thread"]["triggering_user_id"] = "active-user"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)

    assert await slack_reply_tool.slack_thread_reply("hello") == {"success": True}
    assert posted["run_id"] == "12345678-1234-5678-1234-567812345678"
    assert posted["triggering_user"] == "active-user"


async def test_slack_thread_reply_passes_model_reported_usage(posted: dict[str, Any]) -> None:
    state = {
        "messages": [
            HumanMessage(content="request"),
            AIMessage(
                content="",
                response_metadata={"model_name": "model-a"},
                usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            ),
        ]
    }

    assert await slack_reply_tool.slack_thread_reply("Done", state=state) == {"success": True}
    assert posted["usage"].models == ("model-a",)
    assert posted["usage"].main_agent_tokens == 110
