"""A repository's CODEOWNERS file: who owns each path, by GitHub's last-match-wins rule."""

import re
from dataclasses import dataclass
from typing import Self

from openswe.github.http import RepoClient
from openswe.github.org_membership import team_members

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
CODEOWNERS_MAX_CHARS = 3_000_000
# Comment markers, as langchainplus's `langdev pr hard-codeowners` reads them, for owners whose
# review is required rather than suggested.
HARD_START = "@hard-required-review-start"
HARD_END = "@hard-required-review-end"
HARD_NEXT = "@hard-required-review"


def _segment(segment: str) -> str:
    return "".join(
        "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char) for char in segment
    )


def _compile(pattern: str) -> re.Pattern[str]:
    """GitHub's gitignore dialect, without negation or character ranges."""
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    segments = body.split("/") if body else ["**"]
    regex = "" if anchored else "(?:.*/)?"
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            regex += ".*" if last else "(?:.*/)?"
            continue
        regex += _segment(segment) + ("" if last else "/")
    # ``dir/*`` owns only the files directly in ``dir``; anything else also owns what is under it.
    if segments[-1] not in {"*", "**"} or pattern.endswith("/"):
        regex += "(?:/.*)?"
    return re.compile(regex)


@dataclass(frozen=True, slots=True)
class CodeOwnersRule:
    pattern: re.Pattern[str]
    owners: tuple[str, ...]
    # Inside a ``# @hard-required-review-start``/``-end`` block, or right after ``# @hard-required-review``.
    hard: bool = False


@dataclass(frozen=True, slots=True)
class CodeOwners:
    rules: tuple[CodeOwnersRule, ...]

    @classmethod
    def parse(cls, text: str) -> Self:
        rules: list[CodeOwnersRule] = []
        in_block = hard_next = False
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped.startswith("#"):
                comment = stripped.removeprefix("#")
                if HARD_START in comment:
                    in_block = True
                elif HARD_END in comment:
                    in_block = False
                elif HARD_NEXT in comment:
                    hard_next = True
                continue
            line = raw.split(" #", 1)[0].strip()
            if not line:
                continue
            pattern, *owners = line.split()
            rules.append(
                CodeOwnersRule(
                    _compile(pattern),
                    tuple(owner for owner in owners if owner.startswith("@")),
                    hard=in_block or hard_next,
                )
            )
            hard_next = False
        return cls(tuple(rules))

    def rule_for(self, path: str) -> CodeOwnersRule | None:
        """The last rule matching ``path``, which GitHub applies; ``None`` when none does."""
        for rule in reversed(self.rules):
            if rule.pattern.fullmatch(path):
                return rule
        return None

    def owners_for(self, path: str) -> tuple[str, ...]:
        """``@user`` and ``@org/team`` handles owning ``path``; empty when it is unowned."""
        rule = self.rule_for(path)
        return rule.owners if rule is not None else ()

    async def approved_by(self, paths: list[str], approvers: set[str]) -> bool:
        """Whether each owned path has an approval from one of its owners."""
        teams: dict[str, list[str]] = {}
        logins = {login.lower() for login in approvers}
        for path in paths:
            owners = self.owners_for(path)
            if not owners:
                continue
            approved = False
            for owner in owners:
                handle = owner.removeprefix("@")
                if "/" not in handle:
                    approved |= handle.lower() in logins
                else:
                    if handle not in teams:
                        org, slug = handle.split("/", 1)
                        teams[handle] = await team_members(org, slug) or []
                    approved |= any(login.lower() in logins for login in teams[handle])
                if approved:
                    break
            if not approved:
                return False
        return True

    @classmethod
    async def fetch(cls, repo: RepoClient, ref: str | None, *, strict: bool = False) -> Self | None:
        """The first CODEOWNERS file GitHub would read at ``ref``; ``None`` when there is none."""
        for path in CODEOWNERS_PATHS:
            content = await repo.read_file(path, ref, max_chars=CODEOWNERS_MAX_CHARS, strict=strict)
            if content is not None:
                return cls.parse(content)
        return None
