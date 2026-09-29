from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from agent.input_messages import Surface, human_input, input_message_text
from agent.middleware.large_input import LARGE_INPUT_CHAR_THRESHOLD, LargeInputMiddleware
from agent.middleware.prepare_run import BasePrepareRunMiddleware


@pytest.mark.parametrize("multimodal", [False, True])
async def test_large_input_is_saved_without_changing_history(tmp_path: Path, multimodal: bool):
    body = "Read these logs: <error> café\n" * 1000
    content = human_input(body, {"sender_id": "user:alice", "surface": "web", "kind": "human"})[
        "content"
    ]
    assert isinstance(content, str)
    original = HumanMessage(
        content=[
            {"type": "text", "text": content},
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        ]
        if multimodal
        else content,
        id="large-input",
    )

    class Prepare(BasePrepareRunMiddleware):
        async def _prepare(self, state: AgentState, runtime: Runtime) -> dict[str, object]:
            return {"work_dir": "/"}

    @wrap_model_call
    async def inspect(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        reference = input_message_text(request.messages[0].content)
        assert reference is not None and "pasted-" in reference
        files = list(tmp_path.rglob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text() == body
        assert body not in str(request.messages[0].content)
        if multimodal:
            assert request.messages[0].content[1] == original.content[1]
        return ModelResponse(result=[AIMessage(content="Saved")])

    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[
            LargeInputMiddleware(FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)),
            Prepare(),
            inspect,
        ],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "large-input"}}
    await graph.ainvoke({"messages": [original]}, config)
    assert (await graph.aget_state(config)).values["messages"][0] == original


async def test_small_and_non_dashboard_inputs_stay_inline(tmp_path: Path):
    middleware = LargeInputMiddleware(FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True))
    cases: list[tuple[Surface, str]] = [
        ("web", "short"),
        ("slack", "x" * (LARGE_INPUT_CHAR_THRESHOLD + 1)),
    ]
    for surface, body in cases:
        content = human_input(
            body, {"sender_id": "user:alice", "surface": surface, "kind": "human"}
        )["content"]
        assert isinstance(content, str)
        assert await middleware._offload(content, "/") == content
    assert not list(tmp_path.rglob("*.txt"))
