"""Server-side, read-only LangSmith tools.

Credentials live in team settings (encrypted at rest). The tools run in the
LangGraph server process and call the LangSmith API directly — the sandbox never
holds a LangSmith key. The surface is intentionally read-only: fetch a single
run/trace and list recent runs in a project.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from ..dashboard.team_credentials import LangSmithCredentials, get_langsmith_credentials

logger = logging.getLogger(__name__)

_MAX_LIST_RUNS = 50


def _client(creds: LangSmithCredentials):
    from langsmith import Client

    return Client(api_key=creds.api_key, api_url=creds.endpoint)


def _serialize_run(run: Any) -> dict[str, Any]:
    def _get(name: str) -> Any:
        value = getattr(run, name, None)
        return str(value) if value is not None else None

    return {
        "id": _get("id"),
        "name": getattr(run, "name", None),
        "run_type": getattr(run, "run_type", None),
        "status": getattr(run, "status", None),
        "error": getattr(run, "error", None),
        "start_time": _get("start_time"),
        "end_time": _get("end_time"),
        "trace_id": _get("trace_id"),
        "inputs": getattr(run, "inputs", None),
        "outputs": getattr(run, "outputs", None),
    }


def _make_tools(creds: LangSmithCredentials) -> list[BaseTool]:
    async def langsmith_get_trace(run_id: str, load_child_runs: bool = False) -> dict[str, Any]:
        """Fetch a single LangSmith run (trace) by its run ID.

        Args:
            run_id: The LangSmith run UUID.
            load_child_runs: Include nested child runs when True.

        Returns:
            Dictionary with the run details, or an error message.
        """
        try:
            run = await asyncio.to_thread(
                _client(creds).read_run, run_id, load_child_runs=load_child_runs
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_get_trace failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        return {"success": True, "run": _serialize_run(run)}

    async def langsmith_list_runs(
        project_name: str,
        limit: int = 20,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List recent LangSmith runs in a project.

        Args:
            project_name: The LangSmith project (tracing project) name.
            limit: Maximum runs to return (capped at 50).
            filter: Optional LangSmith filter string (e.g. "eq(status, 'error')").

        Returns:
            Dictionary with a list of runs, or an error message.
        """
        capped = max(1, min(limit, _MAX_LIST_RUNS))

        def _list() -> list[Any]:
            return list(
                _client(creds).list_runs(
                    project_name=project_name,
                    filter=filter,
                    limit=capped,
                )
            )

        try:
            runs = await asyncio.to_thread(_list)
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_list_runs failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        return {"success": True, "runs": [_serialize_run(r) for r in runs]}

    return [
        StructuredTool.from_function(coroutine=langsmith_get_trace),
        StructuredTool.from_function(coroutine=langsmith_list_runs),
    ]


async def load_langsmith_tools() -> list[BaseTool]:
    """Return read-only LangSmith tools when the team has connected LangSmith."""
    creds = await get_langsmith_credentials()
    if creds is None:
        return []
    return _make_tools(creds)
