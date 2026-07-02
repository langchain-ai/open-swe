import re
from typing import Any

import httpx
from markdownify import markdownify

from .http_request import _request_with_safe_redirects

FETCH_URL_MAX_CHARS = 100_000

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_NOTION_SIGNIN_MARKERS = (
    "sign in to see this page",
    "notion.so/login",
    "log in to notion",
    "log in — notion",
    "log in - notion",
)

_CHALLENGE_MARKERS = (
    "cf-challenge-form",
    "verifying you are human",
    "checking your browser before accessing",
    "attention required! | cloudflare",
)

_GENERIC_AUTH_MARKERS = ("sign in", "log in", "login required", "please log in")


def _detect_auth_wall(response: Any, markdown_content: str) -> str | None:
    """Return a short reason when a 200 response looks like a login/challenge wall."""
    html_lower = (getattr(response, "text", "") or "").lower()
    md_lower = markdown_content.lower()

    for marker in _NOTION_SIGNIN_MARKERS:
        if marker in html_lower or marker in md_lower:
            return "notion_signin"

    for marker in _CHALLENGE_MARKERS:
        if marker in html_lower:
            return "cloudflare_challenge"

    title_match = _TITLE_RE.search(getattr(response, "text", "") or "")
    title = title_match.group(1).strip().lower() if title_match else ""
    head = md_lower[:500]
    body_short = len(markdown_content) < 2000
    if body_short and any(m in title or m in head for m in _GENERIC_AUTH_MARKERS):
        return "login_required"

    return None


async def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
    """Fetch content from a URL and convert HTML to markdown format.

    This tool fetches web page content and converts it to clean markdown text,
    making it easy to read and process HTML content. After receiving the markdown,
    you MUST synthesize the information into a natural, helpful response for the user.

    Args:
        url: The URL to fetch (must be a valid HTTP/HTTPS URL)
        timeout: Request timeout in seconds (default: 30)

    Returns:
        Dictionary containing:
        - success: Whether the request succeeded
        - url: The final URL after redirects
        - markdown_content: The page content converted to markdown
        - status_code: HTTP status code
        - content_length: Length of the markdown content in characters
        - accessible: False when the fetched page looks like a login/auth wall
          (Notion sign-in stub, generic "Sign in" page, Cloudflare challenge),
          True otherwise. HTTP errors surface via `error` and don't set this.
        - auth_wall_reason: Short reason string when `accessible` is False
          (e.g. `"notion_signin"`, `"login_required"`, `"cloudflare_challenge"`),
          `None` otherwise.

    IMPORTANT: After using this tool:
    1. Read through the markdown content
    2. Extract relevant information that answers the user's question
    3. Synthesize this into a clear, natural language response
    4. NEVER show the raw markdown to the user unless specifically requested
    5. If `accessible` is False, STOP. Do not clone repos or start investigation
       on this task. Report the auth wall to the user (Slack/Linear) and ask for
       a public link or pasted contents.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response, blocked = await _request_with_safe_redirects(
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

            response.raise_for_status()

            # Convert HTML content to markdown
            markdown_content = markdownify(response.text)

        auth_wall_reason = _detect_auth_wall(response, markdown_content)

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
            "accessible": auth_wall_reason is None,
            "auth_wall_reason": auth_wall_reason,
        }
    except httpx.HTTPError as e:
        return {"error": f"Fetch URL error: {e!s}", "url": url}
