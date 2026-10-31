"""Review links at the Slack delivery boundary."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import cast

from openswe.utils.dashboard_links import dashboard_base_url, dashboard_review_url

_PRESERVE_GITHUB_LINKS: ContextVar[bool] = ContextVar("preserve_github_links", default=False)


@contextmanager
def preserve_github_links(enabled: bool) -> Iterator[None]:
    token = _PRESERVE_GITHUB_LINKS.set(enabled)
    try:
        yield
    finally:
        _PRESERVE_GITHUB_LINKS.reset(token)


_PR_URL = r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+/pull/[1-9]\d*/?"
_PR_PARTS = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", re.IGNORECASE)
_PR_LINK = re.compile(
    rf"\[(?P<md_label>[^\]\n]*)\]\((?P<md>{_PR_URL})\)"
    rf"|<(?P<slack>{_PR_URL})(?:\|(?P<slack_label>[^<>]*))?>"
    rf"|(?P<bare>{_PR_URL})(?=$|[\s<>|)\].,!;:'\"])",
    re.IGNORECASE,
)
_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")
_GITHUB_MARK = "↗"


@dataclass(frozen=True)
class _PrLinks:
    """How one Slack text field shows PR links: the review page, then a ↗ link to GitHub."""

    mrkdwn: bool
    github_mark: bool = True

    def text(self, text: str) -> str:
        parts: list[str] = []
        start = 0
        for code in _CODE.finditer(text):
            parts.append(_PR_LINK.sub(self.link, text[start : code.start()]))
            parts.append(code.group())
            start = code.end()
        return "".join([*parts, _PR_LINK.sub(self.link, text[start:])])

    def link(self, match: re.Match[str]) -> str:
        github = match["md"] or match["slack"] or match["bare"]
        owner, repo, number = cast(re.Match[str], _PR_PARTS.search(github)).groups()
        review = dashboard_review_url(owner, repo, int(number))
        if not review:
            return match.group()
        if not self.github_mark:
            return match.group().replace(github, review)
        if match["md"]:
            return f"[{match['md_label']}]({review})[{_GITHUB_MARK}]({github})"
        label = match["slack_label"] or f"{repo}#{number}"
        if match["slack"] or self.mrkdwn:
            return f"<{review}|{label}><{github}|{_GITHUB_MARK}>"
        return f"[{label}]({review})[{_GITHUB_MARK}]({github})"

    def block(self, value: object) -> object:
        if isinstance(value, list):
            return [self.block(item) for item in value]
        if not isinstance(value, dict) or value.get("type") in {
            "plain_text",
            "rich_text_preformatted",
        }:
            return value
        field = _PrLinks(mrkdwn=value.get("type") != "markdown")
        return {
            key: (
                field.text(item)
                if key == "text"
                else _PrLinks(mrkdwn=True, github_mark=False).text(item)
            )
            if key in {"text", "url"} and isinstance(item, str)
            else self.block(item)
            for key, item in value.items()
        }


async def pr_review_links(
    text: str,
    blocks: list[dict[str, object]] | None,
) -> tuple[str, list[dict[str, object]] | None]:
    """Show displayed PR links as the review page plus a GitHub link, keeping button values."""
    if (
        _PRESERVE_GITHUB_LINKS.get()
        or not dashboard_base_url()
        or "github.com/" not in (text + str(blocks)).lower()
    ):
        return text, blocks
    fallback = _PrLinks(mrkdwn=True, github_mark=not blocks)
    return fallback.text(text), cast(
        list[dict[str, object]] | None, _PrLinks(mrkdwn=True).block(blocks)
    )
