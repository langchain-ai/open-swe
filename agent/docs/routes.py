"""Admin-managed Open SWE Docs configuration."""

import asyncio
from urllib.parse import quote
from uuid import uuid7

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP
from agent.docs import github
from agent.docs.coordinator import cancel, job_lock
from agent.docs.models import JOBS, SETTINGS, DocsSettings, repository_name, settings
from agent.utils.url_safety import resolve_and_validate

router = APIRouter()


@router.get("/docs-settings", response_model=DocsSettings)
async def get_docs_settings(_session: dict[str, object] = ADMIN_DEP) -> DocsSettings:
    return await settings()


class DocsRepoUpdate(BaseModel):
    full_name: str
    enabled: bool

    @field_validator("full_name")
    @classmethod
    def normalize(cls, value: str) -> str:
        return repository_name(value)


@router.get("/enabled-docs-repos")
async def get_docs_repositories(_session: dict[str, object] = SESSION_DEP) -> dict[str, list[str]]:
    config = await settings()
    return {"repos": config.source_repositories if config.enabled else []}


@router.put("/enabled-docs-repos")
async def put_docs_repository(
    update: DocsRepoUpdate, _session: dict[str, object] = ADMIN_DEP
) -> dict[str, list[str]]:
    async with job_lock("docs-settings"):
        current = await settings()
        if update.enabled and not current.docs_repository:
            raise HTTPException(422, "Configure the docs target before enabling documentation")
        sources = set(current.source_repositories)
        if update.enabled:
            sources.add(update.full_name)
        else:
            sources.discard(update.full_name)
        try:
            next_settings = DocsSettings.model_validate(
                {
                    **current.model_dump(),
                    "enabled": bool(current.docs_repository),
                    "source_repositories": sorted(sources),
                }
            )
        except ValueError as exc:
            raise HTTPException(422, "The docs target cannot check its own documentation") from exc
        await save_docs_settings(next_settings)
    return {"repos": sorted(sources)}


@router.put("/docs-settings", response_model=DocsSettings)
async def put_docs_settings(
    update: DocsSettings, _session: dict[str, object] = ADMIN_DEP
) -> DocsSettings:
    async with job_lock("docs-settings"):
        current = await settings()
        if update.revision != current.revision:
            raise HTTPException(409, "Docs settings changed; reload and try again")
        return await save_docs_settings(update)


async def save_docs_settings(update: DocsSettings) -> DocsSettings:
    if update.docs_mcp_url:
        safe, _, _, _ = await asyncio.to_thread(resolve_and_validate, update.docs_mcp_url)
        if not safe:
            raise HTTPException(422, "Docs MCP must resolve to a public HTTPS server")
    if update.enabled:
        # Refuse to enable a setup that cannot read sources and write the docs repo.
        try:
            for repository in update.source_repositories:
                await github.token(repository)
                await github.ensure_skip_label(repository)
            await github.token(
                update.docs_repository, {"contents": "write", "pull_requests": "write"}
            )
            await github.request(
                update.docs_repository, "commits/" + quote(update.docs_base_branch, safe="")
            )
        except Exception as exc:
            raise HTTPException(
                422, "GitHub App must have access to the selected repos and docs base branch"
            ) from exc
    saved = update.model_copy(update={"revision": uuid7().hex})
    await SETTINGS.put("default", saved)
    for job in await JOBS.search_all():
        if job.status in {"pending", "running"}:
            async with job_lock(job.snapshot.key):
                current = await JOBS.get(job.snapshot.key)
                if current and current.status in {"pending", "running"}:
                    await cancel(
                        current,
                        "Docs settings changed; a subsequent PR event will use the new settings.",
                    )
    return saved
