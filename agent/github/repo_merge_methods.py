"""Merge methods a repository allows, read with the signed-in user's permissions."""

from typing import Literal

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_status import pull_request_identity

MergeMethod = Literal["squash", "merge", "rebase"]

_MERGE_METHOD_FLAGS: tuple[tuple[MergeMethod, str], ...] = (
    ("squash", "allow_squash_merge"),
    ("merge", "allow_merge_commit"),
    ("rebase", "allow_rebase_merge"),
)


class RepositoryMergeMethods(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    merge_methods: list[MergeMethod]


async def repository_merge_methods(owner: str, repo: str, token: str) -> RepositoryMergeMethods:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": 1}) is None:
        raise HTTPException(422, "invalid repository")
    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client, "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
            )
        payload = response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Could not load merge settings from GitHub") from exc
    if not response.is_success or not isinstance(payload, dict):
        raise HTTPException(502, "Could not load merge settings from GitHub")
    return RepositoryMergeMethods(
        merge_methods=[
            method
            for method, flag in _MERGE_METHOD_FLAGS
            if not isinstance(payload.get(flag), bool) or payload[flag]
        ]
    )
