"""Deterministic docs dispatch, cancellation, deduplication and publication guards."""

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from uuid import NAMESPACE_URL, uuid5

from langgraph_sdk import get_client
from langgraph_sdk.errors import ConflictError
from pydantic import BaseModel

from agent.dispatch import dispatch_agent_run
from agent.docs import github
from agent.docs.models import JOBS, DocsJob, DocsSnapshot, eligible, settings
from agent.github.checks import complete_review_check_run, create_review_check_run
from agent.prompts import prompt
from agent.review.enabled_repos import is_review_repo_enabled
from agent.review.findings import get_thread_metadata, set_reviewer_thread_metadata
from agent.thread_ids import reviewer_thread_id
from agent.threads.creation import create_lock_thread, create_thread
from agent.workspaces.routing import repo_is_routable, workspace_for_repo

logger = logging.getLogger(__name__)


@asynccontextmanager
async def job_lock(key: str) -> AsyncIterator[None]:
    client = get_client()
    lock_id = str(uuid5(NAMESPACE_URL, f"open-swe:docs-lock:{key}"))
    await create_lock_thread(client, lock_id, ttl_minutes=10)
    try:
        yield
    finally:
        await client.threads.delete(lock_id)


async def complete_check(job: DocsJob, title: str, summary: str) -> None:
    if job.check_id is None:
        return
    owner, repo = job.snapshot.source_repository.split("/")
    try:
        await complete_review_check_run(
            owner=owner,
            repo=repo,
            check_run_id=job.check_id,
            token=await github.token(job.snapshot.source_repository, {"checks": "write"}),
            conclusion="neutral",
            title=title,
            summary=summary[:60000],
        )
    except Exception:
        logger.exception("Could not complete docs check", extra={"source_pr": job.snapshot.key})


async def cancel(job: DocsJob, reason: str) -> None:
    # Persist invalidation before interrupting: publication guards fail even if cancellation races.
    job.status = "skipped"
    job.result = reason
    await JOBS.put(job.snapshot.key, job)
    if job.run_id:
        await get_client().runs.cancel(job.thread_id, job.run_id, wait=False)
    await complete_check(job, "Docs run skipped", reason)


