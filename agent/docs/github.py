"""Repository-scoped GitHub access and ordinary docs PR URL discovery."""

import re
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, TypeAdapter

from agent.docs.models import DocsSettings, DocsSnapshot, LinkedPR, PullRequest
from agent.github.app import PermissionMap
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.sandbox_access import repository_token

READ_PERMISSIONS: PermissionMap = {"contents": "read", "pull_requests": "read", "issues": "read"}


async def token(repository: str, permissions: PermissionMap = READ_PERMISSIONS) -> str:
    access = await repository_token([repository], permissions=permissions)
    # The development OAuth fallback is installation-wide and cannot be narrowed.
    if not access.token or not access.expires_at:
        raise RuntimeError("Open SWE Docs requires repository-scoped GitHub App credentials")
    return access.token


async def request(
    repository: str,
    path: str,
    *,
    method: str = "GET",
    data: Mapping[str, object] | None = None,
    permissions: PermissionMap = READ_PERMISSIONS,
) -> object:
    async with github_client(token=await token(repository, permissions)) as client:
        kwargs = {"json": dict(data)} if data is not None else {}
        response = await github_request(
            client,
            method,
            f"{GITHUB_API_BASE}/repos/{repository}" + (f"/{path}" if path else ""),
            **kwargs,
        )
        response.raise_for_status()
        return response.json()


class Comment(BaseModel):
    body: str | None = None


async def comments(repository: str, number: int) -> list[Comment]:
    result: list[Comment] = []
    # A link can be in an issue comment, an inline review comment, or a review body.
    for path in (
        f"issues/{number}/comments",
        f"pulls/{number}/comments",
        f"pulls/{number}/reviews",
    ):
        page = 1
        while True:
            batch = TypeAdapter(list[Comment]).validate_python(
                await request(repository, f"{path}?per_page=100&page={page}")
            )
            result.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return result


def linked_numbers(text: str, docs_repository: str) -> list[int]:
    """Accept ordinary PR URLs, but never credentials, lookalike hosts or repos."""
    numbers: set[int] = set()
    pattern = re.compile(
        rf"/{re.escape(docs_repository)}/pull/([1-9][0-9]*)(?:/(?:files|commits|checks))?/?$",
        re.IGNORECASE,
    )
    for candidate in re.findall(r"https://[^\s<>\"']+", text, re.IGNORECASE):
        try:
            parsed = urlsplit(candidate.rstrip(".,;:!)]}"))
        except ValueError:
            continue
        if (
            parsed.scheme.lower() != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.username
            or parsed.password
        ):
            continue
        if match := pattern.fullmatch(parsed.path):
            numbers.add(int(match.group(1)))
    return sorted(numbers)


async def pull_request(repository: str, number: int) -> PullRequest:
    return PullRequest.model_validate(await request(repository, f"pulls/{number}"))


async def snapshot(repository: str, number: int, config: DocsSettings) -> DocsSnapshot:
    pr = await pull_request(repository, number)
    bodies = [
        pr.body or "",
        *(comment.body or "" for comment in await comments(repository, number)),
    ]
    links = []
    for linked_number in linked_numbers("\n".join(bodies), config.docs_repository):
        linked = await pull_request(config.docs_repository, linked_number)
        # API lookup is constrained to the configured repo, including fork PRs.
        links.append(
            LinkedPR(
                number=linked.number,
                sha=linked.head.sha,
                base_sha=linked.base.sha,
                url=linked.html_url,
                state=linked.state,
                draft=linked.draft,
            )
        )
    base = await request(
        config.docs_repository, f"commits/{quote(config.docs_base_branch, safe='')}"
    )

    class Commit(BaseModel):
        sha: str

    return DocsSnapshot(
        source_repository=repository,
        source=pr,
        settings=config,
        links=links,
        docs_base_sha=Commit.model_validate(base).sha,
    )


async def ensure_skip_label(repository: str) -> None:
    permissions: PermissionMap = {"issues": "write"}
    async with github_client(token=await token(repository, permissions)) as client:
        response = await github_request(
            client, "GET", f"{GITHUB_API_BASE}/repos/{repository}/labels/skip-docs"
        )
        if response.status_code == 404:
            response = await github_request(
                client,
                "POST",
                f"{GITHUB_API_BASE}/repos/{repository}/labels",
                json={
                    "name": "skip-docs",
                    "color": "ededed",
                    "description": "Skip automatic Open SWE documentation checks",
                },
            )
            if response.status_code == 422:
                # A concurrent settings save may have created it first.
                response = await github_request(
                    client, "GET", f"{GITHUB_API_BASE}/repos/{repository}/labels/skip-docs"
                )
        response.raise_for_status()
