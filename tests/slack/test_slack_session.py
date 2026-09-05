import importlib
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

session = importlib.import_module("agent.slack.session")


@pytest.fixture(autouse=True)
def _client_and_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def mutation_lock(*_args: Any, **_kwargs: Any):
        yield

    monkeypatch.setattr(session, "slack_thread_mutation_lock", mutation_lock)
    monkeypatch.setattr(session, "get_langgraph_client", lambda: object())
    monkeypatch.setattr(session, "convert_mentions_to_slack_format", lambda text: text)


async def _post(**overrides: Any) -> dict[str, Any]:
    return await session.post_session_message(
        **{
            "channel_id": "C1",
            "thread_ts": "1.0",
            "post_thread_ts": "1.0",
            "message": "hello",
            **overrides,
        }
    )


async def test_a_posted_message_is_mapped_to_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    mapped: dict[str, Any] = {}

    async def post(*args: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        return "2.0", None

    async def store(_client: Any, channel_id: str, thread_ts: str, message_ts: str, **kwargs: Any):
        mapped.update(channel_id=channel_id, thread_ts=thread_ts, message_ts=message_ts, **kwargs)

    monkeypatch.setattr(session, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(session, "store_slack_message_run_mapping", store)

    assert await _post(run_id="run-1", triggering_user="U1") == {
        "success": True,
        "message_ts": "2.0",
    }
    assert mapped == {
        "channel_id": "C1",
        "thread_ts": "1.0",
        "message_ts": "2.0",
        "run_id": "run-1",
        "triggering_user_id": "U1",
    }


async def test_posting_holds_the_thread_mutation_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    held = False

    @asynccontextmanager
    async def mutation_lock(*_args: Any, **_kwargs: Any):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    async def post(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        assert held is True
        return "2.0", None

    monkeypatch.setattr(session, "slack_thread_mutation_lock", mutation_lock)
    monkeypatch.setattr(session, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(session, "store_slack_message_run_mapping", _noop)

    assert (await _post())["success"] is True
    assert held is False


@pytest.mark.parametrize(
    ("slack_error", "expected"),
    [
        ("msg_too_long", "too long"),
        ("channel_not_found", "do not retry"),
        ("not_in_channel", "do not retry"),
        ("rate_limited: 30", "30s"),
        ("rate_limited", "wait"),
        ("missing_slack_bot_token", "do not retry"),
        ("http_error: Timeout", "retry once"),
        (None, "retry once"),
    ],
)
async def test_a_failed_post_says_what_to_do_about_it(
    monkeypatch: pytest.MonkeyPatch, slack_error: str | None, expected: str
) -> None:
    async def post(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        return None, slack_error

    monkeypatch.setattr(session, "post_slack_thread_reply_with_ts", post)

    result = await _post()

    assert result["success"] is False
    assert result["error"] == (slack_error or "post failed")
    assert result["slack_error"] == slack_error
    assert result["message_chars"] == 5
    assert expected in result["hint"]


def test_plan_options_carry_the_approval_actions() -> None:
    blocks = session.option_blocks("Review", ["Approve & implement", "Request changes"])
    assert blocks is not None
    assert [json.loads(button["value"]) for button in blocks[1]["elements"]] == [
        {"type": "plan_approval", "action": "approve"},
        {"type": "plan_approval", "action": "revise"},
    ]


def test_other_options_carry_the_answer_they_stand_for() -> None:
    blocks = session.option_blocks("Pick", ["Ship it", "  ", "Hold"])
    assert blocks is not None
    elements = blocks[1]["elements"]
    assert [json.loads(button["value"])["response"] for button in elements] == ["Ship it", "Hold"]
    assert [button["action_id"] for button in elements] == [
        "open_swe_option_select_0",
        "open_swe_option_select_1",
    ]


@pytest.mark.parametrize("options", [None, [], ["   "]])
def test_no_real_options_means_no_blocks(options: list[str] | None) -> None:
    assert session.option_blocks("Pick", options) is None


def test_slack_action_ids_are_unique_and_recognized() -> None:
    slack_routes = importlib.import_module("agent.slack.routes")
    actions = session.build_workflow_approval_blocks("Review", "abc")[1]["elements"]

    assert len({action["action_id"] for action in actions}) == len(actions)
    assert slack_routes._first_open_swe_option_action(actions) is actions[0]
    legacy = {"action_id": "open_swe_option_select"}
    assert slack_routes._first_open_swe_option_action([legacy]) is legacy
    assert slack_routes._first_open_swe_option_action([{"action_id": "unrelated"}]) is None


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None
