"""The pull request whose review findings a tool reads or changes, and who it acts for."""

from dataclasses import dataclass
from typing import Self

from fastapi import HTTPException

from openswe import thread_ids
from openswe.credential_scope import pr_author_login
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.run_config import RunConfig
from openswe.slack.client import parse_github_pr_url
from openswe.threads import index


@dataclass(frozen=True)
class ReviewFindingTarget:
    owner: str
    repo: str
    number: int
    login: str

    @classmethod
    async def resolve(cls, pr_url: str) -> Self:
        """``pr_url``'s pull request, else this conversation's, for a person who can access it.

        Raises ``ValueError`` with a message for the model when either is missing.
        """
        cfg = RunConfig.from_runtime()
        if pr_ref := parse_github_pr_url(pr_url):
            owner, repo, number = pr_ref.owner, pr_ref.repo, pr_ref.number
        elif not pr_url.strip() and cfg.repo and cfg.pr_number is not None:
            owner, repo, number = cfg.repo.owner, cfg.repo.name, cfg.pr_number
        else:
            raise ValueError("pr_url must be a GitHub pull request URL")
        try:
            login = cfg.mcp_caller or await pr_author_login()
        except (PermissionError, RuntimeError) as exc:
            raise ValueError(str(exc)) from exc
        if not login:
            raise ValueError("this conversation has no person to act for")
        try:
            await require_repo_access_for_user(login, f"{owner}/{repo}")
        except HTTPException as exc:
            raise ValueError(f"Repository access denied: {exc.detail}") from exc
        return cls(owner=owner, repo=repo, number=number, login=login)

    async def reviewer_thread_id(self) -> str:
        return await index.current_thread_id(
            thread_ids.reviewer_thread_id(self.owner, self.repo, self.number)
        )
