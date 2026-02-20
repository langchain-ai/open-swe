"""Helpers for normalizing message content across model providers."""

from __future__ import annotations

from langchain_core.messages import ContentBlock


def extract_text_content(content: str | list[ContentBlock]) -> str:
    """Extract human-readable text from model message content.

    Supports:
    - Plain strings
    - OpenAI-style content blocks (list of {"type": "text", "text": ...})
    - Dict wrappers with nested "content" or "text"
    """

    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ""

    text = ""
    for item in content:
        if isinstance(item, dict) and "text" in item:
            text += item["text"]

    return text.strip()
