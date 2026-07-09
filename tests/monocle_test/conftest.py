"""Pytest scaffold for the Open SWE Monocle test suite.

Enables Monocle tracing, loads the repo `.env`, and exposes ``run_openswe`` --
the single entry the live tests use to drive the agent under instrumentation.
"""
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from monocle_apptrace import setup_monocle_telemetry

HERE = Path(__file__).resolve().parent
TRACES = HERE / "traces"
REPO_ROOT = HERE.parent.parent

setup_monocle_telemetry(workflow_name="open-swe")
load_dotenv(REPO_ROOT / ".env")


def run_openswe(message: str) -> str:
    """Run Open SWE's read-only ``chat`` graph once and return its final text.

    The ``chat`` graph is the read-only repo/PR Q&A agent (tools: read_file,
    search_repo_code, list_review_findings, web_search, fetch_url). It has no
    sandbox and never commits, pushes, or opens a PR, so driving it here has no
    side effects. ``SANDBOX_TYPE=local`` keeps everything on the local machine.
    """
    import asyncio

    from langchain_core.messages import HumanMessage

    os.environ.setdefault("SANDBOX_TYPE", "local")
    from agent.chat import get_chat_agent

    repo_owner = os.environ.get("OPENSWE_CHAT_REPO_OWNER", "langchain-ai")
    repo_name = os.environ.get("OPENSWE_CHAT_REPO_NAME", "open-swe")
    config = {
        "configurable": {
            "thread_id": f"monocle-test-{uuid.uuid4().hex[:8]}",
            "__is_for_execution__": True,
            "source": "github",
            "chat_repo_owner": repo_owner,
            "chat_repo_name": repo_name,
        }
    }

    async def _run() -> str:
        agent = await get_chat_agent(config)
        result = await agent.ainvoke({"messages": [HumanMessage(content=message)]}, config)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        return str(messages[-1].content) if messages else str(result)

    return asyncio.run(_run())
