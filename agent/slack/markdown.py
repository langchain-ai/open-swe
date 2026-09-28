from html import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agent.slack.blocks import (
    CODE_TEXT_MAX_CHARS,
    MARKDOWN_TEXT_MAX_CHARS,
    MESSAGE_MAX_BLOCKS,
    SECTION_TEXT_MAX_CHARS,
    Block,
    code,
    markdown,
    section,
)

_parser = MarkdownIt("commonmark", {"html": False}).enable("strikethrough")


def markdown_blocks(text: str, *, reserve: int = 0) -> list[Block] | None:
    """``text`` as blocks that keep code highlighted past Slack's native Markdown limit.

    ``None`` when it needs more blocks than a message holds once ``reserve`` are left free.
    """
    if len(text) <= MARKDOWN_TEXT_MAX_CHARS:
        return [markdown(text)]
    lines = text.splitlines(keepends=True)
    blocks: list[Block] = []
    start = 0
    for token in _parser.parse(text):
        if token.level or token.type not in {"fence", "code_block"} or token.map is None:
            continue
        begin, end = token.map
        blocks.extend(_prose(lines[start:begin]))
        language = token.info.split()[0] if token.info.strip() else None
        blocks.extend(
            code(chunk, language=language) for chunk in _chunks(token.content, CODE_TEXT_MAX_CHARS)
        )
        start = end
    blocks.extend(_prose(lines[start:]))
    return blocks if len(blocks) <= MESSAGE_MAX_BLOCKS - reserve else None


def _prose(lines: list[str]) -> list[Block]:
    return [
        section(chunk)
        for chunk in _chunks(markdown_to_mrkdwn("".join(lines)), SECTION_TEXT_MAX_CHARS)
    ]


def _chunks(text: str, limit: int) -> list[str]:
    """``text`` cut on line boundaries into pieces of at most ``limit`` characters."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    chunks.append(current)
    return [chunk.rstrip("\n") for chunk in chunks if chunk.strip()]


def markdown_to_mrkdwn(markdown: str) -> str:
    """Convert standard Markdown to Slack mrkdwn."""
    output: list[str] = []
    list_stack: list[tuple[bool, int]] = []
    quote_depth = 0
    item_prefix = ""
    in_heading = False

    for token in _parser.parse(markdown):
        if token.type == "inline":
            prefix = "" if in_heading else "> " * quote_depth + item_prefix
            output.append(prefix + _render_inline(token.children or []))
            item_prefix = ""
        elif token.type == "heading_open":
            output.append("> " * quote_depth + "*")
            in_heading = True
        elif token.type == "heading_close":
            output[-1] += "*\n"
            in_heading = False
        elif token.type == "paragraph_close":
            output.append("\n")
        elif token.type == "bullet_list_open":
            list_stack.append((False, 1))
        elif token.type == "ordered_list_open":
            list_stack.append((True, int(token.attrGet("start") or "1")))
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            list_stack.pop()
        elif token.type == "list_item_open":
            ordered, number = list_stack[-1]
            item_prefix = f"{number}. " if ordered else "• "
            if ordered:
                list_stack[-1] = (ordered, number + 1)
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type in {"fence", "code_block"}:
            content = escape(token.content, quote=False)
            output.append(
                "> " * quote_depth
                + f"```\n{content}{'' if content.endswith(chr(10)) else chr(10)}```\n"
            )
        elif token.type == "hr":
            output.append("—\n")

    return "".join(output).rstrip()


def _render_inline(tokens: list[Token]) -> str:
    output: list[str] = []
    links: list[str] = []
    markers = {
        "strong_open": "*",
        "strong_close": "*",
        "em_open": "_",
        "em_close": "_",
        "s_open": "~",
        "s_close": "~",
    }
    for token in tokens:
        if token.type == "text":
            output.append(escape(token.content, quote=False))
        elif token.type in markers:
            output.append(markers[token.type])
        elif token.type == "code_inline":
            output.append(f"`{escape(token.content, quote=False)}`")
        elif token.type == "link_open":
            links.append(escape(str(token.attrGet("href") or ""), quote=False))
            output.append("<" + links[-1] + "|")
        elif token.type == "link_close":
            output.append(">")
            links.pop()
        elif token.type in {"softbreak", "hardbreak"}:
            output.append("\n")
        elif token.type == "image":
            output.append(
                f"<{escape(str(token.attrGet('src') or ''), quote=False)}|{escape(token.content, quote=False)}>"
            )
        elif token.type == "html_inline":
            output.append(escape(token.content, quote=False))
    return "".join(output)
