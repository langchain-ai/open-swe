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


def test_state_messages_to_ui_tags_slack_and_linear_replies() -> None:
    messages = [
        {"type": "human", "id": "u1", "content": "ping"},
        {
            "type": "ai",
            "id": "a1",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-slack",
                    "name": "slack_thread_reply",
                    "args": {"message": "Done! Opened a PR."},
                },
                {
                    "id": "call-linear",
                    "name": "linear_comment",
                    "args": {"comment_body": "Done! Opened a PR.", "ticket_id": "abc"},
                },
            ],
        },
    ]

    ui = state_messages_to_ui(messages)

    chunks = ui[-1]["chunks"]
    slack_chunk = next(c for c in chunks if c["toolCallId"] == "call-slack")
    linear_chunk = next(c for c in chunks if c["toolCallId"] == "call-linear")
    assert slack_chunk["toolKind"] == "slack"
    assert slack_chunk["input"]["message"] == "Done! Opened a PR."
    assert linear_chunk["toolKind"] == "linear"
    assert linear_chunk["input"]["comment_body"] == "Done! Opened a PR."


def test_state_messages_to_ui_merges_agent_turn_and_hides_internal_tools() -> None:
    messages = [
        {"type": "human", "id": "u1", "content": "hello"},
        {
            "type": "ai",
            "id": "a1",
            "content": "Hi! What would you like me to work on?",
            "tool_calls": [{"id": "call-cc", "name": "confirming_completion", "args": {}}],
        },
        {
            "type": "tool",
            "tool_call_id": "call-cc",
            "name": "confirming_completion",
            "content": "Confirming task completion.",
        },
        {
            "type": "ai",
            "id": "a2",
            "content": "Hi! What would you like me to work on? Let me know the task.",
        },
    ]

    ui = state_messages_to_ui(messages)

    assert len(ui) == 2
    assert ui[0]["author"] == "user"
    assert len(ui[1]["chunks"]) == 1
    assert (
        ui[1]["chunks"][0]["text"] == "Hi! What would you like me to work on? Let me know the task."
    )
