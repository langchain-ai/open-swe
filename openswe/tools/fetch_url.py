from typing import Any

import httpx2
from markdownify import markdownify

from openswe.utils.url_safety import UnsafeUrlError, request_with_safe_redirects

FETCH_URL_MAX_CHARS = 100_000


async def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
    """Implement the `fetch_url` tool."""
    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await request_with_safe_redirects(
                client,
                "GET",
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DeepAgents/1.0)"},
            )
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
    except UnsafeUrlError as exc:
        return {"error": str(exc), "status_code": 0, "url": exc.url}
    except httpx2.HTTPError as e:
        return {"error": f"Fetch URL error: {e!s}", "url": url}
