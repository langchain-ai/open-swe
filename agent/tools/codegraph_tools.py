import logging

from langchain_core.tools import tool
from langgraph.graph.state import RunnableConfig

from agent.utils.codegraph import ensure_codegraph_indexed
from agent.utils.sandbox_paths import aresolve_sandbox_work_dir
from agent.utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)


@tool
async def codegraph_search(query: str, config: RunnableConfig) -> str:
    """Search for symbols (functions, classes, methods, variables) by name in the codebase using CodeGraph.

    Args:
        query: The symbol name or search term to look up.
    """
    thread_id = config["configurable"].get("thread_id")
    if not thread_id:
        return "Error: No thread_id configured."
    try:
        sandbox = await get_sandbox_backend(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox)
        if not ensure_codegraph_indexed(sandbox, work_dir):
            return "Error: CodeGraph index is not initialized or failed to sync."
        res = sandbox.execute(f'cd {work_dir} && codegraph query "{query}" --limit 10')
        return res.output
    except Exception as e:
        logger.warning("Error in codegraph_search: %s", e)
        return f"Error running codegraph search: {e}"


@tool
async def codegraph_callers(symbol: str, config: RunnableConfig) -> str:
    """Find all callers of a given function or method in the codebase using CodeGraph.

    Args:
        symbol: The name of the function or method (e.g. 'parse_config' or 'User.save').
    """
    thread_id = config["configurable"].get("thread_id")
    if not thread_id:
        return "Error: No thread_id configured."
    try:
        sandbox = await get_sandbox_backend(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox)
        if not ensure_codegraph_indexed(sandbox, work_dir):
            return "Error: CodeGraph index is not initialized or failed to sync."
        res = sandbox.execute(f'cd {work_dir} && codegraph callers "{symbol}"')
        return res.output
    except Exception as e:
        logger.warning("Error in codegraph_callers: %s", e)
        return f"Error running codegraph callers: {e}"


@tool
async def codegraph_callees(symbol: str, config: RunnableConfig) -> str:
    """Find all functions or methods called by a given function in the codebase using CodeGraph.

    Args:
        symbol: The name of the function or method.
    """
    thread_id = config["configurable"].get("thread_id")
    if not thread_id:
        return "Error: No thread_id configured."
    try:
        sandbox = await get_sandbox_backend(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox)
        if not ensure_codegraph_indexed(sandbox, work_dir):
            return "Error: CodeGraph index is not initialized or failed to sync."
        res = sandbox.execute(f'cd {work_dir} && codegraph callees "{symbol}"')
        return res.output
    except Exception as e:
        logger.warning("Error in codegraph_callees: %s", e)
        return f"Error running codegraph callees: {e}"


@tool
async def codegraph_impact(symbol: str, config: RunnableConfig) -> str:
    """Analyze the transitive impact radius of changing a specific symbol using CodeGraph.

    Use this tool before editing a symbol to see what other files/methods might be affected.

    Args:
        symbol: The name of the symbol to analyze.
    """
    thread_id = config["configurable"].get("thread_id")
    if not thread_id:
        return "Error: No thread_id configured."
    try:
        sandbox = await get_sandbox_backend(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox)
        if not ensure_codegraph_indexed(sandbox, work_dir):
            return "Error: CodeGraph index is not initialized or failed to sync."
        res = sandbox.execute(f'cd {work_dir} && codegraph impact "{symbol}"')
        return res.output
    except Exception as e:
        logger.warning("Error in codegraph_impact: %s", e)
        return f"Error running codegraph impact: {e}"
