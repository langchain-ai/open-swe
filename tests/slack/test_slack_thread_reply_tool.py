import importlib
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.utils.slack import SlackThreadTranscript

slack_reply_tool = importlib.import_module("agent.tools.slack_thread_reply")


@pytest.fixture(autouse=True)
def _current_thread_version(monkeypatch: pytest.MonkeyPatch) -> None:
    async def current_version(*_args: Any) -> int:
        return 1

    @asynccontextmanager
    async def mutation_lock(*_args: Any):
        yield

    monkeypatch.setattr(slack_reply_tool, "get_slack_thread_version", current_version)
    monkeypatch.setattr(slack_reply_tool, "slack_thread_mutation_lock", mutation_lock)


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
            }
        }
    }


async def test_slack_thread_reply_rejects_stale_thread_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_posted(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        pytest.fail("stale reply must not be posted")

    async def fetch_thread(*_args: Any) -> SlackThreadTranscript:
        return SlackThreadTranscript(
            formatted="Alice: wait, one more thing", count=1, truncated=False
        )

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fail_if_posted)
    monkeypatch.setattr(slack_reply_tool, "fetch_and_format_slack_thread", fetch_thread)

    result = await slack_reply_tool.slack_thread_reply("hello", 0)

    assert result["success"] is False
    assert result["posted"] is False
    assert result["error"] == "Slack thread version mismatch"
    assert result["thread_version"] == 1
    assert result["provided_thread_version"] == 0
    assert result["thread_messages"] == "Alice: wait, one more thing"
    assert "NOT posted" in result["hint"]
    assert "thread_version=1" in result["hint"]
    assert "slack_read_thread_messages" in result["hint"]
    assert "new input to the run" in result["hint"]


async def test_slack_thread_reply_stale_version_without_fetchable_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_posted(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        pytest.fail("stale reply must not be posted")

    async def fetch_thread(*_args: Any) -> None:
        return None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fail_if_posted)
    monkeypatch.setattr(slack_reply_tool, "fetch_and_format_slack_thread", fetch_thread)

    result = await slack_reply_tool.slack_thread_reply("hello", 0)

    assert "thread_messages" not in result
    assert result["thread_version"] == 1
    assert "Call slack_read_thread_messages to see them." in result["hint"]


async def test_slack_thread_reply_holds_mutation_lock_while_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_held = False

    @asynccontextmanager
    async def mutation_lock(*_args: Any):
        nonlocal lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    async def post(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        assert lock_held is True
        return "2.0", None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "slack_thread_mutation_lock", mutation_lock)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_thread_reply("hello", 1) == {
        "success": True,
        "thread_version": 1,
    }
    assert lock_held is False


async def test_code_channel_reply_stays_in_user_started_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def active_thread(_client: Any, thread_id: str, _fallback: Any) -> dict[str, str]:
        assert thread_id == "thread-code"
        return {"channel_id": "C-code", "thread_ts": "0"}

    async def post(
        _channel_id: str, _thread_ts: str, _message: str, **kwargs: Any
    ) -> tuple[str | None, str | None]:
        assert kwargs["post_thread_ts"] == "9.000"
        return "10.000", None

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
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_thread_reply("threaded", 1) == {
        "success": True,
        "thread_version": 1,
    }


async def test_slack_thread_reply_returns_structured_error_for_msg_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        return None, "msg_too_long"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result == {
        "success": False,
        "error": "msg_too_long",
        "slack_error": "msg_too_long",
        "message_chars": 5,
        "hint": "Slack rejected the message as too long; retry with a shorter message.",
    }


