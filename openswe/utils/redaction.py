"""Strip common secret and identity forms from free text before it is kept or shown."""

import re

# Every repetition is bounded so a long hostile string cannot make matching
# polynomial; real tokens, e-mail addresses, and PEM blocks fit comfortably.
_SECRET = re.compile(
    r"(?i)(?:\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|lsv2_pt_)[\w-]{1,512}"
    r"|\bAKIA[A-Z0-9]{16}\b"
    r"|\b(?:bearer\s{1,16})[\w.\-/+=]{1,2048}"
    r"|\b(?:password|secret|api[_-]?key|access[_-]?token|authorization)"
    r"\s{0,16}[:=]\s{0,16}[^\s,;]{1,2048}"
    r"|[\w.+-]{1,64}@[\w.-]{1,255}\.[A-Za-z]{2,24}"
    r"|-----BEGIN [^-]{0,64}PRIVATE KEY-----[\s\S]{0,20000}?-----END [^-]{0,64}PRIVATE KEY-----)"
)


def redact(value: str, limit: int = 4000) -> str:
    """Remove common secret/identity forms before text reaches prompts or reports."""
    return _SECRET.sub("[redacted]", value)[:limit]
