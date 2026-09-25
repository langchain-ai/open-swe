import importlib
import json
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.slack.payloads import SlackBlockAction

slack_reply_tool = importlib.import_module("agent.slack.tools.reply")


@pytest.fixture(autouse=True)
def _patch_slack_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def mutation_lock(*_args: Any):
        yield

    monkeypatch.setattr(slack_reply_tool, "slack_thread_mutation_lock", mutation_lock)
    monkeypatch.setattr(slack_reply_tool, "restore_slack_thinking_status", AsyncMock())


def _config() -> dict[str, Any]:
    return {
        "configurable": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.0",
            }
        }
    }


@pytest.mark.parametrize(
    "options,stores_mapping",
    [
        (None, True),
        (["Yes", "No"], True),
    ],
)
async def test_reply_records_mapping_with_or_without_pending_choices(
    monkeypatch: pytest.MonkeyPatch,
    options: list[str] | None,
    stores_mapping: bool,
) -> None:
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(
        slack_reply_tool,
        "get_active_slack_thread",
        AsyncMock(
            return_value={
                "channel_id": "C1",
                "thread_ts": "1.0",
            }
        ),
    )
    monkeypatch.setattr(
        slack_reply_tool, "post_slack_thread_reply_with_ts", AsyncMock(return_value=("2.0", None))
    )
    mapping = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "store_slack_message_run_mapping", mapping)
    assert await slack_reply_tool.slack_reply("The answer", "final", options=options) == {
        "success": True
    }
    assert mapping.await_count == int(stores_mapping)


@pytest.mark.parametrize("background_task_completion", [False, True])
@pytest.mark.parametrize("thread_ts", ["1.0", "0"])
async def test_reply_restores_status_including_completion_runs(
    monkeypatch: pytest.MonkeyPatch,
    background_task_completion: bool,
    thread_ts: str,
) -> None:
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "run_id": "run-1",
            "configurable": {"background_task_completion": background_task_completion},
        },
    )
    monkeypatch.setattr(
        slack_reply_tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": thread_ts}),
    )
    post = AsyncMock(return_value=("2.0", None))
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)
    thinking_status = AsyncMock()
    session_status = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "restore_slack_thinking_status", thinking_status)
    monkeypatch.setattr(slack_reply_tool, "restore_slack_session_status", session_status)

    assert await slack_reply_tool.slack_reply("The answer", "final") == {"success": True}
    post.assert_awaited_once()
    assert thinking_status.await_count == int(thread_ts != "0")
    assert session_status.await_count == int(thread_ts == "0")


