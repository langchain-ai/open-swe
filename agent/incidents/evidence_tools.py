"""Bounded, redacted evidence collected from the main agent and incident context."""

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.incidents.models import Evidence
from agent.store import now_iso

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


def source_url(value: str | None) -> str:
    """Keep only HTTPS source locations, without credentials or arbitrary queries."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))[:2000]
    except ValueError:
        return ""


class EvidenceCollector:
    def __init__(self) -> None:
        self.evidence: list[Evidence] = []
        self.gaps: list[str] = []
        self.checked: list[str] = []
        self._remaining_chars = 30_000

    def record_observation(
        self, *, source: str, url: str, summary: str, query: str, content: Any
    ) -> dict[str, Any]:
        observation = json.dumps(content, ensure_ascii=False)
        key = hashlib.sha256(f"{source}:{url}:{query}:{observation}".encode()).hexdigest()[:16]
        evidence = Evidence(
            id=f"{source}:{key}",
            source=source,
            url=url,
            summary=redact(summary, 1500),
            query=redact(query, 2000),
            retrieved_at=now_iso(),
        )
        if all(item.id != evidence.id for item in self.evidence):
            self.evidence.append(evidence)
        serialized = redact(observation, min(4000, self._remaining_chars))
        if len(observation) > len(serialized):
            gap = "Evidence content was bounded or redacted; source links retain the full context."
            if gap not in self.gaps:
                self.gaps.append(gap)
        self._remaining_chars -= len(serialized)
        return {"evidence_id": evidence.id, "source_url": url, "observation": serialized}
