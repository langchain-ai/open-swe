"""A fetched web page as the markdown ``fetch_url`` answers with.

Both sides run ``fetch_url``: the backend's tool (``openswe/tools/fetch_url.py``) and the
copy MDA runs itself (``mda/rt/local_tools.py``), so they convert a page the same way.
"""

import asyncio
import logging
import re
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
    _BLOCKS: Final = frozenset(
        "address article aside blockquote body br caption dd details dialog div dl dt "
        "fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr "
        "li main menu nav ol p pre search section summary table tbody td tfoot th thead tr ul".split()
    )

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
        blocks = (" ".join(part.split()) for part in "".join(page._parts).split("\n"))
        return "\n\n".join(filter(None, blocks))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002 - HTMLParser's signature
        if tag in self._SKIPPED:
            self._skip_depth += 1
        elif not self._skip_depth and tag in self._BLOCKS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth and tag in self._BLOCKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(re.sub(r"\s+", " ", data))
