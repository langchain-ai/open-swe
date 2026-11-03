"""Vibe check: yes/no rules TypeSafe's System One model answers about each changed file of a PR.

A port of sven (github.com/semistrict/sven, MIT): every rule is a question whose
"yes" means the change breaks it, asked once per changed file about that file's
diff alone. A rule's probability of "yes" at or above its error threshold fails
the check, and one only reaching its warn threshold is reported. It runs on the
pull requests of authors who turned the experiment on, and whenever someone asks
for one, and posts a single conversation comment it updates in place.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final, Literal

import httpx2
from langchain_typesafe import Noul, NoulCriteria, TypeSafeClassifier

from openswe.database import postgres
from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestEvent
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.users.models import User
from openswe.utils.http_pool import pooled_transport
from openswe.utils.jev import SystemOneGateway

logger = logging.getLogger(__name__)

JOB_KIND: Final = "vibe_check"
_MARKER: Final = "<!-- open-swe-vibe-check -->"
_CHUNK_CHARS: Final = 12_000
_CONCURRENCY: Final = 8
_TIMEOUT_SECONDS: Final = 30.0
_MAX_FILE_PAGES: Final = 30
_EXCLUDED: Final = (
    "**/*.lock",
    "**/go.sum",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/vendor/**",
    "**/node_modules/**",
    "**/*.map",
    "**/*.min.js",
    "**/__snapshots__/**",
    "**/*.snap",
)

type Level = Literal["error", "warn"]


@dataclass(frozen=True)
class VibeRule:
    """One question, worded in ``prompts/vibe-check/<id>.md``: the question, what yes means, what no means."""

    id: str
    error: float | None = 0.7
    warn: float | None = None

    @classmethod
    def warning(cls, rule_id: str) -> VibeRule:
        """A rule that only ever warns."""
        return cls(rule_id, error=None, warn=0.5)

    def _wording(self) -> list[str]:
        return [" ".join(part.split()) for part in prompt(f"vibe-check/{self.id}").split("\n\n")]

    def question(self) -> Noul:
        instructions, violation, ok = self._wording()
        return Noul(instructions=instructions, criteria=NoulCriteria(true=violation, false=ok))

    @property
    def violation(self) -> str:
        return self._wording()[1]

    def level(self, probability: float) -> Level | None:
        if self.error is not None and probability >= self.error:
            return "error"
        if self.warn is not None and probability >= self.warn:
            return "warn"
        return None


RULES: Final = (
    VibeRule("debug-leftovers"),
    VibeRule("commented-out-code"),
    VibeRule("hardcoded-secret"),
    VibeRule("swallowed-error"),
    VibeRule("unfinished"),
    VibeRule("narrating-comments"),
    VibeRule("silent-fallback"),
    VibeRule("test-special-casing"),
    VibeRule.warning("weakened-tests"),
    VibeRule.warning("low-value-test"),
    VibeRule.warning("type-escape"),
    VibeRule.warning("lint-suppression"),
    VibeRule.warning("emoji"),
    VibeRule.warning("timing-patch"),
    VibeRule.warning("blanket-retry"),
    VibeRule.warning("compat-shim"),
    VibeRule.warning("reinvented-helper"),
)


@dataclass(frozen=True)
class VibeVerdict:
    path: str
    rule: VibeRule
    probability: float
    level: Level


@dataclass(frozen=True)
class ChangedFile:
    """One changed file's diff, split at hunk boundaries into pieces the model reads whole."""

    path: str
    patch: str

    @classmethod
    def of(cls, entry: dict[str, object]) -> ChangedFile | None:
        path, patch = entry.get("filename"), entry.get("patch")
        if not isinstance(path, str) or not isinstance(patch, str) or not patch:
            return None
        if any(PurePosixPath(path).full_match(pattern) for pattern in _EXCLUDED):
            return None
        return cls(path, patch)

    def chunks(self) -> list[str]:
        chunks: list[str] = []
        for hunk in self.patch.replace("\n@@ ", "\n\0@@ ").split("\0"):
            if chunks and len(chunks[-1]) + len(hunk) <= _CHUNK_CHARS:
                chunks[-1] += hunk
            else:
                chunks.append(hunk[:_CHUNK_CHARS])
        return chunks


