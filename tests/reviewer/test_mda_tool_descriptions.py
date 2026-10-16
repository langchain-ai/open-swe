"""The model labels each call of a described tool, and the tool runs without the label."""

import sys
from collections.abc import Sequence
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.middleware import DISPLAY_DESCRIPTION, DescribedToolsMiddleware


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    described: list[str] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "tool-descriptions-test"

    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> ScriptedModel:
        self.described = [
            t.name
            for t in tools
            if isinstance(t, BaseTool)
            and convert_to_openai_tool(t)["function"]["parameters"]["required"][0]
            == DISPLAY_DESCRIPTION
        ]
        return self

    def _generate(self, *args: object, **kwargs: object) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[self.calls - 1])])


async def test_tool_runs_without_the_description_the_model_gave_it() -> None:
    received: list[str] = []

    def count(path: str) -> str:
        received.append(path)
        return "3"

    # A JSON schema passes every argument through, so a stray one would fail the call.
    count_lines = StructuredTool.from_function(
        count,
        name="count_lines",
        description="Count the lines of a file.",
        args_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    call = {
        "name": "count_lines",
        "args": {DISPLAY_DESCRIPTION: "Count the README's lines", "path": "README.md"},
        "id": "call-1",
        "type": "tool_call",
    }
    model = ScriptedModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="3 lines")]
    )
    agent = create_agent(
        model,
        tools=[count_lines],
        middleware=[DescribedToolsMiddleware({"count_lines": "What this call does."})],
    )

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "how long?"}]})

    assert model.described == ["count_lines"]
    assert received == ["README.md"]
    issued, answered = result["messages"][1], result["messages"][2]
    assert isinstance(issued, AIMessage)
    assert issued.tool_calls[0]["args"][DISPLAY_DESCRIPTION] == "Count the README's lines"
    assert isinstance(answered, ToolMessage) and answered.content == "3"
