"""Azure DevOps Git REST API: default branch and pull request creation."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

API_VERSION = "7.1"


def basic_auth_headers(pat: str) -> dict[str, str]:
    """HTTP headers for Azure DevOps REST (Basic auth with empty username).

    ``pat`` may be a PAT or an OAuth access token from Entra ID; Git uses the same
    Basic encoding for both.
    """
    encoded = base64.b64encode(f":{pat}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def org_base_url(organization: str) -> str:
    """Base URL for REST and web paths (dev.azure.com or full URL for Server / collection)."""
    org = organization.strip().rstrip("/")
    if org.startswith("http"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}"


# Short names used inside this module’s REST helpers.
_basic_headers = basic_auth_headers
_org_base_url = org_base_url


def azure_devops_https_clone_url(organization: str, project: str, repository_name: str) -> str:
    """HTTPS clone URL aligned with :func:`org_base_url` (cloud and on-premises).

    Omits the ``.git`` suffix to match Azure DevOps web \"Clone\" URLs; ``git clone``
    accepts this form.
    """
    base = org_base_url(organization)
    p = quote(project.strip(), safe="")
    n = quote(repository_name.strip(), safe="")
    return f"{base}/{p}/_git/{n}"


def azure_devops_git_credential_host(organization: str) -> str:
    """Host for git credential store (``pat:...@host``), for push/clone over HTTPS."""
    netloc = urlparse(org_base_url(organization)).netloc
    return netloc if netloc else "dev.azure.com"


def resolve_azure_devops_pat(configurable: dict[str, Any] | None = None) -> str | None:
    """Resolve Azure DevOps credential string used like a PAT (Basic ``:<token>``).

    Order:

    1. ``AZURE_DEVOPS_PAT`` environment variable (PAT or static OAuth token).
    2. ``configurable['azure_devops_pat']`` when set.
    3. Entra ID service principal via :func:`azure_devops_identity_token.get_azure_devops_access_token_sync`
       when ``AZURE_DEVOPS_USE_ENTRA_IDENTITY`` and related Azure env vars are set
       (token is cached/refreshed by ``azure-identity``).

    Async HTTP handlers should call :func:`resolve_azure_devops_pat_async` instead so
    Entra token acquisition does not block the event loop.
    """
    env_pat = (os.environ.get("AZURE_DEVOPS_PAT") or "").strip()
    if env_pat:
        return env_pat
    if configurable:
        c = configurable.get("azure_devops_pat")
        if c is not None and str(c).strip():
            return str(c).strip()
    from .azure_devops_identity_token import get_azure_devops_access_token_sync

    tok = get_azure_devops_access_token_sync()
    if tok:
        return tok
    return None


async def resolve_azure_devops_pat_async(
    configurable: dict[str, Any] | None = None,
) -> str | None:
    """Like :func:`resolve_azure_devops_pat` but fetches Entra tokens off the event loop."""
    env_pat = (os.environ.get("AZURE_DEVOPS_PAT") or "").strip()
    if env_pat:
        return env_pat
    if configurable:
        c = configurable.get("azure_devops_pat")
        if c is not None and str(c).strip():
            return str(c).strip()
    from .azure_devops_identity_token import get_azure_devops_access_token_async

    tok = await get_azure_devops_access_token_async()
    if tok:
        return tok
    return None


def _refs_heads(branch: str) -> str:
    b = branch.strip()
    return b if b.startswith("refs/heads/") else f"refs/heads/{b}"


def _branch_from_ref(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return ref


async def get_azure_devops_repository(
    organization: str,
    project: str,
    repository_name_or_id: str,
    pat: str,
) -> dict[str, Any] | None:
    """Fetch repository metadata including id and defaultBranch."""
    base = _org_base_url(organization)
    proj = quote(project, safe="")
    repo = quote(repository_name_or_id, safe="")
    url = f"{base}/{proj}/_apis/git/repositories/{repo}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                url,
                headers=_basic_headers(pat),
                params={"api-version": API_VERSION},
            )
            if r.status_code != 200:  # noqa: PLR2004
                logger.error(
                    "Azure DevOps get repository failed org=%s project=%s repo=%s status=%s: %s",
                    organization,
                    project,
                    repository_name_or_id,
                    r.status_code,
                    r.text[:500],
                )
                return None
            return r.json()
    except httpx.HTTPError:
        logger.exception(
            "Azure DevOps get repository HTTP error (org=%s project=%s repo=%s)",
            organization,
            project,
            repository_name_or_id,
        )
        return None


async def get_azure_devops_default_branch(
    organization: str,
    project: str,
    repository_name: str,
    pat: str,
) -> str:
    """Return short branch name (e.g. main) for the repo default branch."""
    data = await get_azure_devops_repository(organization, project, repository_name, pat)
    if not data:
        return "main"
    ref = data.get("defaultBranch") or "refs/heads/main"
    return _branch_from_ref(ref)


async def _find_active_pr_for_source(
    client: httpx.AsyncClient,
    base: str,
    project: str,
    repository_id: str,
    pat: str,
    source_ref: str,
) -> tuple[str | None, int | None]:
    """Prefer server-side filter on source branch to avoid missing PRs in large repos."""
    proj = quote(project, safe="")
    url = f"{base}/{proj}/_apis/git/repositories/{repository_id}/pullrequests"
    params: dict[str, Any] = {
        "api-version": API_VERSION,
        "searchCriteria.status": "active",
        "searchCriteria.sourceRefName": source_ref,
        "$top": 20,
    }
    r = await client.get(url, headers=_basic_headers(pat), params=params)
    if r.status_code == 400:  # noqa: PLR2004
        # Some hosts reject sourceRefName filter; fall back to scanning active PRs.
        logger.debug(
            "Azure DevOps list PRs: searchCriteria.sourceRefName not accepted (400); "
            "retrying without source filter (repo_id=%s ref=%s)",
            repository_id,
            source_ref,
        )
        fallback_params = {
            "api-version": API_VERSION,
            "searchCriteria.status": "active",
            "$top": 50,
        }
        r = await client.get(url, headers=_basic_headers(pat), params=fallback_params)
    if r.status_code != 200:  # noqa: PLR2004
        return None, None
    for pr in r.json().get("value", []):
        if (pr.get("sourceRefName") or "") == source_ref:
            pr_id = pr.get("pullRequestId")
            web = (pr.get("_links") or {}).get("web", {}) or {}
            href = web.get("href") or ""
            if not href and pr_id:
                href = f"{base}/{proj}/_git/{repository_id}/pullrequest/{pr_id}"
            return href or None, int(pr_id) if pr_id else None
    return None, None


async def create_azure_devops_pull_request(
    organization: str,
    project: str,
    repository_name: str,
    pat: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
    *,
    draft: bool = True,
    reviewers: list[dict[str, Any]] | None = None,
    work_item_ids: list[int] | None = None,
) -> tuple[str | None, int | None, bool]:
    """Create a pull request in Azure DevOps Git.

    ``reviewers`` entries are typically ``{"id": "<identity-guid>", "vote": 0}``.
    ``work_item_ids`` links Boards work items to the PR (``workItemRefs`` on create).

    Returns (pr_web_url, pull_request_id, already_existed).
    """
    base = _org_base_url(organization)
    repo_data = await get_azure_devops_repository(organization, project, repository_name, pat)
    if not repo_data:
        return None, None, False
    repo_id = repo_data.get("id")
    if not repo_id:
        return None, None, False

    proj = quote(project, safe="")
    api_url = f"{base}/{proj}/_apis/git/repositories/{repo_id}/pullrequests"
    source_ref = _refs_heads(head_branch)
    target_ref = _refs_heads(base_branch)
    payload: dict[str, Any] = {
        "sourceRefName": source_ref,
        "targetRefName": target_ref,
        "title": title,
        "description": body,
        "isDraft": draft,
    }
    if reviewers:
        payload["reviewers"] = reviewers
    if work_item_ids:
        payload["workItemRefs"] = [{"id": str(wid)} for wid in work_item_ids]

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(
                api_url,
                headers=_basic_headers(pat),
                params={"api-version": API_VERSION},
                json=payload,
            )
        except httpx.HTTPError:
            logger.warning(
                "Azure DevOps create PR HTTP error org=%s project=%s repo=%s; probing for existing PR "
                "(sourceRef=%s)",
                organization,
                project,
                repository_name,
                source_ref,
                exc_info=True,
            )
            existing = await _find_active_pr_for_source(
                client, base, project, str(repo_id), pat, source_ref
            )
            if existing[0]:
                logger.info(
                    "Recovered after HTTP error — using existing Azure DevOps PR ref=%s: %s",
                    source_ref,
                    existing[0],
                )
                return existing[0], existing[1], True
            return None, None, False
        if r.status_code in (200, 201):  # noqa: PLR2004
            pr = r.json()
            pr_id = pr.get("pullRequestId")
            web = (pr.get("_links") or {}).get("web", {}) or {}
            href = web.get("href") or ""
            if not href and pr_id:
                # Prefer human-readable repo path in URL
                href = f"{base}/{proj}/_git/{quote(repository_name, safe='')}/pullrequest/{pr_id}"
            logger.info(
                "Azure DevOps PR created org=%s project=%s repo=%s id=%s url=%s",
                organization,
                project,
                repository_name,
                pr_id,
                href,
            )
            return href, int(pr_id) if pr_id else None, False

        logger.warning(
            "Azure DevOps create PR failed org=%s project=%s repo=%s status=%s: %s",
            organization,
            project,
            repository_name,
            r.status_code,
            r.text[:800],
        )
        existing = await _find_active_pr_for_source(
            client, base, project, str(repo_id), pat, source_ref
        )
        if existing[0]:
            logger.info(
                "Azure DevOps using existing active PR for %s/%s ref=%s: %s",
                project,
                repository_name,
                source_ref,
                existing[0],
            )
            return existing[0], existing[1], True
        return None, None, False
