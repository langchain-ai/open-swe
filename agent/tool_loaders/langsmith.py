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
from langgraph.config import get_config
from langsmith import AsyncClient as AsyncLangSmithClient
from langsmith import Client as LangSmithClient

from agent.dashboard.team_credentials import (
    LangSmithCredentials,
)
from agent.dashboard.team_credentials import (
    get_langsmith_credentials as get_team_langsmith_credentials,
)
from agent.dashboard.threads.summary import thread_is_owner, thread_is_readable
from agent.dashboard.user_credentials import (
    get_langsmith_credentials as get_user_langsmith_credentials,
)
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import resolve_participant
from agent.utils.tracing import tracing_project

logger = logging.getLogger(__name__)

_MAX_LIST_RUNS = 50


def langsmith_client(creds: LangSmithCredentials):
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


async def _authorized_thread_metadata(thread_id: str, login: str) -> dict[str, Any]:
    metadata = thread_metadata(await langgraph_client().threads.get(thread_id))
    if not thread_is_readable(metadata, login):
        raise ValueError("Thread unavailable")
    return metadata


async def _run_is_readable(client: Any, run: Any, login: str) -> bool:
    try:
        trace_id = getattr(run, "trace_id", None)
        root = run
        if trace_id and str(trace_id) != str(run.id):
            root = await client.read_run(str(trace_id))
            if str(root.id) != str(trace_id) or getattr(root, "parent_run_id", None):
                return False
        elif getattr(run, "parent_run_id", None):
            return False
        for item in (root,) if root is run else (root, run):
            metadata = (getattr(item, "extra", None) or {}).get("metadata", {})
            thread_id = metadata.get("thread_id")
            if thread_id is not None:
                if not isinstance(thread_id, str) or not thread_id:
                    return False
                target = await _authorized_thread_metadata(thread_id, login)
                if target.get("visibility") == "private":
                    caller_id = get_config().get("configurable", {}).get("thread_id")
                    if not isinstance(caller_id, str) or not caller_id:
                        return False
                    caller = await _authorized_thread_metadata(caller_id, login)
                    if caller.get("visibility") != "private" or not thread_is_owner(caller, login):
                        return False
            elif item is root:
                project_id = getattr(item, "session_id", None)
                if not project_id:
                    return False
                project = await client.read_project(project_id=project_id)
                if not project.name or project.name == tracing_project():
                    return False
        for child in getattr(run, "child_runs", None) or []:
            if not await _run_is_readable(client, child, login):
                return False
        return True
    except Exception:
        return False


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
            login = await resolve_participant(on_behalf_of)
            creds = await _creds_for(login, allow_team=allow_team)
            async with langsmith_client(creds) as client:
                run = await client.read_run(run_id)
                if not await _run_is_readable(client, run, login):
                    return {"success": False, "error": "Trace unavailable or access denied."}
                if load_child_runs:
                    run = await asyncio.to_thread(_read_run_with_children, creds, run_id)
                    if not await _run_is_readable(client, run, login):
                        return {"success": False, "error": "Trace unavailable or access denied."}
        except Exception:  # noqa: BLE001
            logger.warning("langsmith_get_trace failed", exc_info=True)
            return {"success": False, "error": "LangSmith request unavailable or access denied."}
        return {"success": True, "run": _serialize_run(run)}

    async def langsmith_list_runs(
        on_behalf_of: str,
        project_name: str,
        limit: int = 20,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List recent LangSmith runs in a project.

        Args:
            on_behalf_of: GitHub login of the thread participant to act for.
            project_name: The LangSmith project (tracing project) name.
            limit: Maximum runs to return (capped at 50).
            filter: Optional LangSmith filter string (e.g. "eq(status, 'error')").

        Returns:
            Dictionary with a list of runs, or an error message.
        """
        capped = max(1, min(limit, _MAX_LIST_RUNS))

        try:
            login = await resolve_participant(on_behalf_of)
            creds = await _creds_for(login, allow_team=allow_team)
            async with langsmith_client(creds) as client:
                runs = [
                    run
                    async for run in client.list_runs(
                        project_name=project_name,
                        filter=filter,
                        limit=capped,
                    )
                    if await _run_is_readable(client, run, login)
                ]
        except Exception:  # noqa: BLE001
            logger.warning("langsmith_list_runs failed", exc_info=True)
            return {"success": False, "error": "LangSmith request unavailable or access denied."}
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
