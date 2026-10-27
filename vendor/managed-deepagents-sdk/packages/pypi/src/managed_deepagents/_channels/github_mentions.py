"""The handles an event's text mentions: GitHub sends no event for a mention."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from managed_deepagents._channels.github_schema import GitHubPayload, SupportedEventKey

# A login (letters, digits, hyphens, at most 39 characters) or an
# organization's team, as GitHub links them. Not part of an email address,
# a longer word or a path, and without trailing punctuation.
_MENTION = re.compile(
    r"(?<![A-Za-z0-9_`@/.-])@("
    r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9_-](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9_-])?)?"
    r")(?![A-Za-z0-9_-])"
)
# A fence opens or closes a code block on a line of its own.
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_QUOTE = re.compile(r" {0,3}>")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_BACKTICKS = re.compile(r"`+")
# Where an HTML block may start: the beginning of a line, after up to 3 spaces.
_LINE_START = re.compile(r"(?:^|\n) {0,3}$")
_MAX_MENTIONS = 50


class _Text(TypedDict, total=False):
    title: str | None
    body: str | None


def _issue(payload: Mapping[str, Any]) -> _Text:
    return {"title": payload["issue"]["title"], "body": payload["issue"]["body"]}


def _pull_request(payload: Mapping[str, Any]) -> _Text:
    return {"title": payload["pull_request"]["title"], "body": payload["pull_request"]["body"]}


def _comment(payload: Mapping[str, Any]) -> _Text:
    return {"body": payload["comment"]["body"]}


def _review(payload: Mapping[str, Any]) -> _Text:
    return {"body": payload["review"]["body"]}


# The text each event writes. Other actions, such as a push to a pull request
# or a label, write none, so they mention no one.
_TEXTS: dict[SupportedEventKey, Callable[[Mapping[str, Any]], _Text]] = {
    "issues.opened": _issue,
    "issues.edited": _issue,
    "pull_request.opened": _pull_request,
    "pull_request.edited": _pull_request,
    "issue_comment.created": _comment,
    "issue_comment.edited": _comment,
    "pull_request_review.submitted": _review,
    "pull_request_review.edited": _review,
    "pull_request_review_comment.created": _comment,
    "pull_request_review_comment.edited": _comment,
}


def event_mentions(key: SupportedEventKey, payload: GitHubPayload) -> tuple[str, ...]:
    """The handles, such as ``@octocat``, the text the event wrote mentions.

    Lowercased, each once, in order. As on GitHub, code, quotes and HTML
    comments mention no one. An edit lists only the handles it added.
    """
    texts = _TEXTS.get(key)
    if texts is None:
        return ()
    record = cast("Mapping[str, Any]", payload)
    text = texts(record)
    mentions = _handles(text)
    if not key.endswith(".edited"):
        return mentions
    changes = record.get("changes")
    changed = cast("Mapping[str, Any]", changes) if isinstance(changes, Mapping) else {}
    if not ({"title", "body"} & set(changed)):
        return ()
    before: _Text = {**text}
    for field in ("title", "body"):
        previous = changed.get(field)
        if isinstance(previous, Mapping) and field in text:
            before[field] = cast("Mapping[str, Any]", previous).get("from")
    known = set(_handles(before))
    return tuple(handle for handle in mentions if handle not in known)


def _handles(text: _Text) -> tuple[str, ...]:
    parts = (text.get("title"), text.get("body"))
    prose = _prose("\n\n".join(part for part in parts if isinstance(part, str)))
    mentions: list[str] = []
    for match in _MENTION.finditer(prose):
        handle = f"@{match.group(1).lower()}"
        if handle not in mentions:
            mentions.append(handle)
            if len(mentions) == _MAX_MENTIONS:
                break
    return tuple(mentions)


def _prose(text: str) -> str:
    """The text GitHub renders as prose: without code, quotes and HTML comments.

    Fences and quotes are read by line, as Markdown does; a fence that is
    never closed runs to the end, as on GitHub.
    """
    lines: list[str] = []
    fence = ""
    quoted = False
    for line in text.split("\n"):
        marker = _FENCE.match(line)
        rest = line[marker.end() :] if marker else ""
        if fence:
            if (
                marker
                and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
                and not rest.strip()
            ):
                fence = ""
            lines.append("")
        # A backtick fence's info string can't hold a backtick.
        elif marker and not (marker.group(1)[0] == "`" and "`" in rest):
            fence = marker.group(1)
            quoted = False
            lines.append("")
        elif _QUOTE.match(line):
            quoted = True
            lines.append("")
        elif not line.strip():
            quoted = False
            lines.append(line)
        else:
            # A line right after a quote continues it.
            lines.append("" if quoted else line)
    paragraphs = _PARAGRAPH_BREAK.split("\n".join(lines))
    return _without_comments("\n\n".join(_without_spans(paragraph) for paragraph in paragraphs))


def _without_spans(paragraph: str) -> str:
    """A paragraph without its code spans.

    A span opens with a run of backticks and closes with the next run of the
    same length; a run that nothing closes is just backticks.
    """
    runs = [match.span() for match in _BACKTICKS.finditer(paragraph)]
    closers: dict[int, deque[int]] = {}
    for index, (start, end) in enumerate(runs):
        closers.setdefault(end - start, deque()).append(index)
    kept: list[str] = []
    cursor = index = 0
    while index < len(runs):
        start, end = runs[index]
        waiting = closers[end - start]
        while waiting and waiting[0] <= index:
            waiting.popleft()
        if not waiting:
            index += 1
            continue
        closer = waiting.popleft()
        kept.append(paragraph[cursor:start] + " ")
        cursor = runs[closer][1]
        index = closer + 1
    kept.append(paragraph[cursor:])
    return "".join(kept)


def _without_comments(text: str) -> str:
    """The text without HTML comments.

    One never closed runs to the end when it starts a line, as an HTML block
    does; elsewhere it is just text.
    """
    kept: list[str] = []
    cursor = 0
    while (start := text.find("<!--", cursor)) != -1:
        end = text.find("-->", start + 4)
        if end == -1 and not _LINE_START.search(text, 0, start):
            break
        kept.append(text[cursor:start] + " ")
        cursor = len(text) if end == -1 else end + 3
    kept.append(text[cursor:])
    return "".join(kept)
