"""Rendering a Notion page body into the Markdown the agent reads."""

import pytest
from pydantic import JsonValue

from openswe.notion import blocks
from openswe.notion.blocks import render_page_body
from tests.notion.fakes import FakeNotion, paragraph

PAGE = "page-1"


def _block(block_id: str, kind: str, payload: dict[str, JsonValue], *, children: bool = False):
    return {
        "object": "block",
        "id": block_id,
        "type": kind,
        "has_children": children,
        kind: payload,
    }


def _text(value: str) -> dict[str, JsonValue]:
    return {"rich_text": [{"type": "text", "plain_text": value}]}


async def test_body_renders_structure_links_code_and_images() -> None:
    notion = FakeNotion()
    notion.blocks[PAGE] = [
        _block("h", "heading_2", _text("Goal")),
        _block("l1", "bulleted_list_item", _text("Show version"), children=True),
        _block("n1", "numbered_list_item", _text("First")),
        _block("n2", "numbered_list_item", _text("Second")),
        _block("t", "to_do", {**_text("Ship it"), "checked": True}),
        _block("c", "code", {**_text("print(1)"), "language": "python"}),
        _block(
            "p",
            "paragraph",
            {"rich_text": [{"plain_text": "spec", "href": "https://example.com/spec"}]},
        ),
        _block("i", "image", {"type": "file", "file": {"url": "https://files.example/x.png"}}),
    ]
    notion.blocks["l1"] = [_block("l1a", "bulleted_list_item", _text("In the footer"))]

    body = await render_page_body(notion.client(), PAGE)

    assert body.markdown.splitlines() == [
        "## Goal",
        "- Show version",
        "  - In the footer",
        "1. First",
        "2. Second",
        "- [x] Ship it",
        "```python",
        "print(1)",
        "```",
        "[spec](https://example.com/spec)",
        "![image](https://files.example/x.png)",
    ]
    assert body.image_urls == ["https://files.example/x.png"]
    assert not body.truncated


async def test_body_stops_at_the_block_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blocks, "MAX_BLOCKS", 2)
    notion = FakeNotion()
    notion.blocks[PAGE] = [paragraph(f"line {n}", block_id=f"b{n}") for n in range(5)]

    body = await render_page_body(notion.client(), PAGE)

    assert body.truncated
    assert "line 1" in body.markdown
    assert "line 2" not in body.markdown


async def test_body_stops_at_the_depth_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blocks, "MAX_DEPTH", 1)
    notion = FakeNotion()
    notion.blocks[PAGE] = [paragraph("top", block_id="top", has_children=True)]
    notion.blocks["top"] = [paragraph("nested", block_id="nested")]

    body = await render_page_body(notion.client(), PAGE)

    assert body.truncated
    assert "nested" not in body.markdown
