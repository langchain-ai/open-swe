from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.middleware.no_subagents import NoSubagentsMiddleware


def test_disabled_subagents_never_register_task_tool() -> None:
    agent = create_deep_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[NoSubagentsMiddleware()],
        subagents=[],
    )
    tools = agent.nodes["tools"].bound.tools_by_name
    assert "task" not in tools
    assert "ls" in tools
