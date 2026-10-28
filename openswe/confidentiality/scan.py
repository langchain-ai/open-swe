"""Find likely confidential material in content about to be published to a public repository.

Deterministic and local: credentials, links into private systems, private
repositories the installation can reach, and runs of words copied from the
run's sensitive context, such as the Slack thread that started it. Findings
name a rule and a location, never the matched text, so a report about a leak
cannot leak it again.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

FindingKind = Literal["secret", "internal_link", "private_repository", "context_overlap"]
Severity = Literal["block", "flag"]

# Words in a run that count as copied rather than coincidental.
SHINGLE_WORDS = 8
_WORD = re.compile(r"[a-z0-9_]+(?:['.-][a-z0-9_]+)*")
_REPO_REFERENCE = re.compile(
    r"(?<![\w.-])(?:https?://github\.com/)?([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})"
)


@dataclass(frozen=True)
class Rule:
    name: str
    kind: FindingKind
    severity: Severity
    pattern: re.Pattern[str]


RULES: tuple[Rule, ...] = (
    Rule("github_token", "secret", "block", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,})\b")),
    Rule("github_pat", "secret", "block", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    Rule("slack_token", "secret", "block", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    Rule("aws_access_key", "secret", "block", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("langsmith_key", "secret", "block", re.compile(r"\blsv2_(?:pt|sk)_[A-Za-z0-9_]{20,}\b")),
    Rule("anthropic_key", "secret", "block", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    Rule("openai_key", "secret", "block", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    Rule(
        "private_key",
        "secret",
        "block",
        re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ),
    Rule(
        "slack_permalink",
        "internal_link",
        "flag",
        re.compile(r"https://[a-z0-9-]+\.slack\.com/archives/[A-Z0-9]+", re.IGNORECASE),
    ),
    Rule(
        "langsmith_org_link",
        "internal_link",
        "flag",
        re.compile(r"https://(?:[a-z]+\.)?smith\.langchain\.com/o/[0-9a-f-]{36}", re.IGNORECASE),
    ),
    Rule(
        "linear_issue_link",
        "internal_link",
        "flag",
        re.compile(r"https://linear\.app/[\w-]+/issue/", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class Segment:
    """One piece of outgoing content and where it would appear, such as ``commit abc1234``."""

    location: str
    text: str


@dataclass(frozen=True)
class SensitiveSource:
    """Text the run saw that must not be published, labeled without its content."""

    label: str
    text: str


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    rule: str
    severity: Severity
    location: str

    def describe(self) -> str:
        return f"{self.location}: {self.kind} ({self.rule})"


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {
        tuple(words[index : index + SHINGLE_WORDS])
        for index in range(len(words) - SHINGLE_WORDS + 1)
    }


class SensitiveContext:
    """What one run must keep out of a public repository, and the check against it."""

    def __init__(
        self,
        sources: Sequence[SensitiveSource] = (),
        *,
        private_repositories: Iterable[str] = (),
        public_text: str = "",
    ) -> None:
        public = _shingles(public_text)
        self.sources = tuple(sources)
        self._sources = [(source.label, _shingles(source.text) - public) for source in sources]
        self._private = frozenset(repo.lower() for repo in private_repositories)

    def scan(self, segments: Iterable[Segment]) -> list[Finding]:
        findings: dict[tuple[str, str, str], Finding] = {}
        for segment in segments:
            for finding in self._segment_findings(segment):
                findings.setdefault((finding.kind, finding.rule, finding.location), finding)
        return list(findings.values())

    def _segment_findings(self, segment: Segment) -> Iterable[Finding]:
        for rule in RULES:
            if rule.pattern.search(segment.text):
                yield Finding(rule.kind, rule.name, rule.severity, segment.location)
        for match in _REPO_REFERENCE.finditer(segment.text):
            name = match.group(2).removesuffix(".git")
            if f"{match.group(1)}/{name}".lower() in self._private:
                yield Finding(
                    "private_repository", "private_repo_reference", "flag", segment.location
                )
                break
        outgoing = _shingles(segment.text)
        for label, shingles in self._sources:
            if outgoing & shingles:
                yield Finding("context_overlap", label, "flag", segment.location)


def blocking(findings: Iterable[Finding]) -> bool:
    return any(finding.severity == "block" for finding in findings)
