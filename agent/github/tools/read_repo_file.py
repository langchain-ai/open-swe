"""Tool: ``read_repo_file``. Read repo files/dirs over the GitHub API (no sandbox).

The PR chat agent has no sandbox, so it reads source at a specific ref through
the GitHub contents API. Repo coordinates and a read-only token come from the
run config (seeded by the dashboard chat proxy).
"""

import base64
from typing import Any

import httpx2

from agent.github.checks import github_headers
from agent.run_config import RunConfig
from agent.tools.errors import ToolError

_GITHUB_API = "https://api.github.com"
_MAX_FILE_BYTES = 256 * 1024


def _chat_repo_context() -> tuple[str, str, str | None, str | None]:
    cfg = RunConfig.from_runtime()
    return (
        cfg.chat_repo_owner or "",
        cfg.chat_repo_name or "",
        cfg.chat_github_token or None,
        cfg.chat_head_sha or None,
    )


async def read_repo_file(path: str, ref: str | None = None) -> dict[str, Any]:
    """Implement the `read_repo_file` tool."""
    owner, repo, token, head_sha = _chat_repo_context()
    if not owner or not repo:
        raise ToolError("repository context unavailable")
    if not token:
        raise ToolError("GitHub credentials unavailable; repository source was not read")

    clean_path = path.strip().lstrip("/")
    resolved_ref = (ref or head_sha or "").strip()
    params = {"ref": resolved_ref} if resolved_ref else None
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{clean_path}"
    headers = github_headers(token)
    try:
        async with httpx2.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx2.HTTPError as exc:
        raise ToolError(f"GitHub request failed: {exc!s}") from exc

    if response.status_code == 404:
        raise ToolError(f"not found: {clean_path} @ {resolved_ref or 'default'}")
    if response.status_code >= 400:
        raise ToolError(f"GitHub returned {response.status_code}")

    payload = response.json()
    if isinstance(payload, list):
        entries = [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "path": item.get("path"),
            }
            for item in payload
            if isinstance(item, dict)
        ]
        return {"success": True, "path": clean_path, "ref": resolved_ref, "entries": entries}

    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ToolError(f"unsupported content type for {clean_path}")

    encoded = payload.get("content")
    if not isinstance(encoded, str):
        raise ToolError("file content unavailable (too large for contents API)")
    raw = base64.b64decode(encoded)
    truncated = len(raw) > _MAX_FILE_BYTES
    text = raw[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return {
        "success": True,
        "path": clean_path,
        "ref": resolved_ref,
        "content": text,
        "truncated": truncated,
    }
