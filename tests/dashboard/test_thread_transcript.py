from agent.dashboard.thread_transcript import message_to_ui


def test_mapping_message_preserves_metadata_reasoning_and_tool_calls() -> None:
    converted = message_to_ui(
        {
            "id": "message-1",
            "role": "assistant",
            "content": [{"type": "reasoning", "reasoning": "Checking the repository"}],
            "response_metadata": {"created_at": "2026-08-25T12:00:00Z"},
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "read_file",
                    "args": {"path": "README.md"},
                }
            ],
        }
    )

    assert converted == {
        "id": "message-1",
        "author": "agent",
        "timestamp": "2026-08-25T12:00:00Z",
        "chunks": [
            {"kind": "reasoning", "text": "Checking the repository"},
            {
                "kind": "tool-execution",
                "toolCallId": "call-1",
                "title": "read_file",
                "toolKind": "other",
                "input": {"path": "README.md"},
                "status": "completed",
            },
        ],
    }
