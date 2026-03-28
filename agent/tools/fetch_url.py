from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from markdownify import markdownify

from ..utils.url_validation import validate_url


def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
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

    IMPORTANT: After using this tool:
    1. Read through the markdown content
    2. Extract relevant information that answers the user's question
    3. Synthesize this into a clear, natural language response
    4. NEVER show the raw markdown to the user unless specifically requested
    """
    result = validate_url(url, allowed_schemes=frozenset({"http", "https"}))
    if result is None:
        return {"error": "URL blocked by SSRF protection", "url": url}

    resolved_ip, port, hostname = result

    # Connect to the resolved IP directly to prevent DNS-rebinding attacks
    parsed = urlparse(url)
    pinned_netloc = f"{resolved_ip}:{port}" if port not in (80, 443) else resolved_ip
    pinned_url = urlunparse(parsed._replace(netloc=pinned_netloc))

    try:
        response = requests.get(
            pinned_url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DeepAgents/1.0)",
                "Host": hostname,
            },
        )
        response.raise_for_status()

        # Convert HTML content to markdown
        markdown_content = markdownify(response.text)

        return {
            "url": url,
            "markdown_content": markdown_content,
            "status_code": response.status_code,
            "content_length": len(markdown_content),
        }
    except requests.exceptions.RequestException as e:
        return {"error": f"Fetch URL error: {type(e).__name__}", "url": url}