@pytest.mark.parametrize("slack_error", ["channel_not_found", "not_in_channel"])
async def test_slack_thread_reply_hints_not_to_retry_channel_errors(
    slack_error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        return None, slack_error

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result["success"] is False
    assert result["error"] == slack_error
    assert result["slack_error"] == slack_error
    assert result["message_chars"] == 5
    assert "do not retry" in result["hint"]
    assert "trace output" in result["hint"]


async def test_slack_thread_reply_rate_limited_hint_includes_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        return None, "rate_limited: 30"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result["success"] is False
    assert result["error"] == "rate_limited: 30"
    assert result["slack_error"] == "rate_limited: 30"
    assert "30s" in result["hint"]
    assert "wait" in result["hint"]


async def test_slack_thread_reply_rate_limited_hint_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        return None, "rate_limited"

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result["success"] is False
    assert result["slack_error"] == "rate_limited"
    assert "wait" in result["hint"]


async def test_slack_thread_reply_uses_post_failed_without_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        return None, None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result["success"] is False
    assert result["error"] == "post failed"
    assert result["slack_error"] is None
    assert result["message_chars"] == 5


async def test_slack_thread_reply_passes_executing_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured.update(kwargs)
        return "2.0", None

    config = _config()
    config["run_id"] = UUID("12345678-1234-5678-1234-567812345678")
    config["configurable"]["slack_thread"]["triggering_user_id"] = "active-user"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("hello", 1)

    assert result == {"success": True, "thread_version": 1}
    assert captured["run_id"] == "12345678-1234-5678-1234-567812345678"
    assert captured["triggering_user_id"] == "active-user"


async def test_slack_thread_reply_posts_plain_text_without_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured.update(message=message, blocks=blocks)
        return "2.0", None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply(
        "Plan ready: review it and reply to approve or request changes.", 1
    )

    assert result == {"success": True, "thread_version": 1}
    assert captured["blocks"] is None


async def test_slack_thread_reply_builds_option_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured.update(
            {"channel_id": channel_id, "thread_ts": thread_ts, "message": message, "blocks": blocks}
        )
        return "2.0", None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    result = await slack_reply_tool.slack_thread_reply("Pick one", 1, options=["A", "B"])

    assert result == {"success": True, "thread_version": 1}
    assert captured["channel_id"] == "C1"
    assert captured["thread_ts"] == "1.0"
    assert captured["message"] == "Pick one"
    actions = captured["blocks"][1]
    assert actions["type"] == "actions"
    assert [button["text"]["text"] for button in actions["elements"]] == ["A", "B"]
    action_ids = [button["action_id"] for button in actions["elements"]]
    assert action_ids == ["open_swe_option_select_0", "open_swe_option_select_1"]
    assert len(action_ids) == len(set(action_ids))

    plan_blocks = slack_reply_tool._build_option_blocks(
        "Review", ["Approve & implement", "Request changes"]
    )
    assert [json.loads(button["value"]) for button in plan_blocks[1]["elements"]] == [
        {"type": "plan_approval", "action": "approve"},
        {"type": "plan_approval", "action": "revise"},
    ]


def test_slack_action_ids_are_unique_and_recognized() -> None:
    slack_routes = importlib.import_module("agent.webhooks.slack_routes")
    blocks = slack_reply_tool.build_workflow_approval_blocks("Review", "abc")
    actions = blocks[1]["elements"]

    assert len({action["action_id"] for action in actions}) == len(actions)
    assert slack_routes._first_open_swe_option_action(actions) is actions[0]
    legacy = {"action_id": "open_swe_option_select"}
    assert slack_routes._first_open_swe_option_action([legacy]) is legacy
    assert slack_routes._first_open_swe_option_action([{"action_id": "unrelated"}]) is None


async def test_slack_thread_reply_passes_live_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured.update(kwargs)
        return "2.0", None

    run_id = UUID("35d1f7e7-c811-43f2-91a6-9d729430b4ea")
    config = _config()
    config["run_id"] = run_id
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)

    assert await slack_reply_tool.slack_thread_reply("Done", 1) == {
        "success": True,
        "thread_version": 1,
    }
    assert captured["run_id"] == str(run_id)


async def test_slack_thread_reply_passes_model_reported_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_post_and_store_mapping(
        channel_id: str,
        thread_ts: str,
        message: str,
        **kwargs: Any,
    ) -> tuple[str | None, str | None]:
        captured.update(kwargs)
        return "2.0", None

    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", fake_post_and_store_mapping)
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

    result = await slack_reply_tool.slack_thread_reply("Done", 1, state=state)

    assert result == {"success": True, "thread_version": 1}
    usage = captured["usage"]
    assert usage.models == ("model-a",)
    assert usage.main_agent_tokens == 110
