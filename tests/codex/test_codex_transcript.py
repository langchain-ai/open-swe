import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from openswe.codex.transcript import CodexTranscript


def _item(payload: dict[str, object]) -> str:
    return json.dumps({"type": "response_item", "payload": payload})


def _user(*texts: str) -> str:
    content = [{"type": "input_text", "text": text} for text in texts]
    return _item({"type": "message", "role": "user", "content": content})


def test_only_injected_context_is_dropped_and_parallel_calls_pair_by_call_id() -> None:
    session = CodexTranscript.parse(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "s1"}}),
                _user(
                    "# AGENTS.md instructions for /repo\n\nbe terse",
                    "<environment_context>x</environment_context>",
                ),
                _user("<environment_context>x</environment_context>", "<task>fix the bug</task>"),
                _item(
                    {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": '{"cmd": "ls"}',
                        "call_id": "a",
                    }
                ),
                _item(
                    {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": "patch",
                        "call_id": "b",
                    }
                ),
                _item({"type": "function_call_output", "call_id": "b", "output": "patched"}),
                _item({"type": "function_call_output", "call_id": "a", "output": "a.py"}),
                _item(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}],
                    }
                ),
            ]
        )
    )

    assert session.session_id == "s1"
    assert session.title == "<task>fix the bug</task>"
    assert [(type(m), m.content) for m in session.messages] == [
        (HumanMessage, "<task>fix the bug</task>"),
        (AIMessage, ""),
        (ToolMessage, "patched"),
        (ToolMessage, "a.py"),
        (AIMessage, "Done"),
    ]
    assert [call["id"] for call in session.messages[1].tool_calls] == ["a", "b"]
