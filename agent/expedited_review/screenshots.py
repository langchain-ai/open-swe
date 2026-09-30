"""Screenshot evidence attached to a pull request's description."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from agent.slack.blocks import ImageBlock


class _Images(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            values = dict(attrs)
            self.images.append((values.get("alt") or "Screenshot", values.get("src") or ""))


def screenshots(body: str) -> list[ImageBlock]:
    """Collect up to four HTTPS images without fetching author-controlled URLs."""
    parser = _Images()
    parser.feed(body)
    candidates = re.findall(r"!\[([^\]]*)\]\((https://[^\s)]+)\)", body)
    blocks: list[ImageBlock] = []
    seen: set[str] = set()
    for label, url in [*candidates, *parser.images]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        if url in seen or len(url) > 3000:
            continue
        seen.add(url)
        blocks.append({"type": "image", "image_url": url, "alt_text": label[:2000] or "Screenshot"})
        if len(blocks) == 4:
            break
    return blocks
