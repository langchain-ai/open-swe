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
