"""Directories of Markdown rules in a repository's ``.robin`` (or legacy ``.open-swe``), each scoped
to the files it covers.

New rules go in ``.robin/<name>``. The rules in ``.open-swe/<name>`` still apply alongside them,
so moving rules between the two never drops one, including a restriction.

One rule per Markdown file. A rule's optional frontmatter scopes it with
``match-all-files`` (every changed file matches one of the globs) or
``match-any-files`` (some changed file does); without either it applies to every
pull request. Globs use ``fnmatch``, where ``*`` also matches ``/``.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from fnmatch import fnmatchcase
from typing import Self

import httpx2
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.github.http import RepoClient
from openswe.github.repo_files import CONFIG_DIRS, RepoSettings

logger = logging.getLogger(__name__)

RULE_MAX_CHARS = 15_000
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class InvalidRuleError(ValueError):
    """A rule file that cannot be applied as written."""


class _RuleFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    match_all_files: list[str] = Field(default_factory=list, alias="match-all-files")
    match_any_files: list[str] = Field(default_factory=list, alias="match-any-files")


class ScopedRule(BaseModel):
    """One rule file: its body and the changes it covers."""

    path: str
    match_all_files: list[str] = Field(default_factory=list)
    match_any_files: list[str] = Field(default_factory=list)
    body: str

    @classmethod
    def parse(cls, path: str, text: str | None) -> Self:
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
        if len(body) > RULE_MAX_CHARS:
            raise InvalidRuleError(f"`{path}`: the rule exceeds {RULE_MAX_CHARS} characters")
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
            body=body,
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


class RuleDirectory(BaseModel):
    """The usable rules for one kind of rule, from every configuration directory, and why any
    others are not."""

    name: str
    rules: list[ScopedRule] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @staticmethod
    def paths(name: str) -> tuple[str, ...]:
        """Every directory rules ``name`` are read from; the first is where new rules go."""
        return tuple(f"{directory}/{name}" for directory in CONFIG_DIRS)

    @classmethod
    async def fetch(cls, repo: RepoClient, name: str, ref: str | None = None) -> Self:
        """The rules ``name`` at ``ref`` (the default branch when ``None``), from every directory
        in ``paths``. A directory that cannot be read is an error, which fails the rules closed."""
        read = await asyncio.gather(
            *(cls._read(repo, directory, ref) for directory in cls.paths(name))
        )
        return cls(
            name=name,
            rules=[rule for rules, _ in read for rule in rules],
            errors=[error for _, errors in read for error in errors],
        )

    @staticmethod
    async def _read(
        repo: RepoClient, directory: str, ref: str | None
    ) -> tuple[list[ScopedRule], list[str]]:
        try:
            files = await repo.read_dir(directory, ref)
        except (httpx2.HTTPError, ValueError) as exc:
            logger.warning(
                "Could not read a rule directory",
                extra={"repository": repo.full_name, "directory": directory},
                exc_info=True,
            )
            return [], [f"Could not read `{directory}/`: {exc}"]
        rules: list[ScopedRule] = []
        errors: list[str] = []
        for path, text in sorted(files.items()):
            if not path.endswith(".md"):
                continue
            try:
                rules.append(ScopedRule.parse(path, text))
            except InvalidRuleError as exc:
                errors.append(str(exc))
        if errors:
            logger.warning(
                "Some rules are unusable",
                extra={
                    "repository": repo.full_name,
                    "directory": directory,
                    "ref": ref or "",
                    "problems": errors,
                },
            )
        return rules, errors
