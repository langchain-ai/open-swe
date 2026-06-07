import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from agent.dashboard.options import DEFAULT_MODEL_ID
from agent.utils.model import make_model
from agent.utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)


def _resolve_model_id(config: RunnableConfig) -> str:
    """Helper to robustly extract model ID from both string and tuple formats."""
    val = config["configurable"].get("agent_model_id")
    if not val:
        return DEFAULT_MODEL_ID
    if isinstance(val, tuple):
        return val[0]
    return str(val)


async def pm_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """PM Node: Analyzes requirements and drafts a precise test plan and specifications."""
    logger.info("Entering PM Node to analyze task requirements")

    model_id = _resolve_model_id(config)
    model = make_model(model_id, temperature=0.2)

    task_desc = state.get("task_description", "")
    if not task_desc and state.get("messages"):
        task_desc = state["messages"][-1].content

    prompt = f"""You are a Product Manager and QA Architect Agent.
Analyze the user's coding request:
\"\"\"
{task_desc}
\"\"\"

Create a highly detailed, step-by-step test plan and functional specifications.
Detail:
1. Core features and behaviors that must be implemented.
2. Specific edge cases to consider.
3. Recommended unit test cases, including what test commands to run (e.g. pytest or npm test paths) and expected assertions.

Write a clear, concise markdown plan.
"""
    try:
        response = await model.ainvoke([HumanMessage(content=prompt)])
        test_plan = response.content.strip()
    except Exception as e:
        logger.error("PM LLM call failed: %s", e)
        test_plan = "Standard functional code modification plan."

    return {"test_plan": test_plan}


async def architect_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Architect Node: Analyzes sandbox directories and identifies target source & test files."""
    logger.info("Entering Architect Node to map codebase files")

    thread_id = config["configurable"].get("thread_id")
    target_files = []

    # 1. Query sandbox directory structure
    try:
        sandbox = await get_sandbox_backend(thread_id)
        from unittest.mock import Mock

        if isinstance(sandbox, Mock):
            workspace_files = None
        else:
            from agent.utils.codegraph import get_workspace_files_via_codegraph
            from agent.utils.sandbox_paths import aresolve_sandbox_work_dir

            try:
                work_dir = await aresolve_sandbox_work_dir(sandbox)
                workspace_files = get_workspace_files_via_codegraph(sandbox, work_dir)
            except Exception as p_err:
                logger.info("Failed to resolve sandbox work dir: %s. Using default walk.", p_err)
                workspace_files = None

        if workspace_files is None:
            logger.info(
                "CodeGraph files listing failed or disabled. Falling back to default directory walk."
            )
            # List files up to depth 3 in a cross-platform way using python executed in the sandbox
            list_cmd = (
                'python -c "import os; '
                "exclude = {'.git', '.venv', 'node_modules', '__pycache__'}; "
                "res = []; "
                "def walk(path, depth): "
                "    if depth > 3: return; "
                "    try: "
                "        for entry in os.scandir(path): "
                "            if entry.name in exclude or entry.name.startswith('.'): continue; "
                "            p = entry.path.replace(chr(92), '/'); "
                "            if p.startswith('./'): p = p[2:]; "
                "            if entry.is_file(): res.append(p); "
                "            elif entry.is_dir(): walk(entry.path, depth + 1) "
                "    except Exception: pass; "
                "walk('.', 1); "
                "print('\\n'.join(res))\""
            )
            res = sandbox.execute(list_cmd)
            workspace_files = [f.strip() for f in res.output.strip().split("\n") if f.strip()]
    except Exception as e:
        logger.warning("Failed to scan sandbox workspace: %s", e)
        workspace_files = []

    model_id = _resolve_model_id(config)
    model = make_model(model_id, temperature=0.0)

    task_desc = state.get("task_description", "")
    test_plan = state.get("test_plan", "")

    files_str = "\n".join(workspace_files[:150])
    prompt = f"""You are a Software Architect Agent.
Analyze the user request and the PM's test plan:
User Request:
{task_desc}

PM Test Plan:
{test_plan}

Here is the directory file tree of the cloned workspace:
\"\"\"
{files_str}
\"\"\"

Identify the specific source files and unit test files that are directly relevant to this task (i.e. those that should be read or modified by the coder).
Respond in EXACTLY the following JSON format:
{{
  "target_files": [
     "path/to/source_file.py",
     "path/to/test_file.py"
  ]
}}"""

    try:
        response = await model.ainvoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            target_files = data.get("target_files", [])
    except Exception as e:
        logger.error("Architect LLM call failed: %s", e)

    return {"target_files": target_files}


async def qa_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """QA Node: Runs unit tests inside the sandbox and logs detailed feedback."""
    logger.info("Entering QA Node to run unit tests")

    thread_id = config["configurable"].get("thread_id")
    target_files = state.get("target_files", [])

    # Try to resolve affected tests using CodeGraph
    test_files = []
    try:
        sandbox = await get_sandbox_backend(thread_id)
        from unittest.mock import Mock

        if isinstance(sandbox, Mock):
            test_files_cg = None
        else:
            from agent.utils.codegraph import get_affected_tests_via_codegraph
            from agent.utils.sandbox_paths import aresolve_sandbox_work_dir

            try:
                work_dir = await aresolve_sandbox_work_dir(sandbox)
                test_files_cg = get_affected_tests_via_codegraph(sandbox, work_dir)
            except Exception as p_err:
                logger.info("Failed to resolve sandbox work dir for QA: %s", p_err)
                test_files_cg = None

        if test_files_cg is not None:
            test_files = test_files_cg
            logger.info("Retrieved test files via CodeGraph: %s", test_files)
        else:
            logger.info(
                "CodeGraph affected test retrieval returned None. Falling back to heuristic."
            )
    except Exception as e:
        logger.warning("Failed to retrieve affected tests via CodeGraph: %s", e)

    # Fallback to string matching heuristic if CodeGraph was not available or found no tests
    if not test_files:
        test_files = [f for f in target_files if "test" in f.lower()]

    test_cmd = ""
    if test_files:
        test_file = test_files[0]
        # Remove starting ./ if present
        if test_file.startswith("./"):
            test_file = test_file[2:]
        if test_file.endswith(".py"):
            test_cmd = f"uv run pytest -vvv {test_file}"
        elif test_file.endswith(".js") or test_file.endswith(".ts"):
            test_cmd = f"npm test {test_file}"

    if not test_cmd:
        # Check standard languages
        test_cmd = (
            "uv run pytest -vvv" if any(f.endswith(".py") for f in target_files) else "npm test"
        )

    logger.info("QA Node executing test command in sandbox: %s", test_cmd)

    try:
        sandbox = await get_sandbox_backend(thread_id)
        res = sandbox.execute(test_cmd)
        exit_code = res.exit_code
        output = res.output
    except Exception as e:
        exit_code = 1
        output = f"Test execution threw exception in sandbox: {e}"

    success = exit_code == 0
    logger.info("QA Node test execution result: success=%s, exit_code=%d", success, exit_code)

    return {
        "test_results": {
            "success": success,
            "exit_code": exit_code,
            "test_cmd": test_cmd,
            "output": output,
        }
    }
