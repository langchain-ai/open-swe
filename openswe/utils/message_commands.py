"""Quote-aware matching for inline message directives."""

import re

_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)|\"[^\"\n]*\"|(?<!\w)'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|^\s*(?:>|&gt;).*$",
    re.MULTILINE,
)


def unquoted_text(text: str) -> str:
    """Mask quoted text without changing match offsets."""
    return _QUOTED.sub(lambda match: " " * len(match[0]), text)


def parse_mention_command(
    text: str, command: str, bot_user_id: str, bot_username: str = ""
) -> tuple[str, str] | None:
    """Parse a bare directive or one immediately following an unquoted bot mention."""
    command_text = unquoted_text(text)
    pattern = re.compile(rf"{re.escape(command)}(?:\s+(.*))?", re.DOTALL | re.IGNORECASE)
    mentions = [f"<@{bot_user_id}>"] if bot_user_id else []
    if bot_username:
        mentions.append(f"@{bot_username}")
    if mentions:
        mention_pattern = re.compile("|".join(re.escape(mention) for mention in mentions))
        for mention in mention_pattern.finditer(command_text):
            if not command_text[mention.end() :].lstrip().lower().startswith(command.lower()):
                continue
            match = pattern.fullmatch(text[mention.end() :].strip())
            if match is not None:
                return (match[1] or "").strip(), text[: mention.start()].strip()
    if command_text.lstrip().lower().startswith(command.lower()):
        match = pattern.fullmatch(text.strip())
        if match is not None:
            return (match[1] or "").strip(), ""
    return None


def find_message_command(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Find an unquoted directive while preserving its original arguments."""
    match = pattern.search(unquoted_text(text))
    return pattern.match(text, match.start()) if match else None


def remove_message_command(text: str, match: re.Match[str]) -> str:
    before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
    return f"{before} {after}".strip() if before and after else f"{before}{after}".strip()


PERFORMANCE_COMMAND = re.compile(r"/model:perf(?![\w:-])")