async def test_kickoff_stays_until_a_subsequent_reply_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.slack.test_slack_thread_mapping import _Client

    client = _Client()
    monkeypatch.setattr(slack_reply_tool, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-one",
                "source": "slack",
                "slack_kickoff_eligible": True,
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    posts = iter([("1.1", None), (None, "rate_limited"), ("1.2", None), ("1.3", None)])
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", AsyncMock(side_effect=posts))
    deleted = AsyncMock()

    @asynccontextmanager
    async def bot():
        yield type("Slack", (), {"chat_delete": deleted})()

    monkeypatch.setattr(slack_reply_tool.SlackClient, "bot", bot)
    assert await slack_reply_tool.slack_reply("Investigating", "progress") == {"success": True}
    assert await slack_reply_tool.slack_reply("Update", "final") == {
        "success": False,
        "error": "rate_limited",
        "slack_error": "rate_limited",
        "message_chars": 6,
        "hint": slack_reply_tool._slack_reply_failure_hint("rate_limited"),
    }
    deleted.assert_not_awaited()
    assert await slack_reply_tool.slack_reply("Update", "progress") == {"success": True}
    deleted.assert_awaited_once_with(channel="C1", ts="1.1")
    assert await slack_reply_tool.slack_reply("Done", "final") == {"success": True}
    deleted.assert_awaited_once()


async def test_kickoff_delete_failure_does_not_interrupt_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.slack.test_slack_thread_mapping import _Client

    client = _Client()
    await client.store.put_item(("slack_kickoff", "C1"), "1.0", {"kickoff_ts": "1.1"})
    monkeypatch.setattr(slack_reply_tool, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-one",
                "source": "slack",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    monkeypatch.setattr(
        slack_reply_tool, "_post_and_store_mapping", AsyncMock(return_value=("1.2", None))
    )

    deleted = AsyncMock(side_effect=[TimeoutError("failed"), None])

    @asynccontextmanager
    async def bot():
        yield type("Slack", (), {"chat_delete": deleted})()

    monkeypatch.setattr(slack_reply_tool.SlackClient, "bot", bot)
    assert await slack_reply_tool.slack_reply("Update", "final") == {"success": True}
    assert (await client.store.get_item(("slack_kickoff", "C1"), "1.0"))["value"] == {
        "kickoff_ts": "1.1"
    }
    assert await slack_reply_tool.slack_reply("Another update", "final") == {"success": True}
    assert deleted.await_count == 2
    assert (await client.store.get_item(("slack_kickoff", "C1"), "1.0"))["value"] == {
        "removed": True
    }


async def test_slack_reply_holds_mutation_lock_while_posting(
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

    assert await slack_reply_tool.slack_reply("hello", "final") == {"success": True}
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

    assert await slack_reply_tool.slack_reply("threaded", "final") == {"success": True}


async def test_slack_reply_returns_structured_error_for_msg_too_long(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result == {
        "success": False,
        "error": "msg_too_long",
        "slack_error": "msg_too_long",
        "message_chars": 5,
        "hint": "Slack rejected the message as too long; retry with a shorter message.",
    }


@pytest.mark.parametrize("slack_error", ["channel_not_found", "not_in_channel"])
async def test_slack_reply_hints_not_to_retry_channel_errors(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result["success"] is False
    assert result["error"] == slack_error
    assert result["slack_error"] == slack_error
    assert result["message_chars"] == 5
    assert "do not retry" in result["hint"]
    assert "trace output" in result["hint"]


async def test_slack_reply_rate_limited_hint_includes_retry_after(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result["success"] is False
    assert result["error"] == "rate_limited: 30"
    assert result["slack_error"] == "rate_limited: 30"
    assert "30s" in result["hint"]
    assert "wait" in result["hint"]


async def test_slack_reply_rate_limited_hint_without_retry_after(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result["success"] is False
    assert result["slack_error"] == "rate_limited"
    assert "wait" in result["hint"]


async def test_slack_reply_uses_post_failed_without_slack_error(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result["success"] is False
    assert result["error"] == "post failed"
    assert result["slack_error"] is None
    assert result["message_chars"] == 5


async def test_slack_reply_passes_executing_run_id(
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

    result = await slack_reply_tool.slack_reply("hello", "final")

    assert result == {"success": True}
    assert captured["run_id"] == "12345678-1234-5678-1234-567812345678"
    assert captured["triggering_user_id"] == "active-user"


@pytest.mark.parametrize("response_type", ["progress", "final"])
@pytest.mark.parametrize("options", [None, ["Yes", "No"]])
async def test_only_final_reply_has_feedback_for_its_run(
    monkeypatch: pytest.MonkeyPatch,
    response_type: Literal["progress", "final"],
    options: list[str] | None,
) -> None:
    config = _config()
    config["run_id"] = "run-1"
    config["configurable"]["slack_thread"]["triggering_user_id"] = "U1"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    post = AsyncMock(return_value=("2.0", None))
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_reply("Answer", response_type, options=options) == {
        "success": True
    }
    assert post.await_args is not None
    blocks = post.await_args.kwargs["blocks"]
    feedback = [block for block in blocks if block["type"] == "context_actions"]
    if response_type == "progress":
        assert feedback == []
    else:
        buttons = feedback[0]["elements"][0]
        assert buttons["type"] == "feedback_buttons"
        assert json.loads(buttons["positive_button"]["value"]) == {
            "run_id": "run-1",
            "rating": "up",
        }
        assert json.loads(buttons["negative_button"]["value"]) == {
            "run_id": "run-1",
            "rating": "down",
        }
    assert blocks[0] == {"type": "markdown", "text": "Answer"}
    if options:
        assert blocks[1]["type"] == "actions"


async def test_long_reply_retains_all_text_alongside_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config["run_id"] = "run-1"
    config["configurable"]["slack_thread"]["triggering_user_id"] = "U1"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    post = AsyncMock(return_value=("2.0", None))
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_reply("x" * 12001, "final") == {"success": True}
    assert post.await_args is not None
    blocks = post.await_args.kwargs["blocks"]
    assert "".join(block["text"]["text"] for block in blocks[:-1]) == "x" * 12001
    assert all(len(block["text"]["text"]) <= 3000 for block in blocks[:-1])
    assert blocks[-1]["type"] == "context_actions"


async def test_custom_blocks_preserved_when_feedback_is_appended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config["run_id"] = "run-1"
    config["configurable"]["slack_thread"]["triggering_user_id"] = "U1"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    post = AsyncMock(return_value=("2.0", None))
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Answer"}}]

    assert await slack_reply_tool.slack_reply("Answer", "final", blocks=blocks) == {"success": True}
    assert post.await_args is not None
    assert post.await_args.kwargs["blocks"][:-1] == blocks
    assert len(blocks) == 1


async def test_slack_reply_restores_thinking_status_after_interim_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = UUID("12345678-1234-5678-1234-567812345678")
    config = _config()
    config["run_id"] = run_id
    restore_status = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    monkeypatch.setattr(
        slack_reply_tool, "_post_and_store_mapping", AsyncMock(return_value=("2.0", None))
    )
    monkeypatch.setattr(slack_reply_tool, "restore_slack_thinking_status", restore_status)

    assert await slack_reply_tool.slack_reply("Still working", "final") == {"success": True}
    restore_status.assert_awaited_once_with("C1", "1.0")


async def test_slack_reply_posts_native_markdown_without_options(
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

    message = '```python\nfor value in range(3):\n    print("@Name(U123)", value)\n```'
    result = await slack_reply_tool.slack_reply(message, "final")

    assert result == {"success": True}
    assert captured["message"] == message
    assert captured["blocks"] == [{"type": "markdown", "text": message}]


async def test_slack_reply_falls_back_to_mrkdwn_over_native_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=("2.0", None))
    message = "# Heading\n\n" + "x" * 12000
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_reply(message, "final") == {"success": True}
    assert post.await_args.args[2].startswith("*Heading*\n")
    assert post.await_args.kwargs["blocks"] is None


async def test_slack_reply_rejects_oversized_message_with_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    result = await slack_reply_tool.slack_reply("x" * 12001, "final", options=["Yes"])

    assert result["success"] is False
    assert result["retry"] is True
    assert "options" in result["error"]
    post.assert_not_awaited()


async def test_slack_reply_preserves_explicit_blocks_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=("2.0", None))
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "kept"}}]
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_reply("x" * 12001, "final", blocks=blocks) == {
        "success": True
    }
    assert post.await_args.kwargs["blocks"] is blocks


async def test_slack_reply_builds_option_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = await slack_reply_tool.slack_reply("Pick one", "final", options=["A", "B"])

    assert result == {"success": True}
    assert captured["channel_id"] == "C1"
    assert captured["thread_ts"] == "1.0"
    assert captured["message"] == "Pick one"
    assert captured["blocks"][0] == {"type": "markdown", "text": "Pick one"}
    actions = captured["blocks"][1]
    assert actions["type"] == "actions"
    assert [button["text"]["text"] for button in actions["elements"]] == ["A", "B"]
    action_ids = [button["action_id"] for button in actions["elements"]]
    assert action_ids == ["open_swe_option_select_0", "open_swe_option_select_1"]
    assert len(action_ids) == len(set(action_ids))


@pytest.mark.parametrize(
    "plan_mode",
    [False, True],
)
async def test_legacy_plan_config_uses_ordinary_option_buttons(
    monkeypatch: pytest.MonkeyPatch,
    plan_mode: bool,
) -> None:
    config = _config()
    config["configurable"].update(thread_id="thread-1", plan_mode=plan_mode)
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    monkeypatch.setattr(
        slack_reply_tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "0"}),
    )
    post = AsyncMock(return_value=("2.0", None))
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    await slack_reply_tool.slack_reply(
        "Review",
        "final",
        options=["Approve & implement", "Request changes"],
        state={},
    )

    buttons = post.await_args.kwargs["blocks"][1]["elements"]
    values = [json.loads(button["value"]) for button in buttons]
    assert values == [
        {"type": "open_swe_option", "response": label}
        for label in ["Approve & implement", "Request changes"]
    ]


def test_slack_action_ids_are_unique_and_recognized() -> None:
    slack_routes = importlib.import_module("agent.slack.routes")
    blocks = slack_reply_tool.build_workflow_approval_blocks("Review", "abc")
    actions = blocks[1]["elements"]

    assert len({action["action_id"] for action in actions}) == len(actions)
    parsed = [SlackBlockAction.model_validate(action) for action in actions]
    assert slack_routes._first_option_action(parsed) is parsed[0]
    legacy = SlackBlockAction(action_id="open_swe_option_select")
    assert slack_routes._first_option_action([legacy]) is legacy
    assert slack_routes._first_option_action([SlackBlockAction(action_id="unrelated")]) is None


async def test_slack_reply_passes_live_run_id(
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

    assert await slack_reply_tool.slack_reply("Done", "final") == {"success": True}
    assert captured["run_id"] == str(run_id)


async def test_slack_reply_passes_model_reported_usage(
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
    config["configurable"]["resolved_agent_effort"] = "high"
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
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

    result = await slack_reply_tool.slack_reply("Done", "final", state=state)

    assert result == {"success": True}
    usage = captured["usage"]
    assert usage.models == ("model-a",)
    assert usage.reasoning_effort == "high"
    assert usage.total_tokens == 110


async def test_slack_reply_uses_selected_route_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def post(*_args: Any, **kwargs: Any) -> tuple[str, None]:
        captured.update(kwargs)
        return "2.0", None

    config = _config()
    config["configurable"].update(
        resolved_agent_model_id="model-balanced",
        resolved_agent_effort="high",
    )
    monkeypatch.setattr(slack_reply_tool, "get_config", lambda: config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)
    state = {
        "model_route": "balanced",
        "messages": [
            HumanMessage(content="request"),
            AIMessage(content="answer", response_metadata={"model_name": "model-balanced"}),
        ],
    }

    assert await slack_reply_tool.slack_reply("Done", "final", state=state) == {"success": True}
    assert captured["usage"].reasoning_effort == "high"


async def test_reply_moves_the_thread_to_the_dashboard_when_its_slack_thread_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "T1",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    monkeypatch.setattr(
        slack_reply_tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "1.0"}),
    )
    monkeypatch.setattr(
        slack_reply_tool,
        "post_slack_thread_reply_with_ts",
        AsyncMock(return_value=(None, "thread_not_found")),
    )
    moved = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply_tool, "move_thread_to_dashboard", moved)

    result = await slack_reply_tool.slack_reply("The answer", "final")

    assert result["moved_to_dashboard"] is True
    assert result["retry"] is False
    assert moved.await_args.args[1:] == ("T1", "C1", "1.0")


async def test_reply_stops_calling_slack_once_the_thread_lives_in_the_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "T1",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    monkeypatch.setattr(slack_reply_tool, "get_active_slack_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_reply_tool, "_already_moved_to_dashboard", AsyncMock(return_value=True)
    )
    post = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "post_slack_thread_reply_with_ts", post)

    assert (await slack_reply_tool.slack_reply("The answer", "final"))["moved_to_dashboard"] is True
    post.assert_not_awaited()


async def test_reply_does_not_claim_a_handoff_when_detaching_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_reply_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "T1",
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
            }
        },
    )
    monkeypatch.setattr(
        slack_reply_tool,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "1.0"}),
    )
    monkeypatch.setattr(
        slack_reply_tool,
        "post_slack_thread_reply_with_ts",
        AsyncMock(return_value=(None, "thread_not_found")),
    )
    monkeypatch.setattr(slack_reply_tool, "move_thread_to_dashboard", AsyncMock(return_value=False))

    result = await slack_reply_tool.slack_reply("The answer", "final")

    assert result["moved_to_dashboard"] is False
    assert result["retry"] is True
