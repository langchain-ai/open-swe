from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.sanitize_openai_responses import _sanitize_messages


def test_sanitize_messages_drops_stale_reasoning_and_dependent_function_call() -> None:
    messages = [
        HumanMessage(content="review this"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_123",
                    "summary": [],
                    "content": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_123",
                    "name": "read_file",
                    "arguments": "{}",
                },
            ],
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {},
                    "id": "call_123",
                }
            ],
        ),
        ToolMessage(content="file contents", tool_call_id="call_123", name="read_file"),
    ]

    _sanitize_messages(messages)

    content = messages[1].content
    assert isinstance(content, list)
    assert content == []
    assert messages[1].tool_calls == []
    assert len(messages) == 2


def test_sanitize_messages_drops_stale_reasoning_and_invalid_tool_call() -> None:
    messages = [
        HumanMessage(content="review this"),
        AIMessage(
            content=[
                {"type": "reasoning", "id": "rs_123", "summary": [], "content": []},
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_invalid",
                    "name": "read_file",
                    "arguments": "{",
                },
            ],
            invalid_tool_calls=[
                {
                    "name": "read_file",
                    "args": "{",
                    "id": "call_invalid",
                    "error": "invalid JSON",
                    "type": "invalid_tool_call",
                }
            ],
        ),
        ToolMessage(content="tool error", tool_call_id="call_invalid", name="read_file"),
    ]

    _sanitize_messages(messages)

    assert messages[1].content == []
    assert messages[1].invalid_tool_calls == []
    assert len(messages) == 2


def test_sanitize_messages_drops_stale_reasoning_and_tool_calls_only() -> None:
    messages = [
        AIMessage(
            content=[{"type": "reasoning", "id": "rs_123", "summary": [], "content": []}],
            tool_calls=[{"name": "read_file", "args": {}, "id": "call_tool_only"}],
        ),
        ToolMessage(
            content="file contents",
            tool_call_id="call_tool_only",
            name="read_file",
        ),
    ]

    _sanitize_messages(messages)

    assert messages[0].content == []
    assert messages[0].tool_calls == []
    assert len(messages) == 1


def test_sanitize_messages_drops_stale_reasoning_and_invalid_tool_calls_only() -> None:
    messages = [
        AIMessage(
            content=[{"type": "reasoning", "id": "rs_123", "summary": [], "content": []}],
            invalid_tool_calls=[
                {
                    "name": "read_file",
                    "args": "{",
                    "id": "call_invalid_only",
                    "error": "invalid JSON",
                    "type": "invalid_tool_call",
                }
            ],
        ),
        ToolMessage(
            content="tool error",
            tool_call_id="call_invalid_only",
            name="read_file",
        ),
    ]

    _sanitize_messages(messages)

    assert messages[0].content == []
    assert messages[0].invalid_tool_calls == []
    assert len(messages) == 1


def test_sanitize_messages_drops_stale_reasoning_and_v1_tool_call() -> None:
    messages = [
        HumanMessage(content="review this"),
        AIMessage(
            content=[
                {"type": "reasoning", "id": "rs_123", "summary": [], "content": []},
                {
                    "type": "tool_call",
                    "id": "call_v1",
                    "name": "read_file",
                    "args": {},
                },
            ],
            tool_calls=[{"name": "read_file", "args": {}, "id": "call_v1"}],
        ),
        ToolMessage(content="file contents", tool_call_id="call_v1", name="read_file"),
    ]

    _sanitize_messages(messages)

    assert messages[1].content == []
    assert messages[1].tool_calls == []
    assert len(messages) == 2


def test_sanitize_messages_drops_stale_reasoning_and_non_standard_call() -> None:
    messages = [
        HumanMessage(content="review this"),
        AIMessage(
            content=[
                {"type": "reasoning", "id": "rs_123", "summary": [], "content": []},
                {
                    "type": "non_standard",
                    "value": {
                        "type": "function_call",
                        "call_id": "call_non_standard",
                        "name": "read_file",
                        "arguments": "{}",
                    },
                },
            ],
            tool_calls=[{"name": "read_file", "args": {}, "id": "call_non_standard"}],
        ),
        ToolMessage(
            content="file contents",
            tool_call_id="call_non_standard",
            name="read_file",
        ),
    ]

    _sanitize_messages(messages)

    assert messages[1].content == []
    assert messages[1].tool_calls == []
    assert len(messages) == 2


def test_sanitize_messages_preserves_encrypted_reasoning() -> None:
    reasoning = {
        "type": "reasoning",
        "id": "rs_123",
        "encrypted_content": "encrypted",
        "summary": [],
        "content": [],
    }
    messages = [AIMessage(content=[reasoning])]

    _sanitize_messages(messages)

    assert messages[0].content == [reasoning]


def test_sanitize_messages_drops_reverse_orphaned_tool_result() -> None:
    messages = [
        HumanMessage(content="continue"),
        ToolMessage(content="orphaned result", tool_call_id="call_orphan", name="grep"),
        HumanMessage(content="pick up where you left off"),
    ]

    _sanitize_messages(messages)

    assert messages == [
        HumanMessage(content="continue"),
        HumanMessage(content="pick up where you left off"),
    ]


def test_sanitize_messages_preserves_matched_tool_result() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": {}, "id": "call_matched"}],
        ),
        ToolMessage(content="matched result", tool_call_id="call_matched", name="grep"),
    ]

    _sanitize_messages(messages)

    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)


def test_sanitize_messages_recognizes_responses_function_call_blocks() -> None:
    messages = [
        AIMessage(
            content=[
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_content_block",
                    "name": "grep",
                    "arguments": "{}",
                }
            ]
        ),
        ToolMessage(
            content="matched result",
            tool_call_id="call_content_block",
            name="grep",
        ),
    ]

    _sanitize_messages(messages)

    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)


def test_sanitize_messages_recognizes_v1_tool_call_blocks() -> None:
    messages = [
        AIMessage(
            content=[
                {
                    "type": "tool_call",
                    "id": "call_v1",
                    "name": "grep",
                    "args": {},
                }
            ]
        ),
        ToolMessage(content="matched result", tool_call_id="call_v1", name="grep"),
    ]

    _sanitize_messages(messages)

    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)


def test_sanitize_messages_recognizes_non_standard_function_call_blocks() -> None:
    messages = [
        AIMessage(
            content=[
                {
                    "type": "non_standard",
                    "value": {
                        "type": "function_call",
                        "call_id": "call_non_standard",
                        "name": "grep",
                        "arguments": "{}",
                    },
                }
            ]
        ),
        ToolMessage(
            content="matched result",
            tool_call_id="call_non_standard",
            name="grep",
        ),
    ]

    _sanitize_messages(messages)

    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)


def test_sanitize_messages_requires_tool_call_to_precede_result() -> None:
    messages = [
        ToolMessage(content="early result", tool_call_id="call_late", name="grep"),
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": {}, "id": "call_late"}],
        ),
    ]

    _sanitize_messages(messages)

    assert len(messages) == 1
    assert isinstance(messages[0], AIMessage)
