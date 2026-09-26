"""Squash commit message: the PR description, then its commits, without GitHub's repeats."""

import logging
import re
from typing import Self

import httpx2
from pydantic import BaseModel, TypeAdapter, ValidationError

from agent.github.http import GITHUB_API_BASE, github_request

logger = logging.getLogger(__name__)

_COMMITS_PER_PAGE = 100
# GitHub lists at most 250 commits for a pull request.
_MAX_COMMIT_PAGES = 3
_CO_AUTHOR = re.compile(r"^co-authored-by:\s*(.+?)\s*<([^<>\s]+)>\s*$", re.IGNORECASE)


class GitPerson(BaseModel):
    name: str
    email: str


class GitHubUser(BaseModel):
    login: str


class GitCommit(BaseModel):
    message: str
    author: GitPerson | None = None


class CommitParent(BaseModel):
    sha: str


class PullRequestCommit(BaseModel):
    commit: GitCommit
    author: GitHubUser | None = None
    parents: list[CommitParent]


class _PullRequest(BaseModel):
    title: str
    body: str | None = None


_COMMITS = TypeAdapter(list[PullRequestCommit])


def _comparable(line: str) -> str:
    return line.strip().lstrip("*-").strip().casefold()


class SquashSource(BaseModel):
    title: str
    body: str | None
    commits: list[PullRequestCommit]

    @classmethod
    async def fetch(
        cls, client: httpx2.AsyncClient, owner: str, repo: str, number: int
    ) -> Self | None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        try:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
            pull = _PullRequest.model_validate(response.json())
            commits: list[PullRequestCommit] = []
            for page in range(1, _MAX_COMMIT_PAGES + 1):
                response = await github_request(
                    client,
                    "GET",
                    f"{url}/commits",
                    params={"per_page": _COMMITS_PER_PAGE, "page": page},
                )
                response.raise_for_status()
                listed = _COMMITS.validate_python(response.json())
                commits.extend(listed)
                if len(listed) < _COMMITS_PER_PAGE:
                    break
        except httpx2.HTTPError, ValueError, ValidationError:
            logger.warning(
                "Could not build squash commit message; using GitHub's default",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
                exc_info=True,
            )
            return None
        return cls(title=pull.title, body=pull.body, commits=commits)

    def message(self, merger_login: str | None = None) -> str | None:
        co_authors: dict[str, str] = {}
        body_lines: list[str] = []
        for line in (self.body or "").splitlines():
            if match := _CO_AUTHOR.match(line.strip()):
                co_authors.setdefault(match[2].casefold(), f"{match[1]} <{match[2]}>")
            else:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        seen = {_comparable(line) for line in body_lines} | {_comparable(self.title)}
        headlines: list[str] = []
        merger = merger_login.casefold() if merger_login else None
        for entry in self.commits:
            if len(entry.parents) > 1:
                continue
            lines = entry.commit.message.splitlines()
            headline = lines[0].strip() if lines else ""
            if headline and _comparable(headline) not in seen:
                seen.add(_comparable(headline))
                headlines.append(f"* {headline}")
            author = entry.commit.author
            if author and (entry.author is None or entry.author.login.casefold() != merger):
                co_authors.setdefault(author.email.casefold(), f"{author.name} <{author.email}>")
            for line in lines[1:]:
                if match := _CO_AUTHOR.match(line.strip()):
                    co_authors.setdefault(match[2].casefold(), f"{match[1]} <{match[2]}>")
        trailers = [f"Co-authored-by: {person}" for person in co_authors.values()]
        sections = [
            section for section in (body, "\n".join(headlines), "\n".join(trailers)) if section
        ]
        return "\n\n".join(sections) or None
