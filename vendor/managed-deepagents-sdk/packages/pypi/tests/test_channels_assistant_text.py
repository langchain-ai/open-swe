"""Tests for assistant message text extraction."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from managed_deepagents._channels.assistant_text import (
    extract_assistant_content,
    extract_assistant_text,
    extract_reply_content,
    extract_reply_text,
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


NOTE = {"deepagents_notification": {"task_id": "t1", "subagent": "researcher", "status": "success"}}


def _answered_then_notified(extra: str) -> dict[str, object]:
    """A run that answered the user, then got task news and answered again."""
    return {
        "messages": [
            {"type": "human", "content": "earlier question"},
            {"type": "ai", "content": "earlier answer"},
            {"type": "human", "content": "summarize the doc"},
            {"type": "ai", "content": "here is the summary"},
            {"type": "human", "content": "[SYSTEM NOTIFICATION]", "additional_kwargs": NOTE},
            {"type": "ai", "content": extra},
        ]
    }


@pytest.mark.parametrize("extract", [extract_reply_text, extract_reply_content])
def test_reply_keeps_the_answer_given_before_task_news(
    extract: Callable[..., object],
) -> None:
    state = _answered_then_notified("the researcher also finished")

    assert extract(state) == "here is the summary\n\nthe researcher also finished"


@pytest.mark.parametrize("extract", [extract_reply_text, extract_reply_content])
def test_reply_leaves_out_a_quiet_answer_to_task_news(
    extract: Callable[..., object],
) -> None:
    assert extract(_answered_then_notified("[quiet]")) == "here is the summary"


def test_notification_run_reply_starts_after_its_input() -> None:
    state = {
        "messages": [
            {"type": "human", "content": "question"},
            {"id": "a1", "type": "ai", "content": "answer"},
            {"id": "n1", "type": "human", "content": "[NOTICE]", "additional_kwargs": NOTE},
            {"type": "ai", "content": "first task done"},
            {"type": "human", "content": "[SYSTEM NOTIFICATION]", "additional_kwargs": NOTE},
            {"type": "ai", "content": "second task done"},
        ]
    }

    assert extract_reply_text(state, after_id="a1") == "first task done\n\nsecond task done"


def test_reply_with_one_answer_is_unchanged() -> None:
    state = {
        "messages": [
            HumanMessage("question"),
            AIMessage("", tool_calls=[{"id": "c1", "name": "tool", "args": {}}]),
            {"type": "tool", "content": "result", "tool_call_id": "c1"},
            AIMessage("final answer"),
        ]
    }

    assert extract_reply_text(state) == extract_assistant_text(state) == "final answer"


@pytest.mark.parametrize(
    ("extract", "empty"), [(extract_reply_text, ""), (extract_reply_content, None)]
)
def test_run_with_no_answer_since_its_start_does_not_repost_an_earlier_reply(
    extract: Callable[..., object], empty: object
) -> None:
    state = {
        "messages": [
            {"type": "human", "content": "question"},
            {"id": "a1", "type": "ai", "content": "answer already posted"},
            {"id": "n1", "type": "human", "content": "[NOTICE]", "additional_kwargs": NOTE},
            {"type": "ai", "content": ""},
        ]
    }

    assert extract(state, after_id="a1") == empty


def test_resumed_run_replies_only_after_its_start() -> None:
    # The notification run paused on an approval; the user's answer resumed it.
    state = {
        "messages": [
            {"type": "human", "content": "question"},
            {"id": "a1", "type": "ai", "content": "answer already posted"},
            {"id": "n1", "type": "human", "content": "[NOTICE]", "additional_kwargs": NOTE},
            AIMessage("", tool_calls=[{"id": "c1", "name": "send", "args": {}}]),
            {"type": "tool", "content": "sent", "tool_call_id": "c1"},
            {"type": "ai", "content": "sent it"},
        ],
    }

    assert extract_reply_text(state, after_id="a1") == "sent it"


IMAGE_REPLY = [{"type": "image", "url": "https://example.com/chart.png"}]


def test_image_only_answer_since_the_run_start_is_kept() -> None:
    state = {
        "messages": [
            {"id": "a0", "type": "ai", "content": "earlier"},
            {"id": "n1", "type": "human", "content": "[NOTICE]", "additional_kwargs": NOTE},
            {"type": "ai", "content": IMAGE_REPLY},
        ]
    }

    assert extract_reply_content(state, after_id="a0") == IMAGE_REPLY


def test_quiet_single_answer_since_the_run_start_is_not_posted() -> None:
    state = {
        "messages": [
            {"id": "a0", "type": "ai", "content": "earlier"},
            {"id": "n1", "type": "human", "content": "[NOTICE]", "additional_kwargs": NOTE},
            {"type": "ai", "content": "[quiet]"},
        ]
    }

    assert extract_reply_content(state, after_id="a0") is None
    assert extract_reply_text(state, after_id="a0") == ""
