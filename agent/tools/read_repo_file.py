"""Tool: ``read_repo_file``. Read repo files/dirs over the GitHub API (no sandbox).

The PR chat agent has no sandbox, so it reads source at a specific ref through
the GitHub contents API. Repo coordinates and a read-only token come from the
run config (seeded by the dashboard chat proxy).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_checks import github_headers

_GITHUB_API = "https://api.github.com"
_MAX_FILE_BYTES = 256 * 1024


def _chat_repo_context() -> tuple[str, str, str | None, str | None]:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        configurable = {}
    owner = configurable.get("chat_repo_owner")
    repo = configurable.get("chat_repo_name")
    token = configurable.get("chat_github_token")
    head_sha = configurable.get("chat_head_sha")
    return (
        owner if isinstance(owner, str) else "",
        repo if isinstance(repo, str) else "",
        token if isinstance(token, str) and token else None,
        head_sha if isinstance(head_sha, str) and head_sha else None,
    )


async def read_repo_file(path: str, ref: str | None = None) -> dict[str, Any]:
    """Read a file (or list a directory) from the PR's repository at a git ref.

    Use this to inspect code beyond the diff — callers, definitions, neighboring
    modules, config — at the exact commit under review. The diff itself is
    already available as the virtual file ``/pr/diff.patch``.

    Args:
        path: Repo-relative path, e.g. ``src/app/main.py`` or ``src/app`` for a
            directory listing. Leading slashes are ignored.
        ref: Git ref (branch, tag, or SHA). Defaults to the PR head commit.

    Returns:
        For a file: ``{success, path, ref, content, truncated}``.
        For a directory: ``{success, path, ref, entries}`` where each entry is
        ``{name, type, path}``.
        On failure: ``{success: False, error}``.
    """
    owner, repo, token, head_sha = _chat_repo_context()
    if not owner or not repo:
        return {"success": False, "error": "repository context unavailable"}

    clean_path = path.strip().lstrip("/")
    resolved_ref = (ref or head_sha or "").strip()
    params = {"ref": resolved_ref} if resolved_ref else None
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{clean_path}"
    headers = github_headers(token or "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"GitHub request failed: {exc!s}"}

    if response.status_code == 404:
        return {"success": False, "error": f"not found: {clean_path} @ {resolved_ref or 'default'}"}
    if response.status_code >= 400:
        return {"success": False, "error": f"GitHub returned {response.status_code}"}

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
        return {"success": False, "error": f"unsupported content type for {clean_path}"}

    encoded = payload.get("content")
    if not isinstance(encoded, str):
        return {"success": False, "error": "file content unavailable (too large for contents API)"}
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
