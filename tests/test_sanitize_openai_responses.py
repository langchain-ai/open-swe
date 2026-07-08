from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.sanitize_openai_responses import _sanitize_messages


def test_sanitize_messages_drops_orphaned_function_call() -> None:
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
    ]

    _sanitize_messages(messages)

    content = messages[1].content
    assert isinstance(content, list)
    # Dropping the reasoning item orphans the function_call it anchored, so the
    # replay must drop the function_call too rather than leave the API-rejected
    # orphan.
    assert content == []


def test_sanitize_messages_keeps_calls_when_reasoning_survives() -> None:
    messages = [
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_stale",
                    "summary": [],
                    "content": [],
                },
                {
                    "type": "reasoning",
                    "id": "rs_kept",
                    "encrypted_content": "encrypted",
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
        ),
    ]

    _sanitize_messages(messages)

    content = messages[0].content
    assert [block["type"] for block in content] == ["reasoning", "function_call"]
    assert content[0]["id"] == "rs_kept"


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
