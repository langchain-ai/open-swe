"""Server-side, read-only LangSmith tools.

Credentials are encrypted at rest. The tools run in the LangGraph server process
and call the LangSmith API directly — the sandbox never holds a LangSmith key.
The surface is intentionally read-only: fetch a single run/trace and list recent
runs in a project.
"""

import asyncio
import logging
import os
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langsmith import AsyncClient as AsyncLangSmithClient
from langsmith import Client as LangSmithClient
from langsmith.utils import LangSmithNotFoundError

from agent.dashboard.team_credentials import (
    LangSmithCredentials,
)
from agent.dashboard.team_credentials import (
    get_langsmith_credentials as get_team_langsmith_credentials,
)
from agent.dashboard.user_credentials import (
    get_langsmith_credentials as get_user_langsmith_credentials,
)
from agent.utils.thread_participants import resolve_participant
from agent.utils.tracing import AGENT_TRACING_PROJECT

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


def _deployment_creds() -> LangSmithCredentials | None:
    api_key = (
        os.environ.get("LANGSMITH_API_KEY_PROD")
        or os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
    )
    if not api_key:
        return None
    endpoint = os.environ.get("LANGSMITH_ENDPOINT_PROD") or os.environ.get(
        "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
    )
    return LangSmithCredentials(api_key=api_key, endpoint=endpoint)


def _make_tools(*, allow_team: bool) -> list[BaseTool]:
    async def langsmith_get_trace(
        on_behalf_of: str, run_id: str, load_child_runs: bool = False
    ) -> dict[str, Any]:
        """Fetch a LangSmith run by ID; schedule-origin runs have no triggering human participant."""
        try:
            deployment_creds = _deployment_creds()
            if deployment_creds is not None:
                try:
                    if load_child_runs:
                        run = await asyncio.to_thread(
                            _read_run_with_children, deployment_creds, run_id
                        )
                    else:
                        async with _client(deployment_creds) as client:
                            run = await client.read_run(run_id)
                except LangSmithNotFoundError:
                    pass
                else:
                    return {"success": True, "run": _serialize_run(run)}

            creds = await _creds_for(on_behalf_of, allow_team=allow_team)
            try:
                if load_child_runs:
                    # AsyncClient.read_run has no load_child_runs; the sync one runs off-loop.
                    run = await asyncio.to_thread(_read_run_with_children, creds, run_id)
                else:
                    async with _client(creds) as client:
                        run = await client.read_run(run_id)
            except LangSmithNotFoundError:
                return {
                    "success": False,
                    "error": (
                        f"run {run_id} is not visible with {on_behalf_of}'s LangSmith "
                        "credentials; it may belong to another workspace"
                    ),
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_get_trace failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
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
            if project_name == AGENT_TRACING_PROJECT:
                creds = _deployment_creds()
                if creds is None:
                    creds = await _creds_for(on_behalf_of, allow_team=allow_team)
            else:
                creds = await _creds_for(on_behalf_of, allow_team=allow_team)
            async with _client(creds) as client:
                runs = [
                    run
                    async for run in client.list_runs(
                        project_name=project_name,
                        filter=filter,
                        limit=capped,
                    )
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_list_runs failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
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