@dataclass
class VibeCheck:
    """One vibe check of a pull request at one head commit."""

    pull: PullRequestClient
    head_sha: str
    verdicts: list[VibeVerdict] = field(default_factory=list)
    files_checked: int = 0
    input_tokens: int = 0
    comment_url: str | None = None

    @classmethod
    async def on_pull_request_event(cls, payload: dict[str, object]) -> None:
        """Queue a vibe check of a pushed or opened pull request whose author turned it on."""
        event = PullRequestEvent.parse(payload)
        if event is None or event.identity is None or not postgres.configured():
            return
        if event.pull_request.state == "closed":
            return
        pull = event.pull_request
        if not pull.head_sha or not pull.author:
            return
        if not (await User.preferences_for_login(pull.author)).experimental_vibe_check:
            return
        owner, repo, number = event.identity
        await cls.schedule(owner, repo, number)

    @staticmethod
    async def schedule(owner: str, repo: str, number: int) -> None:
        await queue.schedule(
            JOB_KIND,
            {"owner": owner, "repo": repo, "number": number},
            key=f"{JOB_KIND}:{owner}/{repo}#{number}",
        )

    @classmethod
    async def run_for(cls, owner: str, repo: str, number: int) -> VibeCheck:
        """Check the pull request's current head and post or update its vibe check comment."""
        gateway = SystemOneGateway.configured()
        if gateway is None:
            raise RuntimeError("No LangSmith gateway key is configured for System One")
        async with PullRequestClient.as_app(owner, repo, number) as pull:
            check = cls(pull, await pull.head_sha())
            await check.run(gateway)
            await check.publish()
        logger.info("Vibe check ran", extra=check.log_extra)
        return check

    @property
    def log_extra(self) -> dict[str, object]:
        return {
            "pr_repo_full_name": self.pull.repo.full_name,
            "pr_number": self.pull.number,
            "head_sha": self.head_sha,
            "files_checked": self.files_checked,
            "errors": len(self.errors),
            "warnings": len(self.verdicts) - len(self.errors),
            "input_tokens": self.input_tokens,
        }

    @property
    def errors(self) -> list[VibeVerdict]:
        return [verdict for verdict in self.verdicts if verdict.level == "error"]

    async def run(self, gateway: SystemOneGateway) -> None:
        entries = await self.pull.files(max_pages=_MAX_FILE_PAGES)
        files = [changed for entry in entries if (changed := ChangedFile.of(entry))]
        self.files_checked = len(files)
        questions = {rule.id: rule.question() for rule in RULES}
        limit = asyncio.Semaphore(_CONCURRENCY)
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=_TIMEOUT_SECONDS
        ) as client:
            classifier = gateway.classifier(client)
            answers = await asyncio.gather(
                *(
                    self._ask(classifier, limit, changed, chunk, questions)
                    for changed in files
                    for chunk in changed.chunks()
                )
            )
        worst: dict[tuple[str, str], float] = {}
        for path, probabilities in answers:
            for rule_id, probability in probabilities.items():
                key = (path, rule_id)
                worst[key] = max(worst.get(key, 0.0), probability)
        for rule in RULES:
            for changed in files:
                probability = worst.get((changed.path, rule.id), 0.0)
                if (level := rule.level(probability)) is not None:
                    self.verdicts.append(VibeVerdict(changed.path, rule, probability, level))
        self.verdicts.sort(key=lambda v: (v.level != "error", v.path, -v.probability))

    async def _ask(
        self,
        classifier: TypeSafeClassifier,
        limit: asyncio.Semaphore,
        changed: ChangedFile,
        chunk: str,
        questions: dict[str, Noul],
    ) -> tuple[str, dict[str, float]]:
        async with limit:
            response = await classifier.ainvoke(
                {"state": {"path": changed.path, "diff": chunk}, "questions": dict(questions)},
                config={"tags": ["nostream"]},
            )
        self.input_tokens += response.usage.input_tokens or 0
        return changed.path, {rule_id: a.noul for rule_id, a in response.nouls.items()}

    def report(self) -> str:
        sha = self.head_sha[:7]
        if not self.verdicts:
            verdict = f"**Vibe check passed** at `{sha}`: {self.files_checked} files checked."
        elif self.errors:
            verdict = (
                f"**Vibe check failed** at `{sha}`: {len(self.errors)} errors and "
                f"{len(self.verdicts) - len(self.errors)} warnings in "
                f"{self.files_checked} files checked."
            )
        else:
            verdict = (
                f"**Vibe check passed with warnings** at `{sha}`: "
                f"{len(self.verdicts)} warnings in {self.files_checked} files checked."
            )
        rows = [
            f"| {'❌' if v.level == 'error' else '⚠️'} | `{v.path}` | `{v.rule.id}` | "
            f"{v.probability:.0%} | {v.rule.violation} |"
            for v in self.verdicts
        ]
        table = (
            ["", "| | File | Rule | Confidence | Why |", "| --- | --- | --- | --- | --- |", *rows]
            if rows
            else []
        )
        return "\n".join(
            [
                _MARKER,
                verdict,
                *table,
                "",
                "<sub>Each changed file is judged alone from its own diff by TypeSafe's "
                "System One model. Experimental: enable or disable under Settings → "
                "Experiments.</sub>",
            ]
        )

    async def publish(self) -> None:
        body = self.report()
        existing = next(
            (
                c
                for c in await self.pull.issue_comments()
                if isinstance(c.get("body"), str) and c["body"].startswith(_MARKER)
            ),
            None,
        )
        posted = (
            await self.pull.edit_comment(existing["id"], body)
            if existing is not None and isinstance(existing.get("id"), int)
            else await self.pull.comment(body)
        )
        url = posted.get("html_url") if isinstance(posted, dict) else None
        self.comment_url = url if isinstance(url, str) else None


async def run_vibe_check(job: Job) -> None:
    """Job handler: vibe check one pull request's current head."""
    owner, repo, number = (job.payload.get(k) for k in ("owner", "repo", "number"))
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    try:
        await VibeCheck.run_for(owner, repo, number)
    except GitHubAppUnavailable as exc:
        if exc.retryable:
            raise
        logger.warning(
            "Vibe check skipped without a GitHub App installation",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
