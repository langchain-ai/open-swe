"""A fetched web page as the markdown ``fetch_url`` answers with.

Both sides run ``fetch_url``: the backend's tool (``openswe/tools/fetch_url.py``) and the
copy MDA runs itself (``mda/rt/local_tools.py``), so they convert a page the same way.
"""

import asyncio
import logging
from html.parser import HTMLParser
from typing import Final

from markdownify import markdownify

logger = logging.getLogger(__name__)


class PageText(HTMLParser):
    """The text of an HTML page, for when markdownify cannot convert it.

    markdownify recurses once per nested element, so a deeply nested page exhausts the
    recursion limit; this parser walks the page iteratively instead. It drops what
    raw-text elements hold, so a page's scripts and styles never read as its content.
    """

    _SKIPPED: Final = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    @classmethod
    async def markdown(cls, html: str) -> str:
        """``html`` as markdown, converted off the event loop: a large page takes a while."""
        return await asyncio.to_thread(cls._convert, html)

    @classmethod
    def _convert(cls, html: str) -> str:
        # Sync because it runs in a worker thread.
        try:
            return markdownify(html)
        except RecursionError:
            logger.warning("Page too deeply nested for markdown; falling back to its text")
        page = cls()
        page.feed(html)
        page.close()
        return "\n\n".join(page._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002 - HTMLParser's signature
        if tag in self._SKIPPED:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if text := " ".join(data.split()):
            self._parts.append(text)
