"""Quote-aware matching for inline message directives."""

import re

_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)|\"[^\"\n]*\"|(?<!\w)'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|^\s*(?:>|&gt;).*$",
    re.MULTILINE,
)


def unquoted_text(text: str) -> str:
    """Mask quoted text without changing match offsets."""
    return _QUOTED.sub(lambda match: " " * len(match[0]), text)


def find_message_command(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Find an unquoted directive while preserving its original arguments."""
    match = pattern.search(unquoted_text(text))
    return pattern.match(text, match.start()) if match else None


def remove_message_command(text: str, match: re.Match[str]) -> str:
    before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
    return f"{before} {after}".strip() if before and after else f"{before}{after}".strip()


PERFORMANCE_COMMAND = re.compile(r"/model:perf(?![\w:-])")
