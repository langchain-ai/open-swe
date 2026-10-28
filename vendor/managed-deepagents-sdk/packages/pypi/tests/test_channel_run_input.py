import json

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
    convert_to_messages,
)

from managed_deepagents._channels.run_input import InvalidRunInput, normalize_run_input


def _revived(value: object) -> list[object]:
    """What the Agent Server makes of the normalized messages' JSON."""
    normalized = normalize_run_input(value)
    assert normalized is not None
    return convert_to_messages(json.loads(json.dumps(normalized["messages"])))


def test_every_message_like_agent_invoke_takes_is_accepted() -> None:
    revived = _revived(
        {
            "messages": [
                "text",
                ("human", "tuple"),
                {"role": "user", "content": "role dict"},
                AIMessage(
                    content="", tool_calls=[{"name": "lookup", "args": {"q": 1}, "id": "c1"}]
                ),
                ToolMessage(content="found", tool_call_id="c1", name="lookup"),
            ]
        }
    )
    assert [message.type for message in revived] == ["human", "human", "human", "ai", "tool"]
    assert [message.content for message in revived] == ["text", "tuple", "role dict", "", "found"]
    assert revived[3].tool_calls == [
        {"name": "lookup", "args": {"q": 1}, "id": "c1", "type": "tool_call"}
    ]
    assert revived[4].tool_call_id == "c1"


def test_content_blocks_are_kept() -> None:
    blocks = [
        {"type": "text", "text": "describe"},
        {"type": "image", "mime_type": "image/png", "base64": "AA=="},
    ]
    [revived] = _revived(HumanMessage(content=blocks))
    assert revived.content == blocks


@pytest.mark.parametrize(
    "value",
    [
        {"messages": ["  "]},
        {"messages": [42]},
        {"messages": [RemoveMessage(id="m-1")]},
        ("prompt", {"context": {"channel": {}}}),
        ("prompt", {"context": {"mda_channel": {}}}),
        ("prompt", {"configurable": {}}),
        ("prompt", {"metadata": ["x"]}),
        # MDA and the Agent Server set these, in any case.
        ("prompt", {"metadata": {"mda_delivery_key": "d-1"}}),
        ("prompt", {"metadata": {"MDA_x": 1}}),
        ("prompt", {"metadata": {"langgraph_step": 1}}),
        # LangSmith and the Agent Server set these, or group a thread's runs by them.
        *(
            ("prompt", {"metadata": {key: "x"}})
            for key in (
                "thread_id",
                "session_id",
                "conversation_id",
                "run_id",
                "assistant_id",
                "graph_id",
                "user_id",
                "owner",
                "ls_provider",
                "Thread_ID",
            )
        ),
        ("prompt", {"metadata": {"a b": 1}}),
        ("prompt", {"metadata": {"constructor": 1}}),
        # A webhook is an http(s) URL without credentials.
        ("prompt", {"webhook": "ftp://backend.example/runs"}),
        ("prompt", {"webhook": "https://user:pass@backend.example/runs"}),
        ("prompt", {"webhook": "/runs"}),
        ("prompt", {"webhook": 42}),
        # The TypeScript runtime rejects the same ones.
        ("prompt", {"webhook": "https:backend.example"}),
        ("prompt", {"webhook": "http://x:abc/"}),
        ("prompt", {"webhook": "http://[::1/"}),
        ("prompt", {"webhook": " https://x/runs"}),
    ],
)
def test_invalid_input_is_rejected(value: object) -> None:
    with pytest.raises(InvalidRunInput):
        normalize_run_input(value)


def test_run_metadata_comes_with_the_input() -> None:
    normalized = normalize_run_input(
        ("prompt", {"context": {"token": "t"}, "metadata": {"issue": "ENG-1", "user.id": "u"}})
    )
    assert normalized is not None
    assert normalized.get("context") == {"token": "t"}
    assert normalized.get("metadata") == {"issue": "ENG-1", "user.id": "u"}


def test_a_run_webhook_comes_with_the_input() -> None:
    normalized = normalize_run_input(("prompt", {"webhook": "https://backend.example/runs/done"}))
    assert normalized is not None
    assert normalized.get("webhook") == "https://backend.example/runs/done"
    # Not normalized, so both runtimes pass the Agent Server the same URL.
    kept = normalize_run_input(("prompt", {"webhook": "HTTPS://Backend.example:8443"}))
    assert kept is not None
    assert kept.get("webhook") == "HTTPS://Backend.example:8443"


def test_none_run_options_are_not_set() -> None:
    assert normalize_run_input(
        ("prompt", {"context": None, "metadata": None, "webhook": None})
    ) == (normalize_run_input("prompt"))


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (("a", "b", "c"), "run input must be (input, options)"),
        ("  ", "run input must not be blank"),
        (42, "run input must be a string, a message, or a mapping of state"),
        ({"messages": "hi"}, "run input messages must be a list"),
        ({"todos": {1}}, "run input state must be JSON"),
        ({}, "run input must not be empty"),
        ({"messages": ["  "]}, "a message must not be blank"),
        ({"messages": [42]}, "a message must be a string, a message, or JSON"),
        ({"messages": [RemoveMessage(id="m-1")]}, "unsupported message type remove"),
        (("prompt", ["x"]), "run options must be a mapping"),
        (
            ("prompt", {"configurable": {}, "tags": []}),
            "unsupported run option configurable, tags; use context, metadata or webhook",
        ),
        (("prompt", {"context": ["x"]}), "run context must be a JSON object"),
        (
            ("prompt", {"context": {"channel": {}}}),
            "context.channel is reserved for managed ingress",
        ),
        (
            ("prompt", {"context": {"mda_channel": {}}}),
            "context.mda_channel is reserved for managed ingress",
        ),
        (("prompt", {"metadata": ["x"]}), "run metadata must be a JSON object"),
        (
            ("prompt", {"metadata": {"a b": 1}}),
            (
                "run metadata key is not up to 64 letters, digits, _, . or -, "
                "starting with a letter: a b"
            ),
        ),
        (("prompt", {"metadata": {"user_id": "u"}}), "run metadata key is reserved: user_id"),
        (
            ("prompt", {"metadata": {"ls_repo_provider": "github"}}),
            "run metadata key is reserved: ls_repo_provider",
        ),
        (
            ("prompt", {"webhook": "ftp://backend.example/runs"}),
            "run webhook must be an http(s) URL, without credentials in it",
        ),
    ],
)
def test_a_refusal_says_why(value: object, reason: str) -> None:
    with pytest.raises(InvalidRunInput) as raised:
        normalize_run_input(value)
    assert raised.value.reason == reason
    assert str(raised.value) == reason
    # Raised as before, so callers that catch ``TypeError`` still do.
    assert isinstance(raised.value, TypeError)


@pytest.mark.parametrize(
    "options",
    [{"metadata": {"bad\nkey": 1}}, {"metadata": {"a" * 500: 1}}, {"bad\nkey": 1}],
)
def test_a_refusal_names_a_key_on_one_bounded_line(options: dict[str, object]) -> None:
    with pytest.raises(InvalidRunInput) as raised:
        normalize_run_input(("prompt", options))
    assert "\n" not in raised.value.reason
    assert len(raised.value.reason) < 256
