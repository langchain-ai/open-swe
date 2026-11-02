"""Approval criteria from a repository's ``.open-swe`` directory and its approval mode.

Criteria come from one rule per Markdown file in ``.open-swe/auto-approve-rules/``.
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
import re
from collections.abc import Sequence
from fnmatch import fnmatchcase

import httpx2
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.http import GitHubClient, RepoClient, RepoFileUnreadableError
from openswe.github.repo_files import SETTINGS_MAX_CHARS, SETTINGS_PATH, RepoSettings
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


class InvalidRuleError(ValueError):
    """An auto-approve rule file that cannot be applied as written."""


class _RuleFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    match_all_files: list[str] = Field(default_factory=list, alias="match-all-files")
    match_any_files: list[str] = Field(default_factory=list, alias="match-any-files")


class AutoApproveRule(BaseModel):
    """One file in ``.open-swe/auto-approve-rules/``: criteria and the changes they cover."""

    path: str
    match_all_files: list[str] = Field(default_factory=list)
    match_any_files: list[str] = Field(default_factory=list)
    criteria: str

    @classmethod
    def parse(cls, path: str, text: str | None) -> AutoApproveRule:
        """The rule ``text`` declares; raises ``InvalidRuleError`` saying why it cannot apply."""
        if text is None:
            raise InvalidRuleError(f"`{path}`: GitHub returned no text (binary or too large)")
        frontmatter: object = None
        body = text
        if match := _FRONTMATTER.match(text):
            body = text[match.end() :]
            try:
                frontmatter = yaml.safe_load(match.group(1))
            except yaml.YAMLError as exc:
                raise InvalidRuleError(f"`{path}`: frontmatter is not valid YAML: {exc}") from exc
        body = body.strip()
        if not body:
            raise InvalidRuleError(f"`{path}`: the rule has no body")
        if len(body) > APPROVALS_MAX_CHARS:
            raise InvalidRuleError(f"`{path}`: the rule exceeds {APPROVALS_MAX_CHARS} characters")
        try:
            scope = _RuleFrontmatter.model_validate(frontmatter or {})
        except ValidationError as exc:
            raise InvalidRuleError(
                f"`{path}`: invalid frontmatter: {RepoSettings.describe(exc)}"
            ) from exc
        return cls(
            path=path,
            match_all_files=scope.match_all_files,
            match_any_files=scope.match_any_files,
            criteria=body,
        )

    @property
    def scoped(self) -> bool:
        return bool(self.match_all_files or self.match_any_files)

    def applies_to(self, changed: Sequence[str] | None) -> bool:
        """Whether the changed paths satisfy every scope the rule declares.

        ``None`` means the changes are unknown: ``match-all-files`` cannot hold, while
        ``match-any-files`` is assumed to, so a scoped restriction is never skipped.
        """
        if self.match_all_files and (
            changed is None or not all(self.matches(path, self.match_all_files) for path in changed)
        ):
            return False
        return (
            not self.match_any_files
            or changed is None
            or any(self.matches(path, self.match_any_files) for path in changed)
        )

    @staticmethod
    def matches(path: str, globs: Sequence[str]) -> bool:
        return any(fnmatchcase(path, glob) for glob in globs)


class ApprovalPolicy(BaseModel):
    """A repository's approval criteria, and why any of them could not be read."""

    approvals_md: str | None = None
    rules: list[AutoApproveRule] = Field(default_factory=list)
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
        approvals_md, rule_files = await asyncio.gather(
            cls._approvals_md(repo, ref, errors), cls._rule_files(repo, ref, errors)
        )
        rules: list[AutoApproveRule] = []
        for path, text in sorted(rule_files.items()):
            if not path.endswith(".md"):
                continue
            try:
                rules.append(AutoApproveRule.parse(path, text))
            except InvalidRuleError as exc:
                errors.append(str(exc))
        if errors:
            logger.warning(
                "Auto-approve rules are unusable",
                extra={"repository": repo.full_name, "ref": ref or "", "problems": errors},
            )
        return cls(approvals_md=approvals_md, rules=rules, errors=errors)

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
    async def _rule_files(
        cls, repo: RepoClient, ref: str | None, errors: list[str]
    ) -> dict[str, str | None]:
        try:
            return await repo.read_dir(AUTO_APPROVE_RULES_DIR, ref)
        except (httpx2.HTTPError, ValueError) as exc:
            logger.warning(
                "Could not read auto-approve rules",
                extra={"repository": repo.full_name},
                exc_info=True,
            )
            errors.append(f"Could not read `{AUTO_APPROVE_RULES_DIR}/`: {exc}")
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
        """The criteria as the model reads them; ``None`` when there are none or any is unusable."""
        if self.errors or not (self.approvals_md or self.rules):
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
        return self.for_changes(
            await ChangedFile.touched_paths(
                repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES
            )
        )

    @classmethod
    async def config_errors(cls, repo: RepoClient, number: int, head_sha: str) -> list[str]:
        """Why the Open SWE configuration pull request ``number`` changes is invalid at its head."""
        changed = await ChangedFile.touched_paths(
            repo.pull_request(number), max_pages=_CHANGED_FILE_PAGES
        )
        if changed is None:
            return []
        errors: list[str] = []
        if any(path.startswith(f"{AUTO_APPROVE_RULES_DIR}/") for path in changed):
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
