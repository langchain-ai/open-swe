"""Everything a person types into a Slack message for Open SWE, parsed in one place.

A copy of the backend's ``openswe.slack.parsed_message`` for MDA's Slack channel,
which decides what a message starts (``rt.slack``); the backend keeps its own for
the Slack conversations it holds. Keep the two in step.

Routing decides whether a message is addressed to Open SWE first; a command never
makes it so. An action (`/btw`, `/breakout`, `/breakout:web`) is one of the first
unquoted words after this bot's mention, or of the message, and the rest of the
message is its argument:

    @Open SWE /breakout #eng investigate this
    @Open SWE /btw how does thread routing work?

Options (`/model:perf…`, e.g. `/model:performance`) count anywhere outside quotes,
so `/model:perf` in backticks or quotation marks is just text.
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self


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
#: A channel a breakout names, as Slack writes it: ``<#C123|name>``.
_CHANNEL_MENTION = re.compile(r"<#(?P<channel_id>[CG][A-Z0-9]+)(?:\|[^>]*)?>")

type Span = tuple[int, int]


@dataclass(frozen=True)
class ParsedSlackMessage:
    text: str
    action: SlackAction | None = None
    performance_model: bool = False
    prior_text: str = ""
    """What the sender wrote before mentioning Open SWE, when a command follows the mention."""
    argument: str = ""
    argument_start: int = 0
    performance_span: Span | None = None

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
        return cls._with_options(parsed, masked)

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
    def _is_performance_option(cls, word: str) -> bool:
        return word.lower().startswith(PERFORMANCE_MODEL)

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
            elif not cls._is_performance_option(token):
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
        performance_span = next(
            (
                word.span()
                for word in _TOKEN.finditer(masked)
                if cls._is_performance_option(word[0])
            ),
            None,
        )
        return replace(
            parsed,
            performance_model=performance_span is not None,
            performance_span=performance_span,
        )

    @property
    def options(self) -> str:
        """The model option typed outside the argument, normalized for a new thread."""
        return (
            PERFORMANCE_MODEL
            if self.performance_span and self.performance_span[1] <= self.argument_start
            else ""
        )

    def without(self, *, performance_model: bool = False) -> str:
        """The message text with the chosen option removed."""
        if not performance_model or self.performance_span is None:
            return self.text.strip()
        start, end = self.performance_span
        before, after = self.text[:start].rstrip(), self.text[end:].lstrip()
        return f"{before} {after}" if before and after else f"{before}{after}"


@dataclass(frozen=True)
class Breakout:
    """A ``/breakout``'s instruction and the Slack channel it names, as the backend's
    ``BreakoutCommand`` reads them."""

    instruction: str
    channel: str = ""
    channel_id: str = ""

    @classmethod
    def of(cls, message: ParsedSlackMessage) -> Breakout:
        parts = message.argument.split(maxsplit=1)
        if parts and parts[0].lower() in {"in", "to"} and len(parts) > 1:
            destination = parts[1].split(maxsplit=1)
            if destination[0].startswith(("#", "<#")):
                parts = destination
        if (
            message.action is not SlackAction.BREAKOUT
            or not parts
            or not parts[0].startswith(("#", "<#"))
        ):
            return cls(instruction=message.argument)
        channel = _CHANNEL_MENTION.fullmatch(parts[0])
        return cls(
            instruction=parts[1] if len(parts) > 1 else "",
            channel=parts[0],
            channel_id=channel["channel_id"] if channel else "",
        )
