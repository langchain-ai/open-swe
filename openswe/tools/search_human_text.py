"""Search what people have written in public Slack channels and public Open SWE threads."""

from typing import Literal

from openswe.human_text.search import MAX_LIMIT, search
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
    hits = await search(query, limit=limit, source=source, author=author)
    return {"success": True, "results": [hit.model_dump(mode="json") for hit in hits]}
