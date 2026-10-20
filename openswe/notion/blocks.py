"""Render a Notion page body to Markdown for the agent's task prompt."""

import logging
from dataclasses import dataclass, field

from openswe.notion.client import NotionClient
from openswe.notion.models import NotionBlock, RichText

logger = logging.getLogger(__name__)

MAX_DEPTH = 3
MAX_BLOCKS = 400


@dataclass
class RenderedBody:
    markdown: str
    image_urls: list[str] = field(default_factory=list)
    truncated: bool = False


def _inline(items: list[RichText]) -> str:
    parts: list[str] = []
    for item in items:
        text = item.plain_text
        link = item.href or (item.text.link.url if item.text and item.text.link else None)
        parts.append(f"[{text}]({link})" if link and text else text)
    return "".join(parts)


def render_block(block: NotionBlock, indent: str, number: int) -> tuple[str | None, str | None]:
    """The Markdown line(s) for one block, and its image URL if it is an image."""
    content = block.content()
    text = _inline(content.rich_text)
    kind = block.type
    if kind == "paragraph":
        return f"{indent}{text}", None
    if kind in ("heading_1", "heading_2", "heading_3"):
        return f"{'#' * int(kind[-1])} {text}", None
    if kind == "bulleted_list_item":
        return f"{indent}- {text}", None
    if kind == "numbered_list_item":
        return f"{indent}{number}. {text}", None
    if kind == "to_do":
        return f"{indent}- [{'x' if content.checked else ' '}] {text}", None
    if kind == "toggle":
        return f"{indent}- {text}", None
    if kind == "quote":
        return f"{indent}> {text}", None
    if kind == "callout":
        return f"{indent}> **Note:** {text}", None
    if kind == "code":
        return f"{indent}```{content.language or ''}\n{text}\n{indent}```", None
    if kind == "divider":
        return f"{indent}---", None
    if kind == "image":
        ref = content.file or content.external
        if ref is None or not ref.url:
            return None, None
        caption = _inline(content.caption) or "image"
        return f"{indent}![{caption}]({ref.url})", ref.url
    if kind in ("bookmark", "embed", "link_preview") and content.url:
        return f"{indent}<{content.url}>", None
    if kind == "child_page" and content.title:
        return f"{indent}- Subpage: {content.title}", None
    if text:
        return f"{indent}{text}", None
    return None, None


async def render_page_body(client: NotionClient, page_id: str) -> RenderedBody:
    lines: list[str] = []
    images: list[str] = []
    count = 0
    truncated = False

    async def walk(block_id: str, depth: int) -> None:
        nonlocal count, truncated
        number = 0
        async for block in client.iter_block_children(block_id):
            if count >= MAX_BLOCKS:
                truncated = True
                return
            count += 1
            number = number + 1 if block.type == "numbered_list_item" else 0
            line, image = render_block(block, "  " * depth, number)
            if line is not None:
                lines.append(line)
            if image:
                images.append(image)
            if block.has_children and block.type not in ("child_page", "child_database"):
                if depth + 1 < MAX_DEPTH:
                    await walk(block.id, depth + 1)
                else:
                    truncated = True

    await walk(page_id, 0)
    if truncated:
        logger.info("Notion page body truncated", extra={"notion_page_id": page_id})
        lines.append("\n_(Page content truncated; fetch the page for the rest.)_")
    return RenderedBody("\n".join(lines).strip(), images, truncated)
