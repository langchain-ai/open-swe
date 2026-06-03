import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import RunnableConfig

from agent.multi_agent.state import MultiAgentState
from agent.multi_agent.nodes import pm_node, architect_node, qa_node
from agent.multi_agent.graph import route_after_qa, get_multi_agent_graph

class DummyMessage:
    def __init__(self, content):
        self.content = content

@pytest.mark.asyncio
async def test_pm_node(monkeypatch):
    """Test that PM Node generates a test plan successfully."""
    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=DummyMessage("## Test Plan\n- Verify feature A"))
    
    # Mock make_model helper
    monkeypatch.setattr("agent.multi_agent.nodes.make_model", lambda model_id, **kwargs: mock_model)
    
    state = {"task_description": "Implement a simple calculator."}
    config = {"configurable": {"agent_model_id": ("openai:gpt-5.5", "medium")}}
    
    res = await pm_node(state, config)
    assert "test_plan" in res
    assert "Verify feature A" in res["test_plan"]

@pytest.mark.asyncio
async def test_architect_node(monkeypatch):
    """Test that Architect Node parses sandbox files and isolates target files."""
    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=DummyMessage('{"target_files": ["math/calc.py", "tests/test_calc.py"]}'))
    monkeypatch.setattr("agent.multi_agent.nodes.make_model", lambda model_id, **kwargs: mock_model)

    # Mock get_sandbox_backend
    mock_sandbox = MagicMock()
    mock_sandbox.execute.return_value = MagicMock(output="./math/calc.py\n./tests/test_calc.py\n")
    monkeypatch.setattr("agent.multi_agent.nodes.get_sandbox_backend", AsyncMock(return_value=mock_sandbox))

    state = {
        "task_description": "Implement a calculator",
        "test_plan": "Verify addition"
    }
    config = {"configurable": {"thread_id": "thread-123"}}

    res = await architect_node(state, config)
    assert "target_files" in res
    assert "math/calc.py" in res["target_files"]
    assert "tests/test_calc.py" in res["target_files"]

@pytest.mark.asyncio
async def test_qa_node_python(monkeypatch):
    """Test that QA Node executes correct command for Python files."""
    mock_sandbox = MagicMock()
    mock_sandbox.execute.return_value = MagicMock(exit_code=0, output="5 passed in 0.12s")
    monkeypatch.setattr("agent.multi_agent.nodes.get_sandbox_backend", AsyncMock(return_value=mock_sandbox))

    state = {
        "target_files": ["calculator.py", "tests/test_calculator.py"]
    }
    config = {"configurable": {"thread_id": "thread-123"}}

    res = await qa_node(state, config)
    assert "test_results" in res
    assert res["test_results"]["success"] is True
    assert "pytest" in res["test_results"]["test_cmd"]
    assert "test_calculator.py" in res["test_results"]["test_cmd"]

@pytest.mark.asyncio
async def test_qa_node_js(monkeypatch):
    """Test that QA Node executes correct command for JS/TS files."""
    mock_sandbox = MagicMock()
    mock_sandbox.execute.return_value = MagicMock(exit_code=1, output="Test failed: ReferenceError")
    monkeypatch.setattr("agent.multi_agent.nodes.get_sandbox_backend", AsyncMock(return_value=mock_sandbox))

    state = {
        "target_files": ["ui.ts", "tests/ui.test.ts"]
    }
    config = {"configurable": {"thread_id": "thread-123"}}

    res = await qa_node(state, config)
    assert "test_results" in res
    assert res["test_results"]["success"] is False
    assert "npm test" in res["test_results"]["test_cmd"]
    assert "ui.test.ts" in res["test_results"]["test_cmd"]

def test_route_after_qa():
    # Case 1: QA passes -> ends graph
    state_pass = {"test_results": {"success": True}}
    assert route_after_qa(state_pass) == "__end__"

    # Case 2: QA fails, under retry limit -> routes back to coder
    state_fail_retry = {
        "test_results": {"success": False},
        "config": {"coder_retries": 1}
    }
    assert route_after_qa(state_fail_retry) == "coder"

    # Case 3: QA fails, reaches retry limit -> ends graph
    state_fail_limit = {
        "test_results": {"success": False},
        "config": {"coder_retries": 3}
    }
    assert route_after_qa(state_fail_limit) == "__end__"

def test_get_multi_agent_graph():
    """Verify that the multi-agent graph compiles cleanly without any errors."""
    config = {"configurable": {"agent_model_id": ("openai:gpt-5.5", "medium")}}
    graph = get_multi_agent_graph(config)
    assert graph is not None
    # Pregel/CompiledStateGraph is compiled LangGraph
    assert graph.__class__.__name__ in ("Pregel", "CompiledStateGraph")