async def coordinate(repository: str, number: int, *, allow_dispatch: bool = True) -> str:
    config = await settings()
    key = f"{repository.lower()}#{number}"
    async with job_lock(key):
        previous = await JOBS.get(key)
        if not config.enabled or repository.lower() not in config.source_repositories:
            if previous and previous.status in {"pending", "running"}:
                await cancel(previous, "Open SWE Docs is disabled for this repository.")
            return "skipped"
        owner, name = repository.split("/")
        if not await repo_is_routable(owner, name):
            return "skipped"
        pr = await github.pull_request(repository, number)
        code_review = await is_review_repo_enabled(owner, name) and pr.state == "open"
        if code_review and pr.draft:
            from agent.webhooks.common import draft_review_enabled_for_author

            code_review = await draft_review_enabled_for_author(
                pr.user.login if pr.user else "", {"owner": owner, "name": name}
            )
        docs = eligible(config, repository, pr)
        if (
            not docs
            and previous
            and (previous.snapshot.docs_enabled or not code_review)
            and previous.status in {"pending", "running"}
        ):
            await cancel(previous, "Source PR is closed, draft, or labeled skip-docs.")
        if not code_review and not docs:
            return "skipped"
        if not allow_dispatch:
            return "ignored"
        if docs:
            current = await github.snapshot(repository, number, config)
            if not eligible(config, repository, current.source):
                if previous and previous.status in {"pending", "running"}:
                    await cancel(previous, "Source PR became ineligible during preparation.")
                return "skipped"
        else:
            current = DocsSnapshot(
                source_repository=repository, source=pr, settings=config, docs_base_sha=""
            )
        current = current.model_copy(
            update={"code_review_enabled": code_review, "docs_enabled": docs}
        )
        pr = current.source
        if (
            previous
            and docs
            and previous.docs_pr_url
            and not current.links
            and previous.snapshot.settings.docs_repository == config.docs_repository
        ):
            # Recover a bot-created PR whose source-link comment failed, even after a new source push.
            await recover_link(current, previous.docs_pr_url)
        if previous and previous.snapshot.fingerprint == current.fingerprint:
            if previous.status == "completed":
                return "unchanged"
            if previous.status in {"pending", "running"} and previous.run_id:
                run = await get_client().runs.get(previous.thread_id, previous.run_id)
                if run["status"] in {"pending", "running"}:
                    return "unchanged"
                previous.status = "failed"
        if previous and previous.status in {"pending", "running"}:
            await cancel(previous, "Docs inputs changed; replaced by a current run.")
        job = DocsJob(
            snapshot=current,
            thread_id=reviewer_thread_id(owner, name, number),
            docs_pr_url=previous.docs_pr_url if previous else "",
        )
        client = get_client()
        owner, repo = repository.split("/")
        workspace = await workspace_for_repo(owner, repo)
        await create_thread(
            client,
            job.thread_id,
            title=f"Review: {repository}#{number}",
            if_exists="do_nothing",
            metadata={
                "agent_kind": "reviewer",
                "docs_context": True,
                "kind": "reviewer",
                "source": "github",
                "owner_type": "system",
                # Either checkout may contain private content, including for a public source PR.
                "visibility": "private",
                "admin_thread": True,
                "repo": {"owner": owner, "name": repo},
                "workspace": workspace,
                "github_token_repositories": [repository, config.docs_repository],
            },
        )
        await client.threads.update(
            job.thread_id,
            metadata={
                "agent_kind": "reviewer",
                "docs_context": True,
                "visibility": "private",
                "admin_thread": True,
                "github_token_repositories": [repository, config.docs_repository],
            },
        )
        metadata = await get_thread_metadata(job.thread_id)
        await set_reviewer_thread_metadata(
            job.thread_id,
            pr={
                "owner": owner,
                "name": repo,
                "number": number,
                "url": pr.html_url,
                "title": pr.title,
                "head_ref": pr.head.ref,
                "base_ref": pr.base.ref,
                "author": pr.user.login if pr.user else "",
            },
            head_sha=pr.head.sha,
            watch=code_review,
        )
        await JOBS.put(key, job)
        try:
            try:
                job.check_id = (
                    await create_review_check_run(
                        owner=owner,
                        repo=repo,
                        head_sha=pr.head.sha,
                        token=await github.token(repository, {"checks": "write"}),
                        name="Open SWE Docs",
                        title="Docs review in progress",
                        summary="Checking documentation accuracy and coverage.",
                    )
                    if docs
                    else None
                )
                if code_review:
                    from agent.webhooks.common import track_review_check_run

                    check = await create_review_check_run(
                        owner=owner,
                        repo=repo,
                        head_sha=pr.head.sha,
                        token=await github.token(repository, {"checks": "write"}),
                    )
                    if check is not None:
                        await track_review_check_run(
                            job.thread_id,
                            owner=owner,
                            repo=repo,
                            token=await github.token(repository, {"checks": "write"}),
                            check_run_id=check,
                        )
            except Exception:
                # Missing Checks permission must not disable docs work.
                logger.exception("Could not create docs check", extra={"source_pr": key})
            await JOBS.put(key, job)
            run = await dispatch_agent_run(
                job.thread_id,
                prompt("reviewer/dispatch", code_review_enabled=code_review, docs_enabled=docs),
                {
                    "docs_job_key": key,
                    "docs_fingerprint": current.fingerprint,
                    "workspace": workspace,
                    "repo": {"owner": owner, "name": repo},
                    "pr_number": number,
                    "pr_url": pr.html_url,
                    "source": "github",
                    "base_sha": pr.base.sha,
                    "head_sha": pr.head.sha,
                    "branch_name": pr.head.ref,
                    "code_review_enabled": code_review,
                    "docs_enabled": docs,
                    "re_review": bool(metadata.get("last_reviewed_sha")),
                    "last_reviewed_sha": metadata.get("last_reviewed_sha", ""),
                },
                source="github",
                thread_title=f"Review: {repository}#{number}",
                assistant_id="reviewer",
                client=client,
            )
            job.run_id = run["run_id"]
            job.status = "running"
            await JOBS.put(key, job)
        except Exception:
            job.status = "failed"
            await JOBS.put(key, job)
            await complete_check(job, "Docs run failed", "Could not dispatch the docs agent.")
            raise
        return "dispatched"


