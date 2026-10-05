import importlib
import json
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent.tools.errors import ToolError

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


@pytest.mark.parametrize("breakout", [False, True])
async def test_kickoff_stays_until_a_subsequent_reply_posts(
    monkeypatch: pytest.MonkeyPatch,
    breakout: bool,
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
                "slack_breakout": breakout,
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
    with pytest.raises(ToolError):
        await slack_reply_tool.slack_reply("Update", "final")
    deleted.assert_not_awaited()
    assert await slack_reply_tool.slack_reply("Update", "progress") == {"success": True}
    assert await slack_reply_tool.slack_reply("Done", "final") == {"success": True}
    if breakout:
        deleted.assert_not_awaited()
    else:
        deleted.assert_awaited_once_with(channel="C1", ts="1.1")


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


async def test_freshness_conflicts_survive_offloaded_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import HumanMessage, ToolMessage

    from tests.slack.test_slack_thread_mapping import _Client

    client = _Client()
    monkeypatch.setattr(slack_reply_tool, "get_langgraph_client", lambda: client)
    latest = {"human_timestamps": ["1.0", "2.0"], "formatted": "new request"}
    monkeypatch.setattr(slack_reply_tool, "fetch_and_format_thread", AsyncMock(return_value=latest))
    messages = [HumanMessage(content='<input-message timestamp="1.0">request</input-message>')]
    with pytest.raises(ToolError, match="new_slack_messages") as raised:
        await slack_reply_tool._stale_reply_guard({"messages": messages}, "C1", "1.0", "run-1")
    assert raised.value.details["formatted"] == "new request"
    messages.append(
        ToolMessage(content="Result offloaded to /large_tool_results/reply", tool_call_id="a")
    )
    assert (
        await slack_reply_tool._stale_reply_guard({"messages": messages}, "C1", "1.0", "run-1")
        is None
    )
    latest["human_timestamps"] = ["1.0", "2.0", "3.0"]
    with pytest.raises(ToolError, match="new_slack_messages"):
        await slack_reply_tool._stale_reply_guard({"messages": messages}, "C1", "1.0", "run-1")
    latest["human_timestamps"] = ["1.0", "2.0", "3.0", "4.0"]
    assert (
        await slack_reply_tool._stale_reply_guard({"messages": messages}, "C1", "1.0", "run-1")
        is None
    )


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

    with pytest.raises(ToolError) as raised:
        await slack_reply_tool.slack_reply("hello", "final")

    assert str(raised.value) == slack_error
    assert raised.value.details["slack_error"] == slack_error
    assert raised.value.details["message_chars"] == 5
    assert "do not retry" in raised.value.details["hint"]
    assert "trace output" in raised.value.details["hint"]


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


async def test_slack_reply_keeps_code_highlighted_over_native_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=("2.0", None))
    message = "# Heading\n\n" + "x" * 12000 + "\n\n```diff\n-old\n+new\n```\n\nafter"
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    assert await slack_reply_tool.slack_reply(message, "progress") == {"success": True}
    assert post.await_args.args[2].startswith("*Heading*\n")
    blocks = post.await_args.kwargs["blocks"]
    [code] = [block for block in blocks if block["type"] == "rich_text"]
    assert code["elements"][0] == {
        "type": "rich_text_preformatted",
        "elements": [{"type": "text", "text": "-old\n+new"}],
        "language": "diff",
    }
    assert blocks[0]["text"]["text"] == "*Heading*"
    assert blocks[-1]["text"]["text"] == "after"


async def test_slack_reply_rejects_oversized_message_with_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock()
    monkeypatch.setattr(slack_reply_tool, "get_config", _config)
    monkeypatch.setattr(slack_reply_tool, "_post_and_store_mapping", post)

    with pytest.raises(ToolError) as raised:
        await slack_reply_tool.slack_reply("x" * 12001, "final", options=["Yes"])

    assert raised.value.details["retry"] is True
    assert "options" in str(raised.value)
    post.assert_not_awaited()


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

    with pytest.raises(ToolError) as raised:
        await slack_reply_tool.slack_reply("The answer", "final")

    assert raised.value.details["moved_to_dashboard"] is True
    assert raised.value.details["retry"] is False
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

    with pytest.raises(ToolError):
        await slack_reply_tool.slack_reply("The answer", "final")
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

    with pytest.raises(ToolError) as raised:
        await slack_reply_tool.slack_reply("The answer", "final")

    assert raised.value.details["moved_to_dashboard"] is False
    assert raised.value.details["retry"] is True
