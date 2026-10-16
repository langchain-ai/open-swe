import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openswe.claude_code.transcript import (
    MISSING_TOOL_RESULT,
    ClaudeTranscript,
    TranscriptError,
)


def _jsonl(*records: dict[str, object]) -> str:
    return "\n".join(json.dumps(record) for record in records) + "\n"


def _user(uuid: str, parent: str | None, content: object) -> dict[str, object]:
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "message": {"content": content}}


def _assistant(uuid: str, parent: str, message_id: str, block: dict[str, object]):
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "message": {"id": message_id, "content": [block]},
    }


def _tool_use(call_id: str) -> dict[str, object]:
    return {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": "ls"}}


def _result(call_id: str, text: str) -> list[dict[str, object]]:
    return [{"type": "tool_result", "tool_use_id": call_id, "content": text}]


def test_rewound_branch_is_dropped_and_parallel_results_are_kept() -> None:
    session = ClaudeTranscript.parse(
        _jsonl(
            _user("u1", None, "first try"),
            _assistant("a1", "u1", "m1", {"type": "text", "text": "abandoned"}),
            _user("u2", None, "<system-reminder>private</system-reminder>second try"),
            _assistant("a2", "u2", "m2", _tool_use("c1")),
            _assistant("a3", "a2", "m2", _tool_use("c2")),
            _user("r1", "a2", _result("c1", "one")),
            _user("r2", "a3", _result("c2", "two")),
            _assistant("a4", "r2", "m3", {"type": "text", "text": "done"}),
        )
    )

    assert [type(message) for message in session.messages] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        ToolMessage,
        AIMessage,
    ]
    assert session.messages[0].content == "second try"
    calls = session.messages[1]
    assert isinstance(calls, AIMessage)
    assert [call["id"] for call in calls.tool_calls] == ["c1", "c2"]
    assert [message.content for message in session.messages[2:]] == ["one", "two", "done"]


def test_prompt_queued_mid_turn_waits_for_open_tool_calls_and_dangling_calls_get_a_result() -> None:
    session = ClaudeTranscript.parse(
        _jsonl(
            _user("u1", None, "go"),
            _assistant("a1", "u1", "m1", _tool_use("c1")),
            {
                "type": "attachment",
                "uuid": "q1",
                "parentUuid": "a1",
                "attachment": {"type": "queued_command", "prompt": "also this"},
            },
            _assistant("a2", "q1", "m2", _tool_use("c2")),
        )
    )

    assert [(type(message), message.content) for message in session.messages[1:]] == [
        (AIMessage, ""),
        (ToolMessage, MISSING_TOOL_RESULT),
        (HumanMessage, "also this"),
        (AIMessage, ""),
        (ToolMessage, MISSING_TOOL_RESULT),
    ]


def test_reshaped_records_and_blocks_are_skipped_without_breaking_the_chain() -> None:
    session = ClaudeTranscript.parse(
        _jsonl(
            {**_user("u1", None, "hi"), "someNewField": {"nested": True}},
            _user("u2", "u1", {"content moved": "into an object"}),
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u2",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "id": "c1"},
                        {"type": "text", "text": "still here", "citations": []},
                    ],
                },
            },
        )
        + "{not json\n"
    )

    assert [(type(message), message.content) for message in session.messages] == [
        (HumanMessage, "hi"),
        (AIMessage, "still here"),
    ]


def test_chain_runs_through_records_that_carry_no_message() -> None:
    session = ClaudeTranscript.parse(
        _jsonl(
            _user("u1", None, "first"),
            _assistant("a1", "u1", "m1", {"type": "text", "text": "one"}),
            {"type": "system", "subtype": "stop_hook_summary", "uuid": "s1", "parentUuid": "a1"},
            _user("u2", "s1", "second"),
            _assistant("a2", "u2", "m2", {"type": "text", "text": "two"}),
        )
    )

    assert [message.content for message in session.messages] == ["first", "one", "second", "two"]


def test_input_with_no_transcript_records_is_rejected() -> None:
    with pytest.raises(TranscriptError):
        ClaudeTranscript.parse("{not json\nplain text\n")
