"""Quote-aware matching for commands typed into Slack messages.

Commands never decide whether a message is addressed to Open SWE: routing does
that first, and only an addressed message is parsed for one.
"""

import re
from dataclasses import dataclass

_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)|\"[^\"\n]*\"|(?<!\w)'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|^\s*(?:>|&gt;).*$",
    re.MULTILINE,
)


def unquoted_text(text: str) -> str:
    """Mask quoted text without changing match offsets."""
    return _QUOTED.sub(lambda match: " " * len(match[0]), text)


def _without_span(text: str, start: int, end: int) -> str:
    before, after = text[:start].rstrip(), text[end:].lstrip()
    return f"{before} {after}".strip() if before and after else f"{before}{after}".strip()


def find_message_command(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Find an unquoted inline tag while preserving its original arguments."""
    match = pattern.search(unquoted_text(text))
    return pattern.match(text, match.start()) if match else None


def remove_message_command(text: str, match: re.Match[str]) -> str:
    return _without_span(text, match.start(), match.end())


@dataclass(frozen=True)
class CommandMatch:
    text: str
    start: int
    end: int
    mention_start: int

    @property
    def argument(self) -> str:
        return self.text[self.end :].strip()

    @property
    def prior_text(self) -> str:
        """What the sender wrote before mentioning Open SWE."""
        return self.text[: self.mention_start].strip()

    @property
    def without_command(self) -> str:
        return _without_span(self.text, self.start, self.end)


@dataclass(frozen=True)
class MessageCommand:
    """A command typed into a message, e.g. `@Open SWE /btw why?`.

    It counts only as the first unquoted word after a mention of this bot, or as
    the first word of the message.
    """

    name: str

    def parse(self, text: str, bot_user_id: str, bot_username: str = "") -> CommandMatch | None:
        masked = unquoted_text(text)
        command = re.compile(rf"\s*({re.escape(self.name)})(?=\s|$)", re.IGNORECASE)
        anchors = [(0, 0)]
        mentions = [f"<@{bot_user_id}>"] if bot_user_id else []
        if bot_username:
            mentions.append(f"@{bot_username}")
        if mentions:
            mention = re.compile("|".join(re.escape(token) for token in mentions))
            anchors += [(found.start(), found.end()) for found in mention.finditer(masked)]
        for mention_start, anchor in anchors:
            if match := command.match(masked, anchor):
                return CommandMatch(text, match.start(1), match.end(1), mention_start)
        return None


BY_THE_WAY = MessageCommand("/btw")
BREAKOUT = MessageCommand("/breakout")
BREAKOUT_WEB = MessageCommand("/breakout:web")
PERFORMANCE_MODEL = MessageCommand("/model:perf")
