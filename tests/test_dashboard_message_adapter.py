"""Tests for LangGraph message → dashboard UI adapter."""

from agent.dashboard.message_adapter import state_messages_to_ui


def test_state_messages_to_ui_maps_user_and_tool_calls() -> None:
    messages = [
        {"type": "human", "id": "u1", "content": "Fix the bug"},
        {
            "type": "ai",
            "id": "a1",
            "content": "I'll read the file first.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "read_file",
                    "args": {"path": "app.py"},
                }
            ],
        },
        {
            "type": "tool",
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": "print('hi')",
        },
    ]

    ui = state_messages_to_ui(messages)

    assert len(ui) == 2
    assert ui[0]["author"] == "user"
    assert ui[0]["chunks"][0]["text"] == "Fix the bug"
    assert ui[1]["author"] == "agent"
    tool_chunk = ui[1]["chunks"][-1]
    assert tool_chunk["kind"] == "tool-execution"
    assert tool_chunk["toolCallId"] == "call-1"
    assert tool_chunk["status"] == "completed"
    assert tool_chunk["output"] == "print('hi')"
