import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from langchain_quickjs._format import coerce_tool_output_for_ptc
from langchain_quickjs._repl import _Registry


class TestToolOutput:
    @pytest.mark.parametrize("envelope", ["message", "list", "command", "command_list"])
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (
                ' {"success": true, "items": [{"id": "a"}]}',
                {"success": True, "items": [{"id": "a"}]},
            ),
            ('\n[{"id": "a"}]', [{"id": "a"}]),
            ('{"incomplete":', '{"incomplete":'),
            ("plain text", "plain text"),
            (
                [{"type": "text", "text": '{"id": "a"}'}],
                [{"type": "text", "text": '{"id": "a"}'}],
            ),
        ],
    )
    async def test_structured_message(
        self,
        envelope: str,
        content: str | list[str | dict[str, object]],
        expected: object,
    ) -> None:
        message = ToolMessage(content=content, name="list_threads", tool_call_id="call")
        value: object = message
        if envelope == "list":
            value = [ToolMessage(content="older", tool_call_id="old"), message]
        elif envelope in {"command", "command_list"}:
            value = Command(update={"messages": [message]})
            if envelope == "command_list":
                value = [value]
        assert coerce_tool_output_for_ptc(value) == expected

    @pytest.mark.parametrize("name", ["execute", "read_file"])
    @pytest.mark.parametrize("content", ['{"items": []}', "[1, 2]", "plain text"])
    @pytest.mark.parametrize("envelope", ["message", "list", "command"])
    async def test_text_message(self, name: str, content: str, envelope: str) -> None:
        message = ToolMessage(content=content, name=name, tool_call_id="call")
        value: object = message
        if envelope == "list":
            value = [message]
        elif envelope == "command":
            value = Command(update={"messages": [message]})
        assert coerce_tool_output_for_ptc(value) == content

    async def test_bare_string_and_command_selection(self) -> None:
        content = '{"items": []}'
        assert coerce_tool_output_for_ptc(content) == content
        command = Command(
            update={
                "messages": [
                    ToolMessage(
                        content=content, name="list_threads", tool_call_id="call"
                    ),
                    AIMessage(content="final message"),
                ]
            }
        )
        assert coerce_tool_output_for_ptc(command) == "final message"

    async def list_threads(self) -> ToolMessage:
        return ToolMessage(
            content='{"success": true, "items": [{"id": "a"}]}',
            name="list_threads",
            tool_call_id="call",
        )

    async def test_eval_accesses_structured_result(self) -> None:
        registry = _Registry(
            memory_limit=64 * 1024 * 1024,
            timeout=5,
            capture_console=True,
            max_stdout_chars=4000,
        )
        try:
            repl = registry.get("structured-result")
            repl.install_tools(
                [
                    StructuredTool.from_function(
                        coroutine=self.list_threads,
                        name="list_threads",
                        description="List threads",
                    )
                ]
            )
            result = await repl.eval_async(
                "const threads = await tools.listThreads({}); "
                "({success: threads.success, id: threads.items[0].id})"
            )
            assert result.error_type is None
            assert result.result == '{success: true, id: "a"}'
        finally:
            await registry.aevict("structured-result")
