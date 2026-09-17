from agent.threads.transcript_state import (
    TRIMMED_MARKER,
    state_from_thread_row,
    tool_results,
    transcript_values,
)

MESSAGES = [
    {
        "type": "human",
        "id": "h1",
        "content": "fix the bug",
        "additional_kwargs": {"lc_source": "slack"},
    },
    {
        "type": "ai",
        "id": "a1",
        "content": [
            {"type": "reasoning", "reasoning": "thinking hard"},
            {"type": "text", "text": "Looking at it."},
            {"type": "tool_call", "id": "c1", "name": "execute", "args": {"command": "ls"}},
        ],
        "tool_calls": [
            {"id": "c1", "name": "execute", "args": {"command": "ls"}, "type": "tool_call"}
        ],
        "response_metadata": {"created_at": 1, "model_name": "x", "usage": {"tokens": 9}},
        "additional_kwargs": {"lc_source": "agent", "refusal": None},
        "usage_metadata": {"input_tokens": 1},
    },
    {
        "type": "tool",
        "id": "t1",
        "name": "execute",
        "tool_call_id": "c1",
        "status": "error",
        "content": "x" * 5000,
        "artifact": {"className": "output_iframe", "html": "<div/>"},
    },
    {"type": "ai", "id": "a2", "content": "Done.", "tool_calls": []},
]


def test_transcript_keeps_only_what_the_transcript_paints() -> None:
    values, stats = transcript_values({"messages": MESSAGES, "files": {}})
    human, ai, tool, final = values["messages"]
    assert human == {
        "type": "human",
        "id": "h1",
        "content": "fix the bug",
        "additional_kwargs": {"lc_source": "slack"},
    }
    assert ai["content"] == [{"type": "text", "text": "Looking at it."}]
    assert ai["tool_calls"][0]["args"] == {"command": "ls"}
    assert ai["response_metadata"] == {"created_at": 1}
    assert ai["additional_kwargs"] == {"lc_source": "agent"}
    assert "usage_metadata" not in ai
    assert tool["content"] == ""
    assert tool["status"] == "error"
    assert tool["additional_kwargs"] == {TRIMMED_MARKER: True}
    assert tool["artifact"] == {"className": "output_iframe"}
    assert final["content"] == "Done."
    assert values["files"] == {}
    assert stats == {"messages": 4, "trimmed_tool_results": 1}


def test_tool_results_returns_full_content_for_requested_calls() -> None:
    found = tool_results({"messages": MESSAGES}, ["c1", "missing"])
    assert set(found) == {"c1"}
    assert found["c1"]["content"] == "x" * 5000
    assert found["c1"]["status"] == "error"
    assert found["c1"]["artifact"]["html"] == "<div/>"


def test_state_from_thread_row_exposes_pending_interrupts() -> None:
    interrupt = {"id": "i1", "value": {"question": "ok?"}}
    state = state_from_thread_row(
        {
            "values": {"messages": MESSAGES},
            "metadata": {"graph_id": "agent", "owner_login": "me"},
            "interrupts": {"task-1": [interrupt], "task-2": []},
            "state_updated_at": "2026-09-17T00:00:00Z",
        }
    )
    assert state["values"]["messages"] is MESSAGES
    assert state["next"] == []
    assert state["metadata"] == {"graph_id": "agent"}
    assert state["interrupts"] == [interrupt]
    assert [task["id"] for task in state["tasks"]] == ["task-1"]
    assert state["tasks"][0]["interrupts"] == [interrupt]
    assert state["created_at"] == "2026-09-17T00:00:00Z"