async def recover_link(snapshot: DocsSnapshot, url: str) -> None:
    numbers = github.linked_numbers(url, snapshot.settings.docs_repository)
    if not numbers or any(link.number == numbers[0] for link in snapshot.links):
        return
    linked = await github.pull_request(snapshot.settings.docs_repository, numbers[0])
    from agent.docs.models import LinkedPR

    snapshot.links.append(
        LinkedPR(
            number=linked.number,
            sha=linked.head.sha,
            base_sha=linked.base.sha,
            url=linked.html_url,
            state=linked.state,
            draft=linked.draft,
        )
    )
    snapshot.links.sort(key=lambda link: link.number)


async def publication_guard(expected: DocsSnapshot) -> DocsJob:
    job = await JOBS.get(expected.key)
    config = await settings()
    if not expected.docs_enabled:
        raise ValueError("Documentation is disabled for this run")
    if (
        not job
        or job.status not in {"pending", "running"}
        or job.snapshot.fingerprint != expected.fingerprint
        or config.revision != expected.settings.revision
    ):
        raise ValueError("Docs run is no longer current")
    pr = await github.pull_request(expected.source_repository, expected.source.number)
    if not eligible(config, expected.source_repository, pr):
        raise ValueError("Source PR is closed, draft, disabled, or labeled skip-docs")
    current = await github.snapshot(expected.source_repository, expected.source.number, config)
    if job.docs_pr_url and any(link.url == job.docs_pr_url for link in expected.links):
        await recover_link(current, job.docs_pr_url)
    if current.fingerprint != expected.fingerprint:
        raise ValueError("Source or linked docs PR changed; wait for the current docs run")
    return job


class EventRepository(BaseModel):
    full_name: str


class EventPR(BaseModel):
    number: int


class EventIssue(BaseModel):
    number: int
    pull_request: dict[str, object] | None = None


class DocsEvent(BaseModel):
    action: str = ""
    repository: EventRepository
    pull_request: EventPR | None = None
    issue: EventIssue | None = None


async def handle_event(
    payload: Mapping[str, object], event_type: str, *, allow_dispatch: bool = True
) -> bool:
    """Called after signature validation, independently of reviewer opt-in/mentions."""
    if event_type not in {
        "pull_request",
        "issue_comment",
        "pull_request_review_comment",
        "pull_request_review",
    }:
        return False
    event = DocsEvent.model_validate(payload)
    number = (
        event.pull_request.number
        if event.pull_request
        else (event.issue.number if event.issue and event.issue.pull_request is not None else None)
    )
    if number is None:
        return False
    config = await settings()
    repository = event.repository.full_name.lower()
    if repository == config.docs_repository:
        if not allow_dispatch:
            return True
        # A docs push/edit changes the evidence for all sources linked to that PR.
        for job in await JOBS.search_all():
            if job.snapshot.settings.docs_repository == repository and any(
                link.number == number for link in job.snapshot.links
            ):
                await coordinate(job.snapshot.source_repository, job.snapshot.source.number)
        return True
    if repository not in config.source_repositories:
        return False
    await coordinate(repository, number, allow_dispatch=allow_dispatch)
    return True


async def coding_pr_opened(repository: str, number: int) -> None:
    """Same coordinator for source PRs opened by the regular coding agent."""
    try:
        config = await settings()
        if config.enabled and repository.lower() in config.source_repositories:
            await coordinate(repository.lower(), number)
    except ConflictError:
        # The webhook already owns this source's dispatch lock.
        logger.info(
            "Docs coordinator already running", extra={"repository": repository, "pr": number}
        )
    except Exception:
        # The coding PR exists even when docs dispatch is temporarily unavailable.
        logger.exception(
            "Docs dispatch failed for coding PR", extra={"repository": repository, "pr": number}
        )


async def invalidate_repository(repository: str) -> None:
    for job in await JOBS.search_all():
        if job.snapshot.source_repository.lower() != repository.lower():
            continue
        async with job_lock(job.snapshot.key):
            current = await JOBS.get(job.snapshot.key)
            if current and current.status in {"pending", "running"}:
                await cancel(current, "Repository review settings changed.")
