"""Search what people have written in Slack and to Open SWE."""

from typing import Literal

from openswe.human_text.search import MAX_LIMIT, Reader, search
from openswe.run_config import RunConfig
from openswe.tools.access import resolve_access
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
async def search_human_text(
    query: str,
    limit: int = 20,
    source: Literal["slack", "open_swe"] | None = None,
    author: str | None = None,
) -> dict[str, object]:
    """Implement the `search_human_text` tool."""
    if not query.strip():
        return {"success": False, "error": "Search query must not be empty."}
    if not 1 <= limit <= MAX_LIMIT:
        return {"success": False, "error": f"Use a limit from 1 to {MAX_LIMIT}."}
    cfg = RunConfig.from_runtime()
    access = await resolve_access(cfg)
    # Everyone who can read this thread reads the results, so only a private
    # thread's owner sees their own private messages and private threads.
    reader = Reader()
    if cfg.github_login and access.private and access.owner:
        reader = await Reader.person(cfg.github_login, cfg.user_email)
    hits = await search(query, reader, limit=limit, source=source, author=author)
    return {
        "success": True,
        "scope": "public" if reader.login is None else "personal",
        "results": [hit.model_dump(mode="json") for hit in hits],
    }
