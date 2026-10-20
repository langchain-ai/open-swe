"""A minimal async Notion REST client authenticated with the integration token.

Reads retry on 429 using ``Retry-After``; writes are never retried, because a
retried mutation that already landed would post a duplicate comment.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

import httpx2
from pydantic import JsonValue, ValidationError

from openswe.notion.models import (
    BlockList,
    CommentList,
    NotionBlock,
    NotionComment,
    NotionDataSource,
    NotionPage,
    NotionUser,
    PageList,
    PageMarkdown,
)
from openswe.notion.settings import notion_settings
from openswe.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
_MAX_READ_ATTEMPTS = 3
_MAX_RETRY_AFTER_SECONDS = 10.0
_RICH_TEXT_CHUNK = 2000
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")

type JsonObject = dict[str, JsonValue]


class NotionNotConfiguredError(RuntimeError):
    """Raised when ``NOTION_API_KEY`` is not set."""


class NotionAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"Notion API {status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code


# Everything a Notion call can fail with that callers log instead of propagating.
NOTION_ERRORS: tuple[type[Exception], ...] = (
    NotionAPIError,
    NotionNotConfiguredError,
    httpx2.HTTPError,
    ValidationError,
)


def _text_items(text: str, link: str | None) -> list[JsonValue]:
    items: list[JsonValue] = []
    for start in range(0, len(text), _RICH_TEXT_CHUNK):
        content: JsonObject = {"content": text[start : start + _RICH_TEXT_CHUNK]}
        if link:
            content["link"] = {"url": link}
        items.append({"type": "text", "text": content})
    return items


def markdown_to_rich_text(markdown: str) -> list[JsonValue]:
    """Plain rich text, keeping ``[label](url)`` links clickable."""
    items: list[JsonValue] = []
    cursor = 0
    for match in _MARKDOWN_LINK_RE.finditer(markdown):
        if match.start() > cursor:
            items.extend(_text_items(markdown[cursor : match.start()], None))
        items.extend(_text_items(match.group(1), match.group(2)))
        cursor = match.end()
    if cursor < len(markdown):
        items.extend(_text_items(markdown[cursor:], None))
    return items


class NotionClient:
    def __init__(
        self, token: str, http: httpx2.AsyncClient | None = None, *, as_user: bool = False
    ) -> None:
        self._token = token
        # A personal access token acts as its owner; say so on comments too.
        self._as_user = as_user
        self._http = http or httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT)
        self._owns_http = http is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(resp: httpx2.Response) -> None:
        if resp.status_code < 400:  # noqa: PLR2004
            return
        code, message = "unknown", resp.text[:300]
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            code = str(body.get("code") or code)
            message = str(body.get("message") or message)
        raise NotionAPIError(resp.status_code, code, message)

    async def _read(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: JsonObject | None = None,
    ) -> JsonValue:
        for attempt in range(1, _MAX_READ_ATTEMPTS + 1):
            resp = await self._http.request(
                method, f"{NOTION_API}{path}", headers=self._headers(), params=params, json=json
            )
            if resp.status_code == 429 and attempt < _MAX_READ_ATTEMPTS:  # noqa: PLR2004
                try:
                    delay = float(resp.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1.0
                logger.info(
                    "Notion API rate limited; retrying read",
                    extra={"notion_path": path, "attempt": attempt, "retry_after_s": delay},
                )
                await asyncio.sleep(min(delay, _MAX_RETRY_AFTER_SECONDS))
                continue
            self._raise_for_status(resp)
            return resp.json()
        raise AssertionError("unreachable")

    async def _write(self, method: str, path: str, json: JsonObject) -> JsonValue:
        resp = await self._http.request(
            method, f"{NOTION_API}{path}", headers=self._headers(), json=json
        )
        self._raise_for_status(resp)
        return resp.json()

    async def get_page(self, page_id: str) -> NotionPage:
        return NotionPage.model_validate(await self._read("GET", f"/pages/{page_id}"))

    async def get_page_markdown(self, page_id: str) -> PageMarkdown:
        return PageMarkdown.model_validate(await self._read("GET", f"/pages/{page_id}/markdown"))

    async def create_page(
        self,
        data_source_id: str,
        properties: JsonObject,
        *,
        markdown: str,
        icon_emoji: str = "",
    ) -> NotionPage:
        """Create a page in the data source, its content given as Notion-flavored markdown."""
        body: JsonObject = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
            "markdown": markdown,
        }
        if icon_emoji:
            body["icon"] = {"type": "emoji", "emoji": icon_emoji}
        return NotionPage.model_validate(await self._write("POST", "/pages", body))

    async def get_data_source(self, data_source_id: str) -> NotionDataSource:
        return NotionDataSource.model_validate(
            await self._read("GET", f"/data_sources/{data_source_id}")
        )

    async def get_user(self, user_id: str) -> NotionUser:
        return NotionUser.model_validate(await self._read("GET", f"/users/{user_id}"))

    async def get_bot_user(self) -> NotionUser:
        return NotionUser.model_validate(await self._read("GET", "/users/me"))

    async def query_data_source(
        self, data_source_id: str, filter_: JsonObject, *, limit: int = 100
    ) -> list[NotionPage]:
        pages: list[NotionPage] = []
        cursor: str | None = None
        while len(pages) < limit:
            body: JsonObject = {"filter": filter_, "page_size": min(100, limit - len(pages))}
            if cursor:
                body["start_cursor"] = cursor
            result = PageList.model_validate(
                await self._read("POST", f"/data_sources/{data_source_id}/query", json=body)
            )
            pages.extend(result.results)
            if not result.has_more or not result.next_cursor:
                break
            cursor = result.next_cursor
        return pages

    async def iter_block_children(self, block_id: str) -> AsyncIterator[NotionBlock]:
        cursor: str | None = None
        while True:
            params = {"page_size": "100"}
            if cursor:
                params["start_cursor"] = cursor
            result = BlockList.model_validate(
                await self._read("GET", f"/blocks/{block_id}/children", params=params)
            )
            for block in result.results:
                yield block
            if not result.has_more or not result.next_cursor:
                return
            cursor = result.next_cursor

    async def list_comments(self, block_id: str, *, limit: int = 200) -> list[NotionComment]:
        comments: list[NotionComment] = []
        cursor: str | None = None
        while len(comments) < limit:
            params = {"block_id": block_id, "page_size": "100"}
            if cursor:
                params["start_cursor"] = cursor
            result = CommentList.model_validate(await self._read("GET", "/comments", params=params))
            comments.extend(result.results)
            if not result.has_more or not result.next_cursor:
                break
            cursor = result.next_cursor
        return comments

    async def update_page_properties(self, page_id: str, properties: JsonObject) -> NotionPage:
        return NotionPage.model_validate(
            await self._write("PATCH", f"/pages/{page_id}", {"properties": properties})
        )

    async def get_comment(self, comment_id: str) -> NotionComment:
        return NotionComment.model_validate(await self._read("GET", f"/comments/{comment_id}"))

    async def create_comment(
        self, page_id: str, markdown: str, *, discussion_id: str | None = None
    ) -> NotionComment:
        """Comment on the page, or reply in ``discussion_id`` when given."""
        body: JsonObject = {"rich_text": markdown_to_rich_text(markdown)}
        if discussion_id:
            body["discussion_id"] = discussion_id
        else:
            body["parent"] = {"page_id": page_id}
        if self._as_user:
            body["display_name"] = {"type": "user"}
        return NotionComment.model_validate(await self._write("POST", "/comments", body))


def notion_client() -> NotionClient:
    """Reads, as the integration that receives the webhooks."""
    token = notion_settings().api_key
    if not token:
        raise NotionNotConfiguredError("NOTION_API_KEY is not configured")
    return NotionClient(token)


def notion_writer() -> NotionClient:
    """Writes, as the agent's Notion member when its personal access token is set."""
    settings = notion_settings()
    if settings.agent_api_key:
        return NotionClient(settings.agent_api_key, as_user=True)
    if not settings.api_key:
        raise NotionNotConfiguredError("NOTION_API_KEY is not configured")
    return NotionClient(settings.api_key)
