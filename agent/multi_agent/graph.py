import logging
import os
from typing import Any, Literal

from dotenv import load_dotenv

# Robustly find and load the .env file in project root
cur_dir = os.path.dirname(os.path.abspath(__file__))
while cur_dir and not os.path.exists(os.path.join(cur_dir, ".env")):
    parent = os.path.dirname(cur_dir)
    if parent == cur_dir:
        break
    cur_dir = parent
env_path = os.path.join(cur_dir, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path, override=True)
else:
    load_dotenv(override=True)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

from agent.utils.sandbox_state import get_sandbox_backend

from .nodes import architect_node, pm_node, qa_node
from .state import MultiAgentState

logger = logging.getLogger(__name__)


async def coder_node(state: MultiAgentState, config: RunnableConfig) -> dict[str, Any]:
    """Coder Node: Invokes the core open-swe agent injected with PM & Architect context."""
    logger.info("Entering Coder Node to run primary agent")

    # 1. Update retry count
    current_config = state.get("config", {})
    retries = current_config.get("coder_retries", 0)

    # 2. Build custom context prompt containing test plans and file targets
    context_prompt = f"""---
### MULTI-AGENT ORCHESTRATION CONTEXT
The PM and Architect agents have pre-analyzed this issue to focus your scope and prevent token waste.

#### 📋 Precise Test Plan:
{state.get("test_plan", "")}

#### 📂 Target Files to Read/Modify:
{", ".join(state.get("target_files", []))}
---"""

    # Prepend this context as a SystemMessage to guide the Coding Agent
    messages = [SystemMessage(content=context_prompt)] + state["messages"]

    # If this is a self-reflection retry, append the QA failure logs to guide the Coder
    if retries > 0 and state.get("test_results"):
        qa_feedback = f"""⚠️ Previous test execution failed with exit code {state["test_results"].get("exit_code")}.
Test Command: {state["test_results"].get("test_cmd")}
Error Output:
{state["test_results"].get("output")}

Please reflect on these test failures and apply the necessary corrections."""
        messages.append(HumanMessage(content=qa_feedback))

    # 3. Import and resolve core deep agent
    from agent.server import get_agent

    coder_config = config.copy()
    if "configurable" in config:
        coder_config["configurable"] = config["configurable"].copy()
    else:
        coder_config["configurable"] = {}
    coder_config["configurable"]["in_coder_node"] = True

    core_agent = await get_agent(coder_config)

    # 4. Invoke the core agent
    logger.info("Invoking primary Open-SWE Agent (Retry count: %d)", retries)
    res = await core_agent.ainvoke({"messages": messages}, coder_config)

    # Extract returned messages (excluding our injected context if possible or returning all)
    res_messages = res.get("messages", [])

    # 5. Extract git diff in sandbox to capture modified diffs
    thread_id = config["configurable"].get("thread_id")
    try:
        sandbox = await get_sandbox_backend(thread_id)
        diff_res = sandbox.execute("git diff")
        modified_diffs = diff_res.output.strip()
    except Exception as e:
        logger.warning("Failed to extract git diff in sandbox: %s", e)
        modified_diffs = ""

    # Increment retry count
    new_config = current_config.copy()
    new_config["coder_retries"] = retries + 1

    return {"messages": res_messages, "modified_diffs": modified_diffs, "config": new_config}


def route_after_qa(state: MultiAgentState) -> Literal["coder", "__end__"]:
    """Conditional router that checks QA test success or retry limit."""
    results = state.get("test_results", {})
    if results.get("success", False):
        logger.info("QA Node succeeded! Code modifications passed all checks. Ending workflow.")
        return "__end__"

    retries = state.get("config", {}).get("coder_retries", 0)
    if retries >= 3:
        logger.warning(
            "QA Node failed but reached maximum retry limit (3). Submitting PR with best effort."
        )
        return "__end__"

    logger.info(
        "QA Node failed. Routing back to Coder Node for self-reflection. Retry: %d/3", retries + 1
    )
    return "coder"


def get_multi_agent_graph(config: RunnableConfig) -> Pregel:
    """Build and compile the multi-agent StateGraph orchestrator."""
    builder = StateGraph(MultiAgentState)

    # Define Nodes
    builder.add_node("pm", pm_node)
    builder.add_node("architect", architect_node)
    builder.add_node("coder", coder_node)
    builder.add_node("qa", qa_node)

    # Connect Edges
    builder.add_edge(START, "pm")
    builder.add_edge("pm", "architect")
    builder.add_edge("architect", "coder")
    builder.add_edge("coder", "qa")

    # QA Conditional Routing (Self-Reflection Loop)
    builder.add_conditional_edges("qa", route_after_qa, {"coder": "coder", "__end__": END})

    return builder.compile()
