"""SCM-agnostic pull request creation.

Supports GitHub and Azure DevOps Git (pull requests via REST).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


@runtime_checkable
class PullRequestClient(Protocol):
    """Creates a pull request and resolves the default base branch."""

    async def get_default_branch(self) -> str:
        """Return the repository default branch name (e.g. main)."""
        ...

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        *,
        reviewers: list[dict[str, Any]] | None = None,
        draft: bool | None = None,
        work_item_ids: list[int] | None = None,
    ) -> tuple[str | None, int | None, bool]:
        """Return (pr_url, pr_number, pr_already_existed)."""
        ...


class GitHubPullRequestClient:
    """GitHub REST API implementation of :class:`PullRequestClient`."""

    def __init__(self, owner: str, name: str, token: str) -> None:
        self._owner = owner
        self._name = name
        self._token = token

    async def get_default_branch(self) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{self._owner}/{self._name}",
                headers=_github_headers(self._token),
            )
            if resp.status_code != 200:  # noqa: PLR2004
                return "main"
            data = resp.json()
            return data.get("default_branch", "main") if isinstance(data, dict) else "main"

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        *,
        reviewers: list[dict[str, Any]] | None = None,
        draft: bool | None = None,
    ) -> tuple[str | None, int | None, bool]:
        if reviewers is not None:
            logger.debug("GitHub create_pull_request ignores reviewers (Azure DevOps only)")
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body,
                "draft": draft if draft is not None else True,
            }
            resp = await client.post(
                f"{_GITHUB_API}/repos/{self._owner}/{self._name}/pulls",
                headers=_github_headers(self._token),
                json=payload,
            )
            if resp.status_code == 201:  # noqa: PLR2004
                pr = resp.json()
                if isinstance(pr, dict):
                    return pr.get("html_url"), pr.get("number"), False
            if resp.status_code == 422:  # noqa: PLR2004
                existing_resp = await client.get(
                    f"{_GITHUB_API}/repos/{self._owner}/{self._name}/pulls",
                    headers=_github_headers(self._token),
                    params={"head": f"{self._owner}:{head_branch}", "state": "open"},
                )
                if existing_resp.status_code == 200:  # noqa: PLR2004
                    items = existing_resp.json()
                    if isinstance(items, list) and items:
                        pr = items[0]
                        return pr.get("html_url"), pr.get("number"), True
        return None, None, False


class AzureDevOpsPullRequestClient:
    """Azure DevOps Git REST API implementation of :class:`PullRequestClient`."""

    def __init__(
        self,
        organization: str,
        project: str,
        repository_name: str,
        pat: str,
    ) -> None:
        self._organization = organization
        self._project = project
        self._repository_name = repository_name
        self._pat = pat

    async def get_default_branch(self) -> str:
        from .azure_devops import get_azure_devops_default_branch

        return await get_azure_devops_default_branch(
            self._organization,
            self._project,
            self._repository_name,
            self._pat,
        )

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        *,
        reviewers: list[dict[str, Any]] | None = None,
        draft: bool | None = None,
        work_item_ids: list[int] | None = None,
    ) -> tuple[str | None, int | None, bool]:
        from .azure_devops import create_azure_devops_pull_request

        return await create_azure_devops_pull_request(
            organization=self._organization,
            project=self._project,
            repository_name=self._repository_name,
            pat=self._pat,
            title=title,
            head_branch=head_branch,
            base_branch=base_branch,
            body=body,
            draft=draft if draft is not None else True,
            reviewers=reviewers,
            work_item_ids=work_item_ids,
        )


def azure_devops_organization(repo_config: dict) -> str | None:
    """Azure DevOps org from repo config."""
    v = repo_config.get("organization") or repo_config.get("owner")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def pull_request_client_from_repo_config(
    repo_config: dict,
    token: str,
) -> PullRequestClient:
    """Build a PR client. ``token`` is GitHub token or Azure DevOps PAT."""
    provider = (repo_config.get("scm_provider") or "github").lower()
    if provider == "github":
        owner = repo_config.get("owner")
        name = repo_config.get("name")
        if not owner or not name:
            raise ValueError("GitHub repo config requires 'owner' and 'name'")
        return GitHubPullRequestClient(str(owner), str(name), token)
    if provider == "azure_devops":
        org = azure_devops_organization(repo_config)
        project = repo_config.get("project")
        name = repo_config.get("name")
        if not org or not project or not name:
            raise ValueError(
                "Azure DevOps repo config requires 'owner' (or 'organization'), "
                "'project', and 'name'",
            )
        return AzureDevOpsPullRequestClient(str(org), str(project), str(name), token)
    raise NotImplementedError(f"Unsupported scm_provider: {provider!r}")
