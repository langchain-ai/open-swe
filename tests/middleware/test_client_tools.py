"""A run stops at a client tool call and resumes on the client's result."""

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.openai_responses.client_tools import ClientToolSpec


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "client-tools-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses[len(self.seen) - 1])])


async def test_run_stops_at_client_call_and_resumes_with_its_result() -> None:
    call = {"name": "exec_command", "args": {"cmd": "ls"}, "id": "call-1", "type": "tool_call"}
    model = ScriptedModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="two files")]
    )
    spec = ClientToolSpec(
        kind="function",
        name="exec_command",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    agent = create_agent(
        model, middleware=[ClientToolsMiddleware([spec])], checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "guest"}}

    first = await agent.ainvoke({"messages": [{"role": "user", "content": "list files"}]}, config)
    assert len(model.seen) == 1
    pending = first["messages"][-1]
    assert isinstance(pending, ToolMessage) and pending.tool_call_id == "call-1"

    result = {
        "type": "tool",
        "id": ClientToolSpec.result_message_id("call-1"),
        "tool_call_id": "call-1",
        "content": "a.py\nb.py",
    }
    second = await agent.ainvoke({"messages": [result]}, config)
    assert [type(m).__name__ for m in second["messages"]] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "AIMessage",
    ]
    assert second["messages"][2].content == "a.py\nb.py"
    assert second["messages"][-1].content == "two files"
    assert model.seen[1][-1].content == "a.py\nb.py"
