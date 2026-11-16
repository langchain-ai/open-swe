"""Approval criteria from a repository's ``.robin`` directory and its approval mode.

Criteria come from one rule per Markdown file in ``.robin/auto-approve-rules/`` and the legacy
``.open-swe/auto-approve-rules/``; rules in both apply.
A rule's frontmatter may scope it with ``match-all-files`` (every changed file matches
one of the globs) or ``match-any-files`` (some changed file does). A legacy,
undocumented ``.open-swe/APPROVALS.md`` still applies to every pull request.
The mode on the repository's review style record decides whether a positive
assessment only comments (``dry_run``) or submits a GitHub approval (``approve``).
Reviews read the criteria from the default branch, so one policy covers pull requests
into every branch, including release branches cut before the policy existed, and a
pull request cannot rewrite the policy it is judged by. A rule that cannot be read
or parsed fails the whole policy closed, since it may be the one requiring human review.
"""

import asyncio
import logging
from collections.abc import Sequence

from pydantic import BaseModel, Field

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.http import GitHubClient, RepoClient, RepoFileUnreadableError
from openswe.github.repo_files import SETTINGS_MAX_CHARS, SETTINGS_PATH, RepoSettings
from openswe.github.repo_names import RepoFullName
from openswe.github.rule_directory import RULE_MAX_CHARS, RuleDirectory, ScopedRule
from openswe.prompts import prompt
from openswe.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    effective_approval_mode,
)

logger = logging.getLogger(__name__)

APPROVALS_PATH = ".open-swe/APPROVALS.md"
AUTO_APPROVE_RULES = "auto-approve-rules"
# Every place auto-approve rules may live; the first is where new rules go.
AUTO_APPROVE_RULES_DIRS = RuleDirectory.paths(AUTO_APPROVE_RULES)
AUTO_APPROVE_RULES_DIR = AUTO_APPROVE_RULES_DIRS[0]
APPROVALS_MAX_CHARS = RULE_MAX_CHARS
_CHANGED_FILE_PAGES = 30
_APPROVAL_LOGIC_PATHS = (
    "openswe/review/",
    "openswe/expedited_review/",
    "openswe/github/rule_directory.py",
    "openswe/tools/publish_review.py",
    "openswe/reviewer.py",
    "openswe/resources/prompts/reviewer/",
)


class ApprovalPolicy(BaseModel):
    """A repository's approval criteria, and why any of them could not be read."""

    approvals_md: str | None = None
    rules: list[ScopedRule] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.approvals_md or self.rules or self.errors)

    @property
    def scoped(self) -> bool:
        return any(rule.scoped for rule in self.rules)

    @classmethod
    async def fetch(cls, repo: RepoClient, ref: str | None = None) -> ApprovalPolicy:
        """The criteria at ``ref`` (the default branch when ``None``)."""
        errors: list[str] = []
        approvals_md, directory = await asyncio.gather(
            cls._approvals_md(repo, ref, errors),
            RuleDirectory.fetch(repo, AUTO_APPROVE_RULES, ref),
        )
        return cls(
            approvals_md=approvals_md,
            rules=directory.rules,
            errors=[*errors, *directory.errors],
        )

    @classmethod
    async def _approvals_md(
        cls, repo: RepoClient, ref: str | None, errors: list[str]
    ) -> str | None:
        try:
            return (
                await repo.read_file(
                    APPROVALS_PATH, ref, max_chars=APPROVALS_MAX_CHARS, strict=True
                )
                or None
            )
        except RepoFileUnreadableError as exc:
            errors.append(str(exc))
            return None

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
        """The criteria as the model reads them; ``None`` when there are none or any is unusable."""
        if self.errors or not (self.approvals_md or self.rules):
            return None
        return prompt(
            "reviewer/approval-policy",
            approvals_md=self.approvals_md or "",
            rules=[(rule.path, rule.body) for rule in self.rules],
        )

    async def for_pull(self, repo: RepoClient, number: int) -> ApprovalPolicy:
        """The criteria that apply to pull request ``number``, reading its files only if needed."""
        if not self.scoped:
            return self
        return self.for_changes(
            await ChangedFile.touched_paths(
                repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES
            )
        )

    @staticmethod
    def changes_rules(path: str) -> bool:
        """Whether ``path`` is in a directory auto-approve rules may be read from."""
        return any(path.startswith(f"{directory}/") for directory in AUTO_APPROVE_RULES_DIRS)

    @classmethod
    def changes_approval(cls, path: str) -> bool:
        """Whether ``path`` is auto-approval policy or Robin's approval logic, which never self-approves."""
        if cls.changes_rules(path) or path == APPROVALS_PATH:
            return True
        return path.removeprefix("open-swe/").startswith(_APPROVAL_LOGIC_PATHS)

    @classmethod
    async def changed_by(cls, repo: RepoClient, number: int) -> bool:
        """Whether pull request ``number`` changes auto approval; an unknown file list counts as yes."""
        changed = await ChangedFile.touched_paths(
            repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES
        )
        return changed is None or any(cls.changes_approval(path) for path in changed)

    @classmethod
    async def config_errors(cls, repo: RepoClient, number: int, head_sha: str) -> list[str]:
        """Why the Open SWE configuration pull request ``number`` changes is invalid at its head."""
        changed = await ChangedFile.touched_paths(
            repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES
        )
        if changed is None:
            return []
        errors: list[str] = []
        if any(cls.changes_rules(path) for path in changed):
            errors.extend((await cls.fetch(repo, head_sha)).errors)
        if SETTINGS_PATH in changed:
            try:
                content = await repo.read_file(
                    SETTINGS_PATH, head_sha, max_chars=SETTINGS_MAX_CHARS, strict=True
                )
            except RepoFileUnreadableError as exc:
                errors.append(str(exc))
            else:
                if content is not None:
                    errors.extend(RepoSettings.problems(content))
        return errors


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
) -> ApprovalPolicy | None:
    """The criteria a review of pull request ``pr_number`` assesses against; ``None`` when off."""
    if await approval_mode_for(owner, repo) == "off":
        return None
    async with GitHubClient.connect(token=token) as github:
        client = github.repo(owner, repo)
        policy = await ApprovalPolicy.fetch(client)
        return await policy.for_pull(client, pr_number)


async def open_swe_config_errors(
    owner: str, repo: str, pr_number: int, head_sha: str, *, token: str | None
) -> list[str]:
    """``ApprovalPolicy.config_errors`` for callers that hold a raw token, or none."""
    async with GitHubClient.connect(token=token) as github:
        return await ApprovalPolicy.config_errors(github.repo(owner, repo), pr_number, head_sha)
