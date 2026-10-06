"""A repository's CODEOWNERS file: who owns each path, by GitHub's last-match-wins rule."""

import re
from dataclasses import dataclass
from typing import Self

from agent.github.org_membership import team_members
from agent.github.repo_files import fetch_repo_file

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
CODEOWNERS_MAX_CHARS = 3_000_000


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


@dataclass(frozen=True, slots=True)
class CodeOwners:
    rules: tuple[CodeOwnersRule, ...]

    @classmethod
    def parse(cls, text: str) -> Self:
        rules: list[CodeOwnersRule] = []
        for raw in text.splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            pattern, *owners = line.split()
            rules.append(
                CodeOwnersRule(
                    _compile(pattern), tuple(owner for owner in owners if owner.startswith("@"))
                )
            )
        return cls(tuple(rules))

    def owners_for(self, path: str) -> tuple[str, ...]:
        """``@user`` and ``@org/team`` handles owning ``path``; empty when it is unowned."""
        for rule in reversed(self.rules):
            if rule.pattern.fullmatch(path):
                return rule.owners
        return ()

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
    async def fetch(
        cls, owner: str, repo: str, ref: str | None, *, token: str, strict: bool = False
    ) -> Self | None:
        """The first CODEOWNERS file GitHub would read at ``ref``; ``None`` when there is none."""
        for path in CODEOWNERS_PATHS:
            content = await fetch_repo_file(
                owner, repo, path, ref, token=token, max_chars=CODEOWNERS_MAX_CHARS, strict=strict
            )
            if content is not None:
                return cls.parse(content)
        return None
