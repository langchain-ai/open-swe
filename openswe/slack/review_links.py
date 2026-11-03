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


_GITHUB_URL = r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+/pull/[1-9]\d*/?"
_REVIEW_URL = r"https?://[^\s<>|()\[\]?#]+/agents/reviews/[\w.-]+/[\w.-]+/[1-9]\d*/?"
_PR_URL = rf"(?:{_GITHUB_URL}|{_REVIEW_URL})"
_PR_PARTS = re.compile(
    r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?", re.IGNORECASE
)
_PR_LINK = re.compile(
    rf"\[(?P<md_label>[^\]\n]*)\]\((?P<angle><)?(?P<md>{_PR_URL})(?(angle)>)\)"
    rf"|<(?P<slack>{_PR_URL})(?:\|(?P<slack_label>[^<>]*))?>"
    rf"|(?P<bare>{_PR_URL})(?=$|[\s<>|)\].,!;:'\"])",
    re.IGNORECASE,
)
_CODE = re.compile(r"```[\s\S]*?```|`[^`\n]*`")
_GITHUB_MARK = "↗"
_GITHUB_LABEL = "GitHub"
_GITHUB_LABELS = (_GITHUB_MARK, _GITHUB_LABEL, ":arrow_upper_right:")


@dataclass(frozen=True)
class _PrLinks:
    """Show the review page followed by a format-appropriate GitHub link."""

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
        url = match["md"] or match["slack"] or match["bare"]
        review_parts = re.fullmatch(
            rf"{re.escape(dashboard_base_url())}/agents/reviews/([\w.-]+)/([\w.-]+)/([1-9]\d*)/?",
            url,
            re.IGNORECASE,
        )
        github = (
            "https://github.com/{}/{}/pull/{}".format(*review_parts.groups())
            if review_parts
            else url
        )
        if github != url and match.string[match.end() :].startswith(
            (
                f"[↗]({github})",
                f" (<{github}|GitHub>)",
                f"<{github}|↗>",
                f"<{github}|:arrow_upper_right:>",
            )
        ):
            return match.group()
        label = match["md_label"] or match["slack_label"]
        if label in (_GITHUB_MARK, ":arrow_upper_right:") or (
            label == _GITHUB_LABEL and match.string[: match.start()].endswith("> (")
        ):
            return match.group()
        parts = _PR_PARTS.fullmatch(github)
        if parts is None:
            return match.group()
        owner, repo, number = parts.groups()
        review = dashboard_review_url(owner, repo, int(number))
        if not review:
            return match.group()
        if not self.github_mark:
            return match.group().replace(github, review)
        label = label if label and label != url else f"{repo}#{number}"
        if match["md"]:
            return f"[{label}]({review})[{_GITHUB_MARK}]({github})"
        if match["slack"] or self.mrkdwn:
            return f"<{review}|{label}> (<{github}|{_GITHUB_LABEL}>)"
        return f"[{label}]({review})[{_GITHUB_MARK}]({github})"

    def block(self, value: object) -> object:
        if isinstance(value, list):
            return [self.block(item) for item in value]
        if (
            not isinstance(value, dict)
            or value.get("type") in {"plain_text", "rich_text_preformatted"}
            or (value.get("type") == "link" and value.get("text") in _GITHUB_LABELS)
        ):
            return value
        nested = _PLAIN if value.get("type") == "rich_text" or not self.github_mark else self
        field = nested if nested is _PLAIN else _PrLinks(mrkdwn=value.get("type") != "markdown")
        return {
            key: (field if key == "text" else _PLAIN).text(item)
            if key in {"text", "url"} and isinstance(item, str)
            else nested.block(item)
            for key, item in value.items()
        }


_PLAIN = _PrLinks(mrkdwn=True, github_mark=False)


async def pr_review_links(
    text: str,
    blocks: list[dict[str, object]] | None,
) -> tuple[str, list[dict[str, object]] | None]:
    """Show displayed PR links as the review page plus a GitHub link, keeping button values."""
    if _PRESERVE_GITHUB_LINKS.get() or not dashboard_base_url():
        return text, blocks
    fallback = _PrLinks(mrkdwn=True, github_mark=not blocks)
    return fallback.text(text), cast(
        list[dict[str, object]] | None, _PrLinks(mrkdwn=True).block(blocks)
    )
