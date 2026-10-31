"""Approval criteria from a repository's ``.open-swe`` directory and its approval mode.

Criteria come from one rule per Markdown file in ``.open-swe/auto-approve-rules/``.
A rule's frontmatter may list ``paths`` globs; the rule then applies only when every
file the pull request changes matches one of them. A legacy, undocumented
``.open-swe/APPROVALS.md`` still applies to every pull request.
The mode on the repository's review style record decides whether a positive
assessment only comments (``dry_run``) or submits a GitHub approval (``approve``).
Reviews read the criteria from the default branch, so one policy covers pull requests
into every branch, including release branches cut before the policy existed, and a
pull request cannot rewrite the policy it is judged by.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from fnmatch import fnmatchcase

import httpx2
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.repo_names import RepoFullName
from openswe.prompts import prompt
from openswe.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    effective_approval_mode,
)

logger = logging.getLogger(__name__)

APPROVALS_PATH = ".open-swe/APPROVALS.md"
AUTO_APPROVE_RULES_DIR = ".open-swe/auto-approve-rules"
APPROVALS_MAX_CHARS = 15_000
_CHANGED_FILE_PAGES = 30
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class _RuleFrontmatter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paths: list[str] = Field(default_factory=list)


class AutoApproveRule(BaseModel):
    """One file in ``.open-swe/auto-approve-rules/``: criteria and the changes they cover."""

    path: str
    paths: list[str] = Field(default_factory=list)
    criteria: str

    @classmethod
    def parse(cls, path: str, text: str) -> AutoApproveRule | None:
        """The rule ``text`` declares, or ``None`` when it is empty, oversized, or invalid."""
        frontmatter: object = None
        body = text
        if match := _FRONTMATTER.match(text):
            body = text[match.end() :]
            try:
                frontmatter = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                logger.warning(
                    "Ignoring an auto-approve rule with invalid frontmatter",
                    extra={"rule_path": path},
                    exc_info=True,
                )
                return None
        body = body.strip()
        if not body or len(body) > APPROVALS_MAX_CHARS:
            logger.warning(
                "Ignoring an empty or oversized auto-approve rule",
                extra={"rule_path": path, "chars": len(body)},
            )
            return None
        try:
            scope = _RuleFrontmatter.model_validate(frontmatter or {})
        except ValidationError:
            logger.warning(
                "Ignoring an auto-approve rule with invalid frontmatter",
                extra={"rule_path": path},
                exc_info=True,
            )
            return None
        return cls(path=path, paths=scope.paths, criteria=body)

    def applies_to(self, changed: Sequence[str] | None) -> bool:
        """Whether every changed path matches one of ``paths``; unscoped rules always apply.

        ``None`` means the changes are unknown, so only unscoped rules apply.
        """
        if not self.paths:
            return True
        if changed is None:
            return False
        return all(any(fnmatchcase(path, glob) for glob in self.paths) for path in changed)


class ApprovalPolicy(BaseModel):
    """A repository's approval criteria: ``APPROVALS.md`` and its auto-approve rules."""

    approvals_md: str | None = None
    rules: list[AutoApproveRule] = Field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.approvals_md or self.rules)

    @property
    def scoped(self) -> bool:
        return any(rule.paths for rule in self.rules)

    @classmethod
    async def fetch(cls, repo: RepoClient) -> ApprovalPolicy:
        """The default branch's criteria; unreadable rules read as absent, which never approves."""
        approvals_md, rule_files = await asyncio.gather(
            repo.read_file(APPROVALS_PATH, None, max_chars=APPROVALS_MAX_CHARS),
            cls._rule_files(repo),
        )
        rules = [
            rule
            for path, text in sorted(rule_files.items())
            if path.endswith(".md") and (rule := AutoApproveRule.parse(path, text))
        ]
        return cls(approvals_md=approvals_md, rules=rules)

    @classmethod
    async def _rule_files(cls, repo: RepoClient) -> dict[str, str]:
        try:
            return await repo.read_dir(AUTO_APPROVE_RULES_DIR, None)
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read auto-approve rules",
                extra={"repository": repo.full_name},
                exc_info=True,
            )
            return {}

    @classmethod
    async def load(cls, owner: str, repo: str, *, token: str | None) -> ApprovalPolicy:
        """``fetch`` for callers that hold a raw token, or none."""
        async with GitHubClient.connect(token=token) as github:
            return await cls.fetch(github.repo(owner, repo))

    def for_changes(self, changed: Sequence[str] | None) -> ApprovalPolicy:
        """The criteria that apply to a pull request changing ``changed``."""
        return self.model_copy(
            update={"rules": [rule for rule in self.rules if rule.applies_to(changed)]}
        )

    def render(self) -> str | None:
        """The criteria as the model reads them, or ``None`` when there are none."""
        if not self.configured:
            return None
        return prompt(
            "reviewer/approval-policy",
            approvals_md=self.approvals_md or "",
            rules=[(rule.path, rule.criteria) for rule in self.rules],
        )

    async def for_pull(self, repo: RepoClient, number: int) -> ApprovalPolicy:
        """The criteria that apply to pull request ``number``, reading its files only if needed."""
        if not self.scoped:
            return self
        files = await ChangedFile.of_pull(repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES)
        if files is not None and len(files) >= _CHANGED_FILE_PAGES * 100:
            files = None
        changed = None if files is None else [path for file in files for path in file.paths]
        return self.for_changes(changed)


async def approval_mode_for(owner: str, repo: str) -> ApprovalMode:
    """The repository's approval mode; a failed lookup is ``dry_run``, which never approves."""
    try:
        record = await REVIEW_STYLES.get(RepoFullName(f"{owner}/{repo}"))
    except Exception:
        logger.exception("approval mode lookup failed", extra={"repository": f"{owner}/{repo}"})
        return "dry_run"
    return effective_approval_mode(record)


async def approval_policy_for_review(
    owner: str, repo: str, pr_number: int, *, token: str | None
) -> str | None:
    """The criteria a review of pull request ``pr_number`` in ``owner/repo`` assesses against."""
    if await approval_mode_for(owner, repo) == "off":
        return None
    async with GitHubClient.connect(token=token) as github:
        client = github.repo(owner, repo)
        policy = await ApprovalPolicy.fetch(client)
        return (await policy.for_pull(client, pr_number)).render()
