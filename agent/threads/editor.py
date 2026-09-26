"""Launch an in-sandbox code editor and share it over a workspace service URL."""

import logging
import shlex
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from agent.dashboard.deps import SESSION_DEP
from agent.sandboxes.providers.langsmith import (
    connect_async_langsmith_sandbox,
    create_workspace_service_url,
)
from agent.threads.handlers import get_dashboard_terminal_sandbox

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

EDITOR_PORT = 8080
EDITOR_START_TIMEOUT_SECONDS = 120
EDITOR_HEALTH_CHECK = f"curl -sf -o /dev/null http://127.0.0.1:{EDITOR_PORT}/"


@router.post("/threads/{thread_id}/editor/connect")
async def api_thread_editor_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, str]:
    sandbox_id, repo_name = await get_dashboard_terminal_sandbox(
        thread_id, session["sub"], email=session.get("email")
    )
    response.headers["Cache-Control"] = "no-store"
    folder = f"/workspace/{repo_name}" if repo_name else "/workspace"
    quoted = shlex.quote(folder)
    script = (
        "set -e\n"
        "mkdir -p /tmp/open-swe-editor\n"
        f"if ! {EDITOR_HEALTH_CHECK}; then\n"
        "  nohup /opt/openvscode/ovs/bin/openvscode-server --host 0.0.0.0"
        f" --port {EDITOR_PORT} --without-connection-token"
        " --server-data-dir /tmp/open-swe-editor/data"
        f" --default-folder {quoted}"
        " >/tmp/open-swe-editor/server.log 2>&1 &\n"
        "fi\n"
        f"for i in $(seq 1 {EDITOR_START_TIMEOUT_SECONDS // 2}); do\n"
        f"  {EDITOR_HEALTH_CHECK} && exit 0\n"
        "  sleep 0.5\n"
        "done\n"
        f"echo 'editor did not answer on port {EDITOR_PORT}' >&2; exit 1\n"
    )
    try:
        client, sandbox = await connect_async_langsmith_sandbox(sandbox_id)
    except Exception as exc:
        logger.exception("sandbox unreachable", extra={"thread_id": thread_id})
        raise HTTPException(502, "sandbox is unreachable") from exc
    try:
        result = await sandbox.run(script, timeout=EDITOR_START_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.exception("editor start failed", extra={"thread_id": thread_id})
        raise HTTPException(502, "editor failed to start") from exc
    finally:
        await client.aclose()
    if not result.success:
        raise HTTPException(502, result.stderr.strip()[-200:] or "editor failed to start")
    url = await create_workspace_service_url(sandbox_id, EDITOR_PORT)
    return {"url": url, "port": str(EDITOR_PORT)}
