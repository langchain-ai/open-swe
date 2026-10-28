"""Check content a run is about to make public before it leaves the box or the backend."""

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import httpx2
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from openswe.confidentiality.scan import (
    Finding,
    Segment,
    SensitiveContext,
    SensitiveSource,
    blocking,
)
from openswe.config import ENV
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.repositories import Repository
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.client import fetch_slack_thread_messages
from openswe.utils.model import make_model

logger = logging.getLogger(__name__)

Surface = Literal["push", "github_cli", "pull_request"]
_REVIEW_FLAG_CONFIDENCE = 0.6
_REVIEW_MAX_SEGMENT_CHARS = 4_000


class GuardMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

    @classmethod
    def current(cls) -> GuardMode:
        try:
            return cls(ENV.CONFIDENTIALITY_GUARD.get().strip().lower())
        except ValueError:
            logger.warning("Unknown confidentiality guard mode; enforcing")
            return cls.ENFORCE


async def repository_is_public(full_name: str) -> bool:
    """Whether ``full_name`` is public; one whose visibility cannot be read counts as public."""
    stored = await Repository.get(full_name)
    if stored is not None and stored.private is not None:
        return not stored.private
    owner, _, name = full_name.partition("/")
    try:
        async with GitHubClient.as_app(owner, name) as github:
            info = await github.repo(owner, name).info()
    except GitHubAppUnavailable, httpx2.HTTPError, ValueError:
        logger.warning(
            "Repository visibility is unknown; treating it as public",
            extra={"repo_full_name": full_name},
            exc_info=True,
        )
        return True
    return info.get("private") is not True


async def run_sensitive_context(cfg: RunConfig, *, target: str | None) -> SensitiveContext:
    """What ``cfg``'s run saw that must stay out of the public repository ``target``."""
    sources: list[SensitiveSource] = []
    thread = cfg.slack_thread
    if thread and thread.channel_id and thread.thread_ts:
        messages = await fetch_slack_thread_messages(thread.channel_id, thread.thread_ts)
        # The agent's own replies restate its work; people's messages are the source.
        text = "\n".join(
            str(message.get("text") or "") for message in messages if not message.get("bot_id")
        )
        if text.strip():
            sources.append(SensitiveSource("slack_thread", text))
    private = [
        repo.full_name
        for repo in await Repository.all()
        if repo.private is True and repo.key != (target or "").lower()
    ]
    return SensitiveContext(sources, private_repositories=private)


class _ReviewFinding(BaseModel):
    segment: int = Field(description="Index of the leaking outgoing segment")
    category: str = Field(description="Short category, e.g. customer, internal_url, private_code")
    confidence: float = Field(ge=0, le=1)


class _Review(BaseModel):
    findings: list[_ReviewFinding] = Field(default_factory=list)


class ConfidentialityReviewer:
    """A model pass over outgoing content and the sensitive context the scan cannot judge."""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    @classmethod
    def configured(cls) -> ConfidentialityReviewer | None:
        model_id = ENV.CONFIDENTIALITY_REVIEW_MODEL.optional()
        return cls(make_model(model_id)) if model_id else None

    async def review(
        self, segments: tuple[Segment, ...], sources: tuple[SensitiveSource, ...]
    ) -> list[Finding]:
        if not segments or not sources:
            return []
        outgoing = [
            {
                "index": index,
                "location": segment.location,
                "text": segment.text[:_REVIEW_MAX_SEGMENT_CHARS],
            }
            for index, segment in enumerate(segments)
        ]
        sensitive = [{"label": source.label, "text": source.text} for source in sources]
        request = (
            f"<outgoing>\n{json.dumps(outgoing)}\n</outgoing>\n"
            f"<sensitive>\n{json.dumps(sensitive)}\n</sensitive>"
        )
        review = await self._model.with_structured_output(_Review).ainvoke(
            [SystemMessage(prompt("confidentiality-review")), HumanMessage(request)]
        )
        if not isinstance(review, _Review):
            raise TypeError("Confidentiality review returned no structured result")
        return [
            Finding(
                "context_overlap",
                f"reviewer:{item.category}",
                "flag",
                segments[item.segment].location,
            )
            for item in review.findings
            if 0 <= item.segment < len(segments) and item.confidence >= _REVIEW_FLAG_CONFIDENCE
        ]


@dataclass(frozen=True)
class Verdict:
    mode: GuardMode
    findings: list[Finding] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.mode != GuardMode.ENFORCE or not self.findings

    def refusal(self, publication: Publication) -> str:
        return json.dumps(
            {
                "status": "error",
                "error_type": "ConfidentialContentBlocked",
                "error": (
                    "This would publish content to the public repository "
                    f"{publication.repository or '(unidentified)'} that looks confidential, so "
                    "nothing was "
                    "published. Remove the flagged material (rewrite the commit, title, body or "
                    "comment) and try again. If the person you are working for confirms the "
                    "content is safe to publish, tell them a human approval is required; do not "
                    "work around this check."
                ),
                "blocking": blocking(self.findings),
                "findings": [finding.describe() for finding in self.findings],
            }
        )


@dataclass(frozen=True)
class Publication:
    """Content one run is about to make public in one repository."""

    # ``None`` when the target cannot be identified, which counts as public.
    repository: str | None
    surface: Surface
    segments: tuple[Segment, ...]

    async def check(
        self, cfg: RunConfig, *, reviewer: ConfidentialityReviewer | None = None
    ) -> Verdict:
        mode = GuardMode.current()
        if mode == GuardMode.OFF or (
            self.repository is not None and not await repository_is_public(self.repository)
        ):
            return Verdict(GuardMode.OFF)
        context = await run_sensitive_context(cfg, target=self.repository)
        findings = context.scan(self.segments)
        reviewer = reviewer or ConfidentialityReviewer.configured()
        if reviewer is not None and not blocking(findings):
            findings += await reviewer.review(self.segments, context.sources)
        logger.info(
            "Checked public publication for confidential content",
            extra={
                "confidentiality_mode": mode.value,
                "publication_surface": self.surface,
                "repo_full_name": self.repository,
                "thread_id": cfg.thread_id,
                "finding_rules": sorted({finding.rule for finding in findings}),
                "finding_count": len(findings),
            },
        )
        return Verdict(mode, findings)
