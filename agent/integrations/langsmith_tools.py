"""Server-side, read-only LangSmith tools.

Credentials are encrypted at rest. The tools run in the LangGraph server process
and call the LangSmith API directly — the sandbox never holds a LangSmith key.
The surface is intentionally read-only: fetch a single run/trace and list recent
runs in a project.
"""

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langsmith import AsyncClient as AsyncLangSmithClient
from langsmith import Client as LangSmithClient

from ..dashboard.team_credentials import (
    LangSmithCredentials,
)
from ..dashboard.team_credentials import (
    get_langsmith_credentials as get_team_langsmith_credentials,
)
from ..dashboard.user_credentials import get_langsmith_credentials as get_user_langsmith_credentials
from ..utils.thread_participants import resolve_participant

logger = logging.getLogger(__name__)

_MAX_LIST_RUNS = 50


def _client(creds: LangSmithCredentials):
    return AsyncLangSmithClient(api_key=creds.api_key, api_url=creds.endpoint)


def _read_run_with_children(creds: LangSmithCredentials, run_id: str):
    client = LangSmithClient(api_key=creds.api_key, api_url=creds.endpoint)
    try:
        return client.read_run(run_id, load_child_runs=True)
    finally:
        client.close()


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


async def _creds_for(on_behalf_of: str, *, allow_team: bool) -> LangSmithCredentials:
    login = await resolve_participant(on_behalf_of)
    creds = await get_user_langsmith_credentials(login)
    if creds is None and allow_team:
        creds = await get_team_langsmith_credentials()
    if creds is None:
        raise ValueError(f"{login} has no LangSmith credentials configured.")
    return creds


def _make_tools(*, allow_team: bool) -> list[BaseTool]:
    async def langsmith_get_trace(
        on_behalf_of: str, run_id: str, load_child_runs: bool = False
    ) -> dict[str, Any]:
        """Fetch a single LangSmith run (trace) by its run ID.

        Args:
            on_behalf_of: GitHub login of the thread participant to act for.
            run_id: The LangSmith run UUID.
            load_child_runs: Include nested child runs when True.

        Returns:
            Dictionary with the run details, or an error message.
        """
        try:
            creds = await _creds_for(on_behalf_of, allow_team=allow_team)
            if load_child_runs:
                # AsyncClient.read_run has no load_child_runs; the sync one runs off-loop.
                run = await asyncio.to_thread(_read_run_with_children, creds, run_id)
            else:
                async with _client(creds) as client:
                    run = await client.read_run(run_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_get_trace failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        return {"success": True, "run": _serialize_run(run)}

    async def langsmith_list_runs(
        on_behalf_of: str,
        project_name: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List runs by project_name for human-readable names or project_id for UUIDs from LangSmith URLs."""
        if (project_name is None) == (project_id is None):
            return {"success": False, "error": "Provide exactly one of project_name or project_id"}

        capped = max(1, min(limit, _MAX_LIST_RUNS))
        identifier = (
            f"project_id={project_id!r}"
            if project_id is not None
            else f"project_name={project_name!r}"
        )

        try:
            creds = await _creds_for(on_behalf_of, allow_team=allow_team)
            async with _client(creds) as client:
                runs = [
                    run
                    async for run in client.list_runs(
                        project_name=project_name,
                        project_id=project_id,
                        filter=filter,
                        limit=capped,
                    )
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_list_runs failed", exc_info=True)
            error = f"{type(e).__name__}: {e}"
            if "notfound" in type(e).__name__.lower() or "not found" in str(e).lower():
                error += f" (lookup used {identifier}; use project_id for UUIDs and project_name for names)"
            return {"success": False, "error": error}
        return {"success": True, "runs": [_serialize_run(r) for r in runs]}

    return [
        StructuredTool.from_function(coroutine=langsmith_get_trace),
        StructuredTool.from_function(coroutine=langsmith_list_runs),
    ]


async def load_langsmith_tools(
    login: str | None = None, *, allow_team: bool = True
) -> list[BaseTool]:
    """Return read-only LangSmith tools when ``login`` can reach LangSmith.

    ``login`` decides only whether the thread offers these tools; each call names
    the participant to act for and resolves their credentials then, so the tool
    schema does not change with whoever is speaking.
    """
    creds = await get_user_langsmith_credentials(login) if login else None
    if creds is None and allow_team:
        creds = await get_team_langsmith_credentials()
    return _make_tools(allow_team=allow_team) if creds else []
