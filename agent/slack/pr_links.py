"""Feature-gated review links at the Slack delivery boundary."""

import re
from typing import cast

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.utils.dashboard_links import dashboard_base_url, dashboard_review_url
from agent.workspaces.routing import workspace_for_slack_channel

_PR_LINK = re.compile(
    r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/pull/([1-9]\d*)/?"
    r"(?=$|[\s<>|)\].,!;:'\"])",
    re.IGNORECASE,
)
_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")


def _text(text: str) -> str:
    parts: list[str] = []
    start = 0
    for code in _CODE.finditer(text):
        parts.append(_links(text[start : code.start()]))
        parts.append(code.group())
        start = code.end()
    return "".join([*parts, _links(text[start:])])


def _links(text: str) -> str:
    return _PR_LINK.sub(
        lambda match: dashboard_review_url(match[1], match[2], int(match[3])) or match.group(),
        text,
    )


def _block(value: object) -> object:
    if isinstance(value, list):
        return [_block(item) for item in value]
    if not isinstance(value, dict) or value.get("type") in {"plain_text", "rich_text_preformatted"}:
        return value
    return {
        key: _text(item) if key in {"text", "url"} and isinstance(item, str) else _block(item)
        for key, item in value.items()
    }


async def pr_review_links(
    text: str,
    blocks: list[dict[str, object]] | None,
    *,
    channel_id: str | None = None,
) -> tuple[str, list[dict[str, object]] | None]:
    """Rewrite displayed PR links without changing stored URLs or button values."""
    if not dashboard_base_url() or "github.com/" not in (text + str(blocks)).lower():
        return text, blocks
    workspace = await workspace_for_slack_channel(channel_id) if channel_id else None
    if not (await get_workspace_settings(workspace)).get("pr_review_links", False):
        return text, blocks
    return _text(text), cast(list[dict[str, object]] | None, _block(blocks))
