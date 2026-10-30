"""Everything a person types into a Slack message for Open SWE, parsed in one place.

Routing decides whether a message is addressed to Open SWE first; a command never
makes it so. An action (`/btw`, `/breakout`, `/breakout:web`) is one of the first
unquoted words after this bot's mention, or of the message, and the rest of the
message is its argument:

    @Open SWE /breakout #eng investigate this
    @Open SWE /btw how does thread routing work?

Options (`/model:perf…`, e.g. `/model:performance`, and `/workspace:<name>`) count
anywhere outside quotes, so `/model:perf` in backticks or quotation marks is just
text:

    @Open SWE /model:perf /workspace:infra fix the flaky test
    @Open SWE fix the flaky test /model:perf

The legacy `workspace:<name>` and `env:<name>` tags, without the slash, are still
accepted anywhere in the message.
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from openswe.workspaces.store import slugify


class SlackAction(StrEnum):
    BY_THE_WAY = "/btw"
    BREAKOUT = "/breakout"
    BREAKOUT_WEB = "/breakout:web"


PERFORMANCE_MODEL = "/model:perf"

_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)|\"[^\"\n]*\"|(?<!\w)'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|^\s*(?:>|&gt;).*$",
    re.MULTILINE,
)
_WORD = re.compile(r"\s*(\S+)")
_TOKEN = re.compile(r"\S+")
_WORKSPACE_OPTION = re.compile(r"/workspace:([A-Za-z0-9][A-Za-z0-9._-]*)", re.IGNORECASE)
_LEGACY_WORKSPACE_TAG = re.compile(
    r"(?:(?<=\s)|^)(?:env|workspace):([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|$)", re.IGNORECASE
)

type Span = tuple[int, int]


@dataclass(frozen=True)
class ParsedSlackMessage:
    text: str
    action: SlackAction | None = None
    performance_model: bool = False
    workspace: str | None = None
    prior_text: str = ""
    """What the sender wrote before mentioning Open SWE, when a command follows the mention."""
    argument: str = ""
    argument_start: int = 0
    performance_span: Span | None = None
    workspace_span: Span | None = None

    @classmethod
    def parse(cls, text: str, bot_user_id: str, bot_username: str = "") -> Self:
        masked = cls._unquoted(text)
        parsed = next(
            (
                found
                for mention_start, anchor in cls._anchors(masked, bot_user_id, bot_username)
                if (found := cls._leading_commands(text, masked, mention_start, anchor))
            ),
            cls(text=text, argument=text.strip()),
        )
        parsed = cls._with_options(parsed, masked)
        return parsed if parsed.workspace else cls._with_legacy_workspace_tag(parsed, masked)

    @classmethod
    def _unquoted(cls, text: str) -> str:
        """Mask quoted text without changing offsets."""
        return _QUOTED.sub(lambda match: " " * len(match[0]), text)

    @classmethod
    def _anchors(cls, masked: str, bot_user_id: str, bot_username: str) -> list[Span]:
        """``(mention start, where commands may begin)``: the message start, then each mention."""
        mentions = [f"<@{bot_user_id}>"] if bot_user_id else []
        if bot_username:
            mentions.append(f"@{bot_username}")
        anchors = [(0, 0)]
        if mentions:
            pattern = re.compile(
                "|".join(re.escape(mention) for mention in mentions), re.IGNORECASE
            )
            anchors += [(found.start(), found.end()) for found in pattern.finditer(masked)]
        return anchors

    @classmethod
    def _workspace_slug(cls, name: str) -> str | None:
        try:
            return slugify(name)
        except ValueError:
            return None

    @classmethod
    def _is_performance_option(cls, word: str) -> bool:
        return word.lower().startswith(PERFORMANCE_MODEL)

    @classmethod
    def _workspace_option(cls, word: str) -> str | None:
        option = _WORKSPACE_OPTION.fullmatch(word)
        return cls._workspace_slug(option[1]) if option else None

    @classmethod
    def _leading_commands(
        cls, text: str, masked: str, mention_start: int, anchor: int
    ) -> Self | None:
        action: SlackAction | None = None
        position = anchor
        while word := _WORD.match(masked, position):
            token = word[1].lower()
            if action is None and token in SlackAction:
                action = SlackAction(token)
            elif not (cls._is_performance_option(token) or cls._workspace_option(word[1])):
                break
            position = word.end()
        if position == anchor:
            return None
        return cls(
            text=text,
            action=action,
            prior_text=text[:mention_start].strip(),
            argument=text[position:].strip(),
            argument_start=position,
        )

    @classmethod
    def _with_options(cls, parsed: Self, masked: str) -> Self:
        performance_span: Span | None = None
        workspace: str | None = None
        workspace_span: Span | None = None
        for word in _TOKEN.finditer(masked):
            if performance_span is None and cls._is_performance_option(word[0]):
                performance_span = word.span()
            elif workspace is None and (workspace := cls._workspace_option(word[0])):
                workspace_span = word.span()
        return replace(
            parsed,
            performance_model=performance_span is not None,
            performance_span=performance_span,
            workspace=workspace,
            workspace_span=workspace_span,
        )

    @classmethod
    def _with_legacy_workspace_tag(cls, parsed: Self, masked: str) -> Self:
        tag = _LEGACY_WORKSPACE_TAG.search(masked)
        workspace = cls._workspace_slug(tag[1]) if tag else None
        if tag is None or workspace is None:
            return parsed
        return replace(parsed, workspace=workspace, workspace_span=tag.span())

    @property
    def options(self) -> str:
        """Options typed outside the argument, normalized, to carry into a thread this message starts."""
        options = (
            (self.performance_span, PERFORMANCE_MODEL),
            (self.workspace_span, f"/workspace:{self.workspace}"),
        )
        return " ".join(
            option for span, option in options if span and span[1] <= self.argument_start
        )

    def without(self, *, performance_model: bool = False, workspace: bool = False) -> str:
        """The message text with the chosen options removed."""
        spans = [
            span
            for span, drop in (
                (self.performance_span, performance_model),
                (self.workspace_span, workspace),
            )
            if span is not None and drop
        ]
        text = self.text
        for start, end in sorted(spans, reverse=True):
            before, after = text[:start].rstrip(), text[end:].lstrip()
            text = f"{before} {after}" if before and after else f"{before}{after}"
        return text.strip()


_CHANNEL_MENTION_RE = re.compile(r"<#(?P<channel_id>[CG][A-Z0-9]+)(?:\|[^>]*)?>")
_TITLE_MAX_CHARS = 160


@dataclass(frozen=True)
class BreakoutCommand:
    instruction: str
    channel: str = ""
    channel_id: str = ""
    prior_text: str = ""
    options: str = ""
    workspace: str | None = None

    @classmethod
    def from_message(cls, message: ParsedSlackMessage) -> BreakoutCommand:
        command = cls(
            instruction=message.argument,
            prior_text=message.prior_text,
            options=message.options,
            workspace=message.workspace,
        )
        parts = message.argument.split(maxsplit=1)
        if parts and parts[0].lower() == "in" and len(parts) > 1:
            destination = parts[1].split(maxsplit=1)
            if destination[0].startswith(("#", "<#")):
                parts = destination
        if (
            message.action is not SlackAction.BREAKOUT
            or not parts
            or not parts[0].startswith(("#", "<#"))
        ):
            return command
        channel = _CHANNEL_MENTION_RE.fullmatch(parts[0])
        return replace(
            command,
            instruction=parts[1] if len(parts) > 1 else "",
            channel=parts[0],
            channel_id=channel["channel_id"] if channel else "",
        )

    @property
    def thread_text(self) -> str:
        """The new thread's opening message, keeping options typed alongside the command."""
        return f"{self.options} {self.instruction}".strip()

    def parsed_message(self, original: ParsedSlackMessage) -> ParsedSlackMessage:
        parsed = ParsedSlackMessage.parse(self.thread_text, "")
        return replace(
            parsed,
            action=None,
            performance_model=original.performance_model and parsed.performance_model,
            performance_span=parsed.performance_span if original.performance_model else None,
            workspace=parsed.workspace if original.workspace else None,
            workspace_span=parsed.workspace_span if original.workspace else None,
        )

    @classmethod
    def heading(cls, title: str) -> str:
        first_line = title.splitlines()[0].strip() if title.strip() else ""
        if len(first_line) > _TITLE_MAX_CHARS:
            first_line = f"{first_line[: _TITLE_MAX_CHARS - 1].rstrip()}…"
        return f"`/breakout`: {first_line}"
