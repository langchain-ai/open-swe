"""Bounded, redacted evidence collected from the main agent and incident context."""

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openswe.incidents.models import Evidence
from openswe.utils.redaction import redact
from openswe.utils.time import now_iso


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
