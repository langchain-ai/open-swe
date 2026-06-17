"""Server-side, read-only Currents.dev tools for e2e test investigation.

Credentials are per-user (encrypted at rest in the user-credentials Store
namespace). The tools run in the LangGraph server process and call the
Currents REST API directly — the sandbox never holds a Currents key.

The surface is intentionally read-only: fetch runs, instances, test results,
and list projects so the agent can dig into e2e test failures including
screenshots and DOM snapshots.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool

from ..dashboard.user_credentials import CURRENTS_API_BASE, get_currents_api_key

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }


async def _get(path: str, api_key: str, **params: Any) -> dict[str, Any]:
    url = f"{CURRENTS_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers(api_key), params=params)
        resp.raise_for_status()
        return resp.json()


def _make_tools(api_key: str) -> list[BaseTool]:
    async def currents_list_projects(
        limit: int = 10,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List Currents.dev projects for your organization.

        Args:
            limit: Maximum number of items to return (default 10, max 50).
            starting_after: Cursor for pagination.

        Returns:
            Dictionary with project list, or an error message.
        """
        try:
            params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
            if starting_after:
                params["starting_after"] = starting_after
            return await _get("/projects", api_key, **params)
        except Exception as e:  # noqa: BLE001
            logger.warning("currents_list_projects failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def currents_get_run(run_id: str) -> dict[str, Any]:
        """Get a single Currents.dev test run by ID with full details.

        Use this to inspect a specific e2e test run including specs,
        screenshots, video URLs, and test stats.

        Args:
            run_id: The Currents run ID (e.g. "run_abc123").

        Returns:
            Dictionary with the run details, or an error message.
        """
        try:
            return await _get(f"/runs/{run_id}", api_key)
        except Exception as e:  # noqa: BLE001
            logger.warning("currents_get_run failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def currents_find_run(
        project_id: str,
        ci_build_id: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Find the most recent completed Currents.dev run matching criteria.

        Args:
            project_id: The Currents project ID (e.g. "proj_abc123").
            ci_build_id: Optional CI build ID to find an exact run.
            branch: Optional branch name or prefix (append * for prefix match).

        Returns:
            Dictionary with the run details, or an error message.
        """
        try:
            params: dict[str, Any] = {"projectId": project_id}
            if ci_build_id:
                params["ciBuildId"] = ci_build_id
            if branch:
                params["branch"] = branch
            return await _get("/runs/find", api_key, **params)
        except Exception as e:  # noqa: BLE001
            logger.warning("currents_find_run failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def currents_list_project_runs(
        project_id: str,
        limit: int = 10,
        status: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """List runs for a Currents.dev project with optional filters.

        Args:
            project_id: The Currents project ID.
            limit: Maximum number of runs to return (default 10, max 50).
            status: Optional status filter: PASSED, FAILED, RUNNING, FAILING.
            branch: Optional branch filter (append * for prefix match).

        Returns:
            Dictionary with a list of runs, or an error message.
        """
        try:
            params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
            if status:
                params["status"] = status
            if branch:
                params["branches[]"] = branch
            return await _get(f"/projects/{project_id}/runs", api_key, **params)
        except Exception as e:  # noqa: BLE001
            logger.warning("currents_list_project_runs failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def currents_get_instance(instance_id: str) -> dict[str, Any]:
        """Get a single Currents.dev spec file execution instance by ID.

        An instance represents one spec file's execution within a run,
        including detailed test results, errors, and attempt history.

        Args:
            instance_id: The Currents instance ID (e.g. "inst_abc123").

        Returns:
            Dictionary with the instance details, or an error message.
        """
        try:
            return await _get(f"/instances/{instance_id}", api_key)
        except Exception as e:  # noqa: BLE001
            logger.warning("currents_get_instance failed", exc_info=True)
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    return [
        StructuredTool.from_function(coroutine=currents_list_projects),
        StructuredTool.from_function(coroutine=currents_get_run),
        StructuredTool.from_function(coroutine=currents_find_run),
        StructuredTool.from_function(coroutine=currents_list_project_runs),
        StructuredTool.from_function(coroutine=currents_get_instance),
    ]


async def load_currents_tools(login: str) -> list[BaseTool]:
    """Return read-only Currents tools when the user has connected Currents."""
    api_key = await get_currents_api_key(login)
    if not api_key:
        return []
    return _make_tools(api_key)
