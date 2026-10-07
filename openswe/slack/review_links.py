"""Feature-gated review links at the Slack delivery boundary."""

import logging
import re
from typing import cast

from openswe.run_config import RunConfig
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_base_url, dashboard_review_url

logger = logging.getLogger(__name__)

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
    login: str | None = None,
) -> tuple[str, list[dict[str, object]] | None]:
    """Rewrite displayed PR links without changing stored URLs or button values."""
    if not dashboard_base_url() or "github.com/" not in (text + str(blocks)).lower():
        return text, blocks
    if login is None:
        try:
            login = RunConfig.from_runtime().github_login
        except RuntimeError:
            logger.debug("No active run for Slack PR links", exc_info=True)
    if not login or not (await User.preferences_for_login(login)).pr_review_links:
        return text, blocks
    return _text(text), cast(list[dict[str, object]] | None, _block(blocks))
