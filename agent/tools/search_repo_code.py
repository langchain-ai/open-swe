"""Tool: ``search_repo_code``. Search the PR's repository via the GitHub code-search API."""

from __future__ import annotations

from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_checks import github_headers

_GITHUB_API = "https://api.github.com"


def _chat_repo_context() -> tuple[str, str, str | None]:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        configurable = {}
    owner = configurable.get("chat_repo_owner")
    repo = configurable.get("chat_repo_name")
    token = configurable.get("chat_github_token")
    return (
        owner if isinstance(owner, str) else "",
        repo if isinstance(repo, str) else "",
        token if isinstance(token, str) and token else None,
    )


async def search_repo_code(query: str, max_results: int = 20) -> dict[str, Any]:
    """Search code in the PR's repository for a keyword, symbol, or phrase.

    Backed by GitHub code search, which indexes the repository's default branch
    (not arbitrary refs). Use it to locate where a symbol is defined or used,
    then ``read_repo_file`` for the surrounding context. For matches within the
    changed lines, search the virtual file ``/pr/diff.patch`` instead.

    Args:
        query: Search terms. Repo scoping is added automatically.
        max_results: Max matches to return (capped at 50).

    Returns:
        ``{success, total_count, results}`` where each result is
        ``{path, fragments}``; ``{success: False, error}`` on failure.
    """
    owner, repo, token = _chat_repo_context()
    if not owner or not repo:
        return {"success": False, "error": "repository context unavailable"}

    capped = max(1, min(max_results, 50))
    headers = github_headers(token or "")
    headers["Accept"] = "application/vnd.github.text-match+json"
    params = {"q": f"{query} repo:{owner}/{repo}", "per_page": capped}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{_GITHUB_API}/search/code", headers=headers, params=params
            )
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"GitHub request failed: {exc!s}"}

    if response.status_code == 422:
        return {"success": False, "error": "query rejected by GitHub code search"}
    if response.status_code >= 400:
        return {"success": False, "error": f"GitHub returned {response.status_code}"}

    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    results: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        fragments = [
            match.get("fragment")
            for match in item.get("text_matches", [])
            if isinstance(match, dict) and isinstance(match.get("fragment"), str)
        ]
        results.append({"path": item.get("path", ""), "fragments": fragments})
    total = payload.get("total_count") if isinstance(payload, dict) else None
    return {
        "success": True,
        "total_count": total if isinstance(total, int) else len(results),
        "results": results,
    }
