"""Coder workspace sandbox backend implementation.

Uses the Coder REST API to create/reconnect workspaces and the Coder CLI
(coder ssh) to execute commands inside them.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from typing import Any

import httpx
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox


class CoderSandbox(BaseSandbox):
    """Coder workspace sandbox backend.

    Commands are executed via `coder ssh <workspace> -- bash -c '<cmd>'`.
    File operations inherit from BaseSandbox (which uses execute() internally).
    """

    def __init__(self, workspace_name: str, coder_url: str, session_token: str) -> None:
        self._workspace_name = workspace_name
        self._env = {
            **os.environ,
            "CODER_URL": coder_url,
            "CODER_SESSION_TOKEN": session_token,
        }

    @property
    def id(self) -> str:
        """Workspace name — stable, human-readable, unique per owner."""
        return self._workspace_name

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a shell command in the Coder workspace via SSH."""
        effective_timeout = timeout if timeout is not None else 300
        try:
            result = subprocess.run(
                ["coder", "ssh", self._workspace_name, "--", "bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=self._env,
            )
            output = result.stdout or ""
            if result.stderr:
                output += ("\n" + result.stderr) if output else result.stderr
            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {effective_timeout}s",
                exit_code=124,
                truncated=False,
            )


def _wait_for_workspace(
    client: httpx.Client,
    workspace_id: str,
    timeout: int = 300,
) -> None:
    """Poll until workspace build reaches 'running' status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v2/workspaces/{workspace_id}")
        resp.raise_for_status()
        ws = resp.json()
        status = ws.get("latest_build", {}).get("status", "")
        if status == "running":
            return
        if status in ("failed", "canceled", "canceling"):
            raise RuntimeError(f"Workspace build failed with status: {status!r}")
        time.sleep(3)
    raise RuntimeError(f"Workspace did not become ready within {timeout}s")


def _get_org_id(client: httpx.Client) -> str:
    """Return the first organization ID for the current user."""
    resp = client.get("/api/v2/organizations")
    resp.raise_for_status()
    orgs = resp.json()
    if not orgs:
        raise RuntimeError("No Coder organizations found")
    return orgs[0]["id"]


def _get_or_start_workspace(client: httpx.Client, workspace_name: str) -> dict[str, Any]:
    """Return the workspace dict, starting it first if stopped/failed."""
    resp = client.get(f"/api/v2/users/me/workspace/{workspace_name}")
    resp.raise_for_status()
    workspace = resp.json()

    status = workspace.get("latest_build", {}).get("status", "")
    if status in ("stopped", "failed"):
        resp = client.post(
            f"/api/v2/workspaces/{workspace['id']}/builds",
            json={"transition": "start"},
        )
        resp.raise_for_status()
        _wait_for_workspace(client, workspace["id"])
    elif status != "running":
        _wait_for_workspace(client, workspace["id"])

    return workspace


def _create_workspace(
    client: httpx.Client,
    workspace_name: str,
    template_name: str,
    workspace_size: str,
) -> dict[str, Any]:
    """Create a new Coder workspace from the given template."""
    org_id = _get_org_id(client)

    # Resolve template
    resp = client.get(f"/api/v2/organizations/{org_id}/templates/{template_name}")
    resp.raise_for_status()
    template = resp.json()
    template_id = template["id"]

    # Get latest template version
    resp = client.get(f"/api/v2/templates/{template_id}/versions?limit=1")
    resp.raise_for_status()
    versions = resp.json()
    if not versions:
        raise RuntimeError(f"No versions found for template {template_name!r}")
    template_version_id = versions[0]["id"]

    # Create workspace
    resp = client.post(
        f"/api/v2/organizations/{org_id}/members/me/workspaces",
        json={
            "name": workspace_name,
            "template_id": template_id,
            "template_version_id": template_version_id,
            "rich_parameter_values": [
                {"name": "machine_size", "value": workspace_size},
            ],
        },
    )
    resp.raise_for_status()
    workspace = resp.json()

    _wait_for_workspace(client, workspace["id"])
    return workspace


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Persist workspace name in LangGraph thread metadata for reconnection."""
    try:
        import asyncio

        from langgraph.config import get_config
        from langgraph_sdk import get_client

        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return
        lg_client = get_client()

        async def _update() -> None:
            await lg_client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_id},
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_update())
        else:
            loop.create_task(_update())
    except Exception:
        # Best-effort: ignore failures
        pass


def create_coder_sandbox(sandbox_id: str | None = None) -> SandboxBackendProtocol:
    """Create or reconnect to a Coder workspace sandbox.

    Required env vars:
        CODER_URL             — Coder server URL (e.g. https://code.redo.run)
        CODER_SESSION_TOKEN   — API token

    Optional env vars:
        CODER_TEMPLATE_NAME   — template name (default: open-swe-sandbox)
        CODER_WORKSPACE_SIZE  — 'small' or 'large' (default: small)

    Args:
        sandbox_id: Existing workspace name to reconnect to; None to create new.

    Returns:
        CoderSandbox instance.
    """
    coder_url = os.environ.get("CODER_URL", "https://code.redo.run")
    session_token = os.environ.get("CODER_SESSION_TOKEN")
    if not session_token:
        raise ValueError("CODER_SESSION_TOKEN environment variable is required")

    template_name = os.environ.get("CODER_TEMPLATE_NAME", "open-swe-sandbox")
    workspace_size = os.environ.get("CODER_WORKSPACE_SIZE", "small")

    with httpx.Client(
        base_url=coder_url,
        headers={"Coder-Session-Token": session_token},
        timeout=30.0,
    ) as client:
        if sandbox_id:
            workspace = _get_or_start_workspace(client, sandbox_id)
        else:
            workspace_name = f"open-swe-{uuid.uuid4().hex[:12]}"
            workspace = _create_workspace(client, workspace_name, template_name, workspace_size)

    workspace_name = workspace["name"]
    _update_thread_sandbox_metadata(workspace_name)
    return CoderSandbox(workspace_name, coder_url, session_token)
