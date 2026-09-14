from typing import Any

import httpx2
from markdownify import markdownify

from agent.utils.url_safety import request_with_safe_redirects

FETCH_URL_MAX_CHARS = 100_000


async def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
    """Implement the `fetch_url` tool."""
    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response, blocked = await request_with_safe_redirects(
                client,
                "GET",
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DeepAgents/1.0)"},
            )
            if blocked:
                return {
                    "error": blocked["content"],
                    "status_code": blocked["status_code"],
                    "url": blocked["url"],
                }
            if response is None:
                return {"error": "Fetch URL error: no response received", "url": url}

            response.raise_for_status()

            # Convert HTML content to markdown
            markdown_content = markdownify(response.text)

        if len(markdown_content) > FETCH_URL_MAX_CHARS:
            markdown_content = (
                markdown_content[:FETCH_URL_MAX_CHARS] + "\n... [content truncated: "
                f"{FETCH_URL_MAX_CHARS}/{len(markdown_content)} chars]\n"
            )

        return {
            "url": str(response.url),
            "markdown_content": markdown_content,
            "status_code": response.status_code,
            "content_length": len(markdown_content),
        }
    except httpx2.HTTPError as e:
        return {"error": f"Fetch URL error: {e!s}", "url": url}
