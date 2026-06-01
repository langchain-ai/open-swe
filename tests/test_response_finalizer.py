from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.response_finalizer import finalize_response


def _runtime() -> MagicMock:
    return MagicMock()


class TestFinalizeResponse:
    def test_replaces_empty_final_with_synthesis_from_last_tool_result(self) -> None:
        tool_call_id = "tc-1"
        state = {
            "messages": [
                HumanMessage(content="do the thing"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "bash", "args": {"command": "ls"}, "id": tool_call_id}],
                ),
                ToolMessage(content="file1\nfile2", tool_call_id=tool_call_id, name="bash"),
                AIMessage(content=""),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is not None
        last = result["messages"][-1]
        assert "bash" in last.content
        assert "file1" in last.content
        assert last.tool_calls == []

    def test_replaces_empty_list_content(self) -> None:
        tool_call_id = "tc-2"
        state = {
            "messages": [
                HumanMessage(content="ok"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": tool_call_id}],
                ),
                ToolMessage(content="contents", tool_call_id=tool_call_id, name="read_file"),
                AIMessage(content=[{"type": "text", "text": "   "}]),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is not None
        last = result["messages"][-1]
        assert "read_file" in last.content

    def test_emits_explicit_limit_message_when_step_limit_triggered(self) -> None:
        tool_call_id = "tc-3"
        state = {
            "messages": [
                HumanMessage(content="go"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "bash", "args": {"command": "echo hi"}, "id": tool_call_id}
                    ],
                ),
                ToolMessage(content="hi", tool_call_id=tool_call_id, name="bash"),
                AIMessage(content="Model call limits exceeded: run limit reached"),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is not None
        last = result["messages"][-1]
        assert "Reached step limit" in last.content
        assert "bash" in last.content

    def test_emits_limit_message_when_last_msg_only_has_tool_calls(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="go"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "bash", "args": {"command": "ls"}, "id": "tc-4"}],
                ),
                ToolMessage(content="result", tool_call_id="tc-4", name="bash"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "bash", "args": {"command": "pwd"}, "id": "tc-5"}],
                ),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is not None
        last = result["messages"][-1]
        assert "Reached step limit" in last.content
        assert last.tool_calls == []

    def test_passes_through_when_content_is_non_empty(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="All done — the PR is open."),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is None

    def test_returns_none_for_empty_state(self) -> None:
        assert finalize_response.after_agent({"messages": []}, _runtime()) is None

    def test_returns_none_when_no_tool_history_and_no_content(self) -> None:
        # With no tool messages to summarize, still produce a non-empty fallback.
        state = {
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(content=""),
            ]
        }

        result = finalize_response.after_agent(state, _runtime())

        assert result is not None
        assert result["messages"][-1].content
