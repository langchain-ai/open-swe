"""A post that starts a new Trigger thread, and the run it starts there."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from managed_deepagents._channels import runtime as channel_runtime
from managed_deepagents._channels.trigger import conversation

SOURCE = "slack:v1:A1:T1:C0SOURCE:1789682150.723429"
NEW = "slack:v1:A1:T1:C0SOURCE:1789700000.000100"
ORIGIN = {
    "name": "slack",
    "provider": "slack",
    "source_thread_key": SOURCE,
    "delivery_id": "delivery",
    "transport": "trigger_server",
    "target": {
        "channel_id": "managed-channel",
        "source_thread_key": SOURCE,
        "address": {"conversation_id": "C0SOURCE", "thread_id": "1789682150.723429"},
    },
}
MESSAGE = {"type": "content", "content": "Breakout"}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    recorded = SimpleNamespace(posts=[], runs=[])

    class Client:
        async def post_action(self, channel_id: str, body: dict[str, object]) -> dict[str, object]:
            recorded.posts.append((channel_id, body))
            return {
                "id": "1789700000.000100",
                "thread": {
                    "channel_id": "managed-channel",
                    "source_thread_key": NEW,
                    "address": {"conversation_id": "C0SOURCE", "thread_id": "1789700000.000100"},
                },
            }

    async def start(**run: object) -> dict[str, str]:
        recorded.runs.append(run)
        return {"thread_id": "t", "run_id": "r"}

    config = {
        "configurable": {
            "assistant_id": "agent",
            "langgraph_auth_user": {"mda_agent_auth_principal_id": "principal"},
        }
    }
    monkeypatch.setattr(conversation, "start_ingress_agent", start)
    monkeypatch.setattr(conversation, "get_config", lambda: config)
    recorded.client = Client()
    return recorded


def start(calls: SimpleNamespace, run: object) -> dict[str, object]:
    return asyncio.run(
        conversation.start_trigger_conversation(calls.client, ORIGIN, MESSAGE, run)  # type: ignore[arg-type]
    )


def test_posts_a_followed_message_and_starts_the_run_in_its_thread(
    calls: SimpleNamespace,
) -> None:
    posted = start(calls, {"messages": [HumanMessage("Continue here.")]})

    [(channel_id, body)] = calls.posts
    assert channel_id == "managed-channel"
    assert body == {
        "type": "post",
        "action_id": body["action_id"],
        "address": {"conversation_id": "C0SOURCE"},
        "follow_thread": True,
        "message": {"text": "Breakout"},
    }
    thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, NEW))
    [run] = calls.runs
    assert run["thread_id"] == thread_id
    assert run["assistant_id"] == "agent"
    assert run["user_id"] == "principal"
    assert run["source_thread_key"] == NEW
    assert [(m["role"], m["content"]) for m in run["messages"]] == [("human", "Continue here.")]
    assert run["binding"]["source_thread_key"] == NEW
    assert run["binding"]["target"]["source_thread_key"] == NEW
    assert posted == {
        "id": "1789700000.000100",
        "thread_id": thread_id,
        "url": "https://slack.com/archives/C0SOURCE/p1789700000000100",
    }


def test_starts_no_run_without_run_input(calls: SimpleNamespace) -> None:
    posted = start(calls, None)

    assert len(calls.posts) == 1
    assert calls.runs == []
    assert posted["thread_id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, NEW))


def test_checks_the_run_input_before_it_posts(calls: SimpleNamespace) -> None:
    with pytest.raises(TypeError, match="run input must not be empty"):
        start(calls, {})
    assert calls.posts == []


def test_posts_a_new_thread_only_on_a_channel_that_can_start_one() -> None:
    posts: list[object] = []
    starts: list[object] = []

    class Transport:
        async def post(self, **kwargs: object) -> dict[str, str]:
            posts.append(kwargs)
            return {"id": "1"}

    async def start_thread(message: object, run: object) -> dict[str, str]:
        starts.append((message, run))
        return {"id": "1", "thread_id": "t"}

    def channel(**options: object) -> channel_runtime.RuntimeChannel:
        return channel_runtime.create_runtime_channel(
            name="slack", provider="slack", event=None, target={}, transport=Transport(), **options
        )

    with pytest.raises(ValueError, match="cannot start a new thread"):
        asyncio.run(channel().post(MESSAGE, new_thread=True))  # type: ignore[misc]
    with pytest.raises(ValueError, match="only with new_thread=True"):
        asyncio.run(channel().post(MESSAGE, run="Go."))  # type: ignore[misc]
    posted = asyncio.run(
        channel(start_thread=start_thread).post(MESSAGE, new_thread=True, run="Go.")  # type: ignore[misc]
    )
    assert posted == {"id": "1", "thread_id": "t"}
    assert starts == [(MESSAGE, "Go.")]
    assert posts == []
