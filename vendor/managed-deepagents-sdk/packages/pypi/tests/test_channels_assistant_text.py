"""Tests for assistant message text extraction."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from managed_deepagents._channels.assistant_text import (
    extract_assistant_content,
    extract_assistant_text,
)


def test_reads_base_message_text_from_last_ai_message() -> None:
    assert (
        extract_assistant_text(
            {
                "messages": [
                    HumanMessage("hi"),
                    AIMessage(
                        content=[
                            {"type": "text", "text": "hello"},
                            {"type": "text", "text": " world"},
                        ]
                    ),
                ]
            }
        )
        == "hello world"
    )


def test_coerces_plain_serialized_ai_messages() -> None:
    assert (
        extract_assistant_text({"messages": [{"type": "ai", "content": "plain content"}]})
        == "plain content"
    )


def test_skips_non_ai_messages() -> None:
    assert extract_assistant_text({"messages": [HumanMessage("only human")]}) == ""


IMAGE = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("raw text", "raw text"),
        ([IMAGE], [IMAGE]),
        (["Hello", IMAGE], [{"type": "text", "text": "Hello"}, IMAGE]),
    ],
)
def test_preserves_and_normalizes_final_content(content: object, expected: object) -> None:
    for message in (AIMessage(content=content), {"role": "assistant", "content": content}):
        assert extract_assistant_content({"messages": [message]}) == expected


@pytest.mark.parametrize("extract", [extract_assistant_text, extract_assistant_content])
@pytest.mark.parametrize(
    "last",
    [
        {"type": "ai", "content": ""},
        {"type": "ai", "content": []},
        {"type": "ai", "content": "  "},
        {"type": "human", "content": "next question"},
        {"type": "tool", "content": "result", "tool_call_id": "call-1"},
        {},
    ],
)
def test_scans_backward_past_messages_without_usable_assistant_output(
    extract: Callable[[Mapping[str, object]], object], last: object
) -> None:
    assert extract({"messages": [{"type": "ai", "content": "answer"}, last]}) == "answer"


@pytest.mark.parametrize("extract", [extract_assistant_text, extract_assistant_content])
def test_preserves_slack_selection_of_assistant_text_accompanying_tool_calls(
    extract: Callable[[Mapping[str, object]], object],
) -> None:
    assert (
        extract(
            {
                "messages": [
                    {"type": "ai", "content": "earlier answer"},
                    {
                        "type": "ai",
                        "content": "calling tool",
                        "tool_calls": [{"id": "call-1", "name": "tool", "args": {}}],
                    },
                    {"type": "tool", "content": "result", "tool_call_id": "call-1"},
                ]
            }
        )
        == "calling tool"
    )


@pytest.mark.parametrize("extract", [extract_assistant_text, extract_assistant_content])
def test_no_reply_without_usable_assistant_output(
    extract: Callable[[Mapping[str, object]], object],
) -> None:
    assert not extract({"messages": [HumanMessage("hello"), AIMessage("")]})


def test_preserves_image_only_output_when_later_messages_contain_no_reply() -> None:
    state = {
        "messages": [
            {"type": "ai", "content": "earlier text"},
            {"type": "ai", "content": [IMAGE]},
            {"type": "tool", "content": "result", "tool_call_id": "call-1"},
        ]
    }
    assert extract_assistant_content(state) == [IMAGE]
    assert extract_assistant_text(state) == "earlier text"
