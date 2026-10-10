from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from openswe.middleware.describe_commands import DescribeCommandsMiddleware


class _DescribedCommandModel(BaseChatModel):
    _bound: list[BaseTool] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "described-command-test"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        self._bound = [tool for tool in tools if isinstance(tool, BaseTool)]
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])
        call = {
            "name": "execute",
            "args": {"command": "echo described", "description": "Print a greeting"},
            "id": "call-1",
            "type": "tool_call",
        }
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
        )


async def test_a_described_command_still_runs(tmp_path: Path) -> None:
    model = _DescribedCommandModel()
    agent = create_deep_agent(
        model=model,
        system_prompt="",
        backend=LocalShellBackend(root_dir=tmp_path),
        middleware=[DescribeCommandsMiddleware()],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})

    execute = next(tool for tool in model._bound if tool.name == "execute")
    assert "description" in execute.tool_call_schema.model_json_schema()["properties"]
    output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert output.status == "success"
    assert "described" in str(output.content)
