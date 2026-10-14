import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openswe.claude_code.session_file import MISSING_TOOL_RESULT, ClaudeSessionFile
from openswe.claude_code.transcript import ClaudeTranscript


def _call(call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"id": call_id, "name": "execute", "args": {"command": "ls"}}]
    )


def test_a_thread_resumes_as_a_chained_session_with_every_tool_call_answered() -> None:
    file = ClaudeSessionFile(session_id="s1", cwd="/work/app", git_branch="feature")
    lines = list(
        file.lines(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": "<dynamic-context>sandbox</dynamic-context>"},
                        {"type": "text", "text": "fix the bug"},
                    ]
                ),
                _call("functions.execute:0"),
                HumanMessage(content="also add a test"),
                ToolMessage(content="a.py", tool_call_id="functions.execute:0"),
                _call("functions.execute:0"),
                AIMessage(content="done"),
            ],
            title="Fix the bug",
            note="continue here",
        )
    )
    records = [json.loads(line) for line in lines]

    chained = [record for record in records if "uuid" in record]
    assert [record["parentUuid"] for record in chained[1:]] == [
        record["uuid"] for record in chained[:-1]
    ]
    first_id, second_id = [
        block["id"]
        for record in records
        if record.get("type") == "assistant"
        for block in record["message"]["content"]
        if block["type"] == "tool_use"
    ]
    assert first_id != second_id
    assert all(char.isalnum() or char in "_-" for char in first_id + second_id)

    session = ClaudeTranscript.parse("\n".join(lines))
    assert session.title == "Fix the bug"
    assert [(type(message), message.content) for message in session.messages] == [
        (HumanMessage, "fix the bug"),
        (AIMessage, ""),
        (ToolMessage, "a.py"),
        (HumanMessage, "also add a test"),
        (AIMessage, ""),
        (ToolMessage, MISSING_TOOL_RESULT),
        (AIMessage, "done"),
    ]
