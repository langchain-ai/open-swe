"""Durable, opt-in PR CI watches for the `/baby-sit` skill."""

import hashlib
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Row, Select, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from openswe import event_claims
from openswe.database import postgres
from openswe.dispatch import dispatch_agent_run
from openswe.github.app import get_github_app_installation_token
from openswe.github.ci import (
    FAILING_CONCLUSIONS,
    CommitChecks,
    RequiredCheck,
    branch_from_check_payload,
    head_sha_from_check_payload,
    is_completed_ci_payload,
)
from openswe.github.comments import post_github_comment
from openswe.github.http import GitHubAppUnavailable, GitHubClient, read_result
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repositories import Repository
from openswe.human_review.requests import HumanReviewRequest
from openswe.jobs import queue
from openswe.prompts import prompt
from openswe.slack.client import GitHubPrRef, post_slack_thread_reply
from openswe.source_context import SourceContext
from openswe.tasks.store import Task, TaskMembership
from openswe.utils.json_types import JsonObject
from openswe.utils.time import now_iso
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)

_STATE_KEY = "baby_sit"
WATCH_CRON_KIND = "baby_sit_watch"
WATCH_JOB_KIND = "baby_sit"
WATCH_SCHEDULE = "*/10 * * * *"
MAX_RETRIES_PER_HEAD = 3
MAX_DISPATCH_KEYS = 30
MAX_DELIVERY_IDS = 50
MAX_ALERT_KEYS = 30
MAX_EVALUATION_ERRORS = 3
WATCH_LOCK_TTL = timedelta(minutes=5)
_WATCH_LOCK_SCOPE = "baby_sit_watch"


@asynccontextmanager
async def _watch_lock(key: str) -> AsyncIterator[bool]:
    try:
        acquired = await event_claims.claim(_WATCH_LOCK_SCOPE, key, ttl=WATCH_LOCK_TTL)
    except Exception:
        logger.warning("Failed to acquire baby-sit lock for %s", key, exc_info=True)
        acquired = False
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            await event_claims.release(_WATCH_LOCK_SCOPE, key)
        except Exception:
            logger.warning("Failed to release baby-sit lock for %s", key, exc_info=True)


class BabySitState(BaseModel):
    """A watch's working state, kept on its pull request at ``task_state["baby_sit"]``."""

    model_config = ConfigDict(extra="ignore")

    active: bool = True
    head_sha: str = ""
    head_ref: str = ""
    ready_head_sha: str = ""
    ready_dispatch_errors: int = 0
    installation_id: int | None = None
    run_config: dict[str, Any] = Field(default_factory=dict)
    source_context: SourceContext = Field(default_factory=SourceContext)
    retry_count: int = 0
    dispatch_keys: list[str] = Field(default_factory=list)
    delivery_ids: list[str] = Field(default_factory=list)
    alert_keys: list[str] = Field(default_factory=list)
    evaluation_errors: int = 0
    cron_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


class BabySitWatch(BabySitState):
    key: str
    task_id: UUID | None = None
    # The thread the watch wakes: the starting thread until it joins a task,
    # then that task's coordinator.
    thread_id: str = ""
    owner: str = ""
    repo: str = ""
    pr_number: int = 0
    pr_url: str = ""

    def state(self) -> JsonObject:
        return self.model_dump(mode="json", include=set(BabySitState.model_fields))

    async def join_task(self, *, title: str) -> None:
        """Join the starting thread's task, making it a new task's coordinator when it has none."""
        workspace = self.run_config.get("workspace") or self.run_config.get("environment")
        resolution = await resolve_workspace(
            thread_workspace=workspace if isinstance(workspace, str) else None,
            repo=(self.owner, self.repo),
        )
        task = await Task.for_thread(self.thread_id, title=title, workspace=resolution.slug)
        self.task_id = task.id
        self.thread_id = task.require_coordinator()

    async def belongs_to(self, thread_id: str) -> bool:
        """Whether ``thread_id`` is part of the task this watch works for."""
        context = await TaskMembership.context_for_thread(thread_id)
        return context is not None and context.task.id == self.task_id

    def dispatch_config(self) -> dict[str, Any]:
        configurable = dict(self.run_config)
        configurable.update(
            {
                "source": configurable.get("source") or "github",
                "thread_id": self.thread_id,
                "repo": {"owner": self.owner, "name": self.repo},
                "pr_number": self.pr_number,
                "baby_sit_watch_key": self.key,
            }
        )
        return configurable

    def failure_prompt(self, failures: list[dict[str, Any]], *, expedited: bool) -> str:
        lines = []
        for failure in failures:
            name = _prompt_scalar(failure.get("name") or "check", 200)
            conclusion = _prompt_scalar(failure.get("conclusion") or "failure", 50)
            url = _prompt_scalar(failure.get("url") or "", 500)
            lines.append(f"- {name} ({conclusion})" + (f" — {url}" if url else ""))
        return prompt(
            "runs/baby-sit-failure",
            pr_url=self.pr_url,
            head_sha=self.head_sha,
            retry_count=self.retry_count,
            max_retries=MAX_RETRIES_PER_HEAD,
            signals="\n".join(lines),
            expedited=expedited,
        )


class BabySitWatches:
    """Watches, each kept on its pull request beside the task it works for."""

    async def get(self, key: str) -> BabySitWatch | None:
        full_name, _, number = key.rpartition("#")
        async with postgres.session() as session:
            row = (
                await session.execute(
                    self._select().where(
                        Repository.key == full_name, PullRequest.number == int(number)
                    )
                )
            ).first()
        return None if row is None else self._watch(row)

    async def list_active(
        self, *, owner: str | None = None, repo: str | None = None
    ) -> list[BabySitWatch]:
        query = self._select().where(
            PullRequest.task_state[_STATE_KEY]["active"].as_boolean().is_(True)
        )
        if owner and repo:
            query = query.where(Repository.key == f"{owner}/{repo}".strip().lower())
        async with postgres.session() as session:
            rows = (await session.execute(query.order_by(Repository.key, PullRequest.number))).all()
        return [self._watch(row) for row in rows]

    async def save(self, watch: BabySitWatch) -> BabySitWatch:
        watch.updated_at = now_iso()
        merged = func.coalesce(PullRequest.task_state, literal({}, JSONB)).op("||")(
            literal({_STATE_KEY: watch.state()}, JSONB)
        )
        values = {"task_state": merged}
        if watch.task_id is not None:
            values["task_id"] = watch.task_id
        if not await self._update(watch.key, values):
            await PullRequest(owner=watch.owner, repo=watch.repo, number=watch.pr_number).ensure()
            if not await self._update(watch.key, values):
                raise RuntimeError(f"pull request {watch.pr_url} vanished while saving its watch")
        return watch

    async def delete(self, key: str) -> None:
        remaining = PullRequest.task_state.op("-")(_STATE_KEY)
        await self._update(key, {"task_state": func.nullif(remaining, literal({}, JSONB))})

    @staticmethod
    def _select() -> Select[str, str, int, UUID | None, JsonObject | None, str | None]:
        return (
            select(
                PullRequest.owner,
                PullRequest.repo,
                PullRequest.number,
                PullRequest.task_id,
                PullRequest.task_state,
                Task.coordinator_thread_id,
            )
            .join(Repository, Repository.id == PullRequest.repository_id)
            .outerjoin(Task, Task.id == PullRequest.task_id)
            .where(PullRequest.task_state.has_key(_STATE_KEY))
        )

    @staticmethod
    def _watch(
        row: Row[str, str, int, UUID | None, JsonObject | None, str | None],
    ) -> BabySitWatch:
        owner, repo, number, task_id, task_state, coordinator = row.tuple()
        return BabySitWatch.model_validate(
            {
                **(task_state or {}).get(_STATE_KEY, {}),
                "key": watch_key(owner, repo, number),
                "task_id": task_id,
                "thread_id": coordinator or "",
                "owner": owner.lower(),
                "repo": repo.lower(),
                "pr_number": number,
                "pr_url": f"https://github.com/{owner}/{repo}/pull/{number}",
            }
        )

    @staticmethod
    async def _update(key: str, values: Mapping[str, object]) -> bool:
        full_name, _, number = key.rpartition("#")
        repository_id = select(Repository.id).where(Repository.key == full_name).scalar_subquery()
        async with postgres.session() as session:
            updated = await session.scalar(
                update(PullRequest)
                .where(
                    PullRequest.repository_id == repository_id, PullRequest.number == int(number)
                )
                .values(**values)
                .returning(PullRequest.id)
            )
        return updated is not None


WATCHES = BabySitWatches()


def watch_key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}#{pr_number}"


def _watch_job_key(key: str) -> str:
    return f"{WATCH_JOB_KIND}:{key}"


async def _ensure_watch_job(key: str) -> str:
    job = await queue.schedule(
        WATCH_JOB_KIND,
        {"watch_key": key},
        cron=WATCH_SCHEDULE,
        key=_watch_job_key(key),
        metadata={"kind": WATCH_CRON_KIND, "watch_key": key},
    )
    return str(job.id)


async def adopt_agent_server_cron(key: str) -> bool:
    """Move an active watch's Agent Server cron onto a job; ``False`` once it has stopped."""
    watch = await WATCHES.get(key)
    if watch is None or not watch.active:
        return False
    watch.cron_id = await _ensure_watch_job(key)
    await WATCHES.save(watch)
    return True


async def start_watch(
    *,
    pr_ref: GitHubPrRef,
    title: str,
    head_sha: str,
    head_ref: str,
    installation_id: int | None,
    thread_id: str,
    run_config: dict[str, Any],
    source_context: SourceContext,
) -> BabySitWatch:
    key = watch_key(pr_ref.owner, pr_ref.repo, pr_ref.number)
    existing = await WATCHES.get(key)
    if existing and existing.active and not await existing.belongs_to(thread_id):
        raise ValueError("This pull request is already monitored by another task")

    now = now_iso()
    carried = existing if existing is not None and existing.head_sha == head_sha else None
    watch = BabySitWatch(
        key=key,
        active=True,
        thread_id=thread_id,
        owner=pr_ref.owner.lower(),
        repo=pr_ref.repo.lower(),
        pr_number=pr_ref.number,
        pr_url=pr_ref.url,
        head_sha=head_sha,
        head_ref=head_ref,
        installation_id=installation_id,
        run_config=run_config,
        source_context=source_context,
        ready_head_sha=carried.ready_head_sha if carried else "",
        ready_dispatch_errors=carried.ready_dispatch_errors if carried else 0,
        retry_count=carried.retry_count if carried else 0,
        dispatch_keys=list(carried.dispatch_keys) if carried else [],
        delivery_ids=list(carried.delivery_ids) if carried else [],
        alert_keys=list(carried.alert_keys) if carried else [],
        evaluation_errors=0,
        cron_id=existing.cron_id if existing else None,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    await watch.join_task(title=title)
    watch = await WATCHES.save(watch)
    try:
        watch.cron_id = await _ensure_watch_job(key)
        return await WATCHES.save(watch)
    except Exception:
        if existing is None:
            try:
                await queue.cancel_key(_watch_job_key(key))
            except Exception:
                logger.warning("Failed to roll back baby-sit job %s", key, exc_info=True)
            await WATCHES.delete(key)
        raise


async def stop_watch(key: str) -> bool:
    watch = await WATCHES.get(key)
    if not watch:
        return False
    try:
        await queue.cancel_key(_watch_job_key(key))
    except Exception:
        logger.warning("Failed to delete baby-sit job %s", key, exc_info=True)
        watch.active = False
        await WATCHES.save(watch)
        return True
    await WATCHES.delete(key)
    return True


async def _notify_watch(watch: BabySitWatch, message: str) -> bool:
    """Post ``message`` where the watch was started, unless an expedited card owns that thread.

    An expedited review's thread only ever carries its card; returning ``False``
    hands terminal outcomes to the agent instead.
    """
    if await _review_card(watch) is not None:
        logger.info(
            "Suppressed baby-sit notice for an expedited review", extra={"watch_key": watch.key}
        )
        return False
    context = watch.source_context
    try:
        destination = context.slack_location
        if destination is not None:
            return await post_slack_thread_reply(destination[0], destination[1], message)
        issue_number = context.github_issue.number if context.github_issue else None
        if issue_number is None:
            configured_number = watch.run_config.get("pr_number")
            issue_number = configured_number if isinstance(configured_number, int) else None
        source_repo = watch.run_config.get("source_repo")
        repo = (
            source_repo
            if isinstance(source_repo, dict)
            else {
                "owner": watch.owner,
                "name": watch.repo,
            }
        )
        source_installation_id = watch.run_config.get("source_installation_id")
        token = await get_github_app_installation_token(
            installation_id=(
                source_installation_id
                if isinstance(source_installation_id, int)
                else watch.installation_id
            )
        )
        if issue_number is not None and token:
            return await post_github_comment(repo, issue_number, message, token=token)
    except Exception:
        logger.warning("Failed to notify source for %s", watch.key, exc_info=True)
        return False
    return False


async def _finish_watch(watch: BabySitWatch, message: str) -> str:
    # The agent owning a Slack thread is the only thing that posts in it.
    notified = watch.source_context.slack_location is None and await _notify_watch(watch, message)
    if not notified:
        try:
            configurable = watch.dispatch_config()
            await dispatch_agent_run(
                watch.thread_id,
                f"/baby-sit --terminal {watch.pr_url}\n\n{message}",
                configurable,
                source=str(configurable.get("source") or "dashboard"),
                thread_title=None,
                metadata={},
                multitask_strategy="enqueue",
            )
        except Exception:
            logger.warning("Failed to dispatch terminal baby-sit update for %s", watch.key)
    await stop_watch(watch.key)
    return "stopped"


async def _review_card(watch: BabySitWatch) -> HumanReviewRequest | None:
    if not postgres.configured():
        return None
    try:
        approval = await HumanReviewRequest.active_for(watch.owner, watch.repo, watch.pr_number)
    except Exception:
        logger.warning("Expedited review lookup failed for %s", watch.key, exc_info=True)
        return None
    return (
        approval if approval is not None and approval.thread_id in {"", watch.thread_id} else None
    )


async def _finish_ready(watch: BabySitWatch) -> str:
    """Report each green head once while continuing to watch future commits."""
    approval = await _review_card(watch)
    if (approval is not None and approval.awaiting_ready) or watch.ready_head_sha == watch.head_sha:
        await WATCHES.save(watch)
        return "watching"
    try:
        configurable = watch.dispatch_config()
        await dispatch_agent_run(
            watch.thread_id,
            prompt(
                "runs/baby-sit-ready",
                pr_url=watch.pr_url,
                head_sha=watch.head_sha,
                expedited=approval is not None,
            ),
            configurable,
            source=str(configurable.get("source") or "github"),
            thread_title=None,
            metadata={},
            multitask_strategy="enqueue",
        )
    except Exception:
        logger.warning("Failed to dispatch baby-sit ready wakeup for %s", watch.key, exc_info=True)
        watch.ready_dispatch_errors += 1
        await WATCHES.save(watch)
        if watch.ready_dispatch_errors >= MAX_EVALUATION_ERRORS:
            return await _finish_watch(
                watch,
                f"*`/baby-sit` stopped:* {watch.pr_url} finished CI without a failure, but "
                f"Open SWE could not resume its agent after {MAX_EVALUATION_ERRORS} attempts. "
                "Check the agent service and restart the CI watch; nothing was merged.",
            )
        return "error"
    watch.ready_dispatch_errors = 0
    watch.ready_head_sha = watch.head_sha
    await WATCHES.save(watch)
    return "watching"


def _failure_signals(
    check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    failures = [
        {
            "name": run.get("name") or "check",
            "conclusion": run.get("conclusion") or "failure",
            "url": run.get("details_url") or run.get("html_url") or "",
        }
        for run in check_runs
        if run.get("status") == "completed" and run.get("conclusion") in FAILING_CONCLUSIONS
    ]
    failures.extend(
        {
            "name": status.get("context") or "status",
            "conclusion": status.get("state") or "failure",
            "url": status.get("target_url") or "",
        }
        for status in statuses
        if status.get("state") in {"failure", "error"}
    )
    return failures


def _failure_key(head_sha: str, retry_count: int) -> str:
    return hashlib.sha256(f"{head_sha}|retry:{retry_count}".encode()).hexdigest()


def aggregate_check_state(
    check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """``(pending | success | blocked | failure, failing signals)`` for one check set."""
    failures = _failure_signals(check_runs, statuses)
    if failures:
        return "failure", failures
    if any(run.get("status") != "completed" for run in check_runs) or any(
        status.get("state") == "pending" for status in statuses
    ):
        return "pending", []
    if not check_runs and not statuses:
        return "pending", []
    blocked_checks = [
        run for run in check_runs if run.get("conclusion") not in {"success", "neutral", "skipped"}
    ]
    blocked_statuses = [status for status in statuses if status.get("state") not in {"success"}]
    if blocked_checks or blocked_statuses:
        return "blocked", []
    return "success", []


def _prompt_scalar(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


async def _record_evaluation_error(
    watch: BabySitWatch, detail: str, *, retryable: bool = False
) -> str:
    if retryable:
        await WATCHES.save(watch)
        return "error"
    errors = watch.evaluation_errors + 1
    watch.evaluation_errors = errors
    await WATCHES.save(watch)
    if errors < MAX_EVALUATION_ERRORS:
        return "error"
    return await _finish_watch(
        watch,
        f"*`/baby-sit` stopped:* {watch.pr_url} could not be evaluated after "
        f"{MAX_EVALUATION_ERRORS} attempts ({detail}).",
    )


async def evaluate_watch(key: str) -> str:
    async with _watch_lock(key) as acquired:
        if not acquired:
            return "busy"
        return await _evaluate_watch(key)


async def _evaluate_watch(key: str) -> str:
    watch = await WATCHES.get(key)
    if not watch:
        return "missing"
    if not watch.active:
        await stop_watch(key)
        return "stopped"
    if watch.installation_id is None:
        return await _record_evaluation_error(watch, "GitHub token unavailable")
    try:
        async with GitHubClient.as_app(installation_id=watch.installation_id) as github:
            return await _evaluate_pull(
                watch, github.repo(watch.owner, watch.repo).pull_request(watch.pr_number)
            )
    except GitHubAppUnavailable as exc:
        return await _record_evaluation_error(watch, str(exc), retryable=exc.retryable)


async def _evaluate_pull(watch: BabySitWatch, pull: PullRequestClient) -> str:
    key = watch.key
    result = await read_result(pull.pull())
    pr = result.value
    if not pr:
        detail = {
            "missing": "pull request not found or hidden from the GitHub App (404)",
            "forbidden": "GitHub denied access to the pull request (401/403)",
        }.get(result.status, "pull request unavailable")
        return await _record_evaluation_error(watch, detail, retryable=result.status == "transient")
    # A merged or closed PR already says so where people look; the watch just ends.
    if pr.get("state") != "open":
        await stop_watch(key)
        return "stopped"

    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or not head_sha:
        return await _record_evaluation_error(watch, "head SHA unavailable")
    head_ref = head.get("ref") if isinstance(head, dict) else None
    if isinstance(head_ref, str) and head_ref:
        watch.head_ref = head_ref
    if head_sha != watch.head_sha:
        watch.head_sha = head_sha
        watch.ready_head_sha = ""
        watch.ready_dispatch_errors = 0
        watch.retry_count = 0
        watch.dispatch_keys = []
        watch.alert_keys = []

    check_result = await CommitChecks.read_result(pull.repo, head_sha)
    checks = check_result.value
    if checks is None:
        return await _record_evaluation_error(
            watch,
            f"CI status unavailable ({check_result.status})",
            retryable=check_result.status == "transient",
        )

    watch.evaluation_errors = 0
    state, failures = aggregate_check_state(checks.runs, checks.statuses)
    if state == "pending":
        await WATCHES.save(watch)
        return state
    if state == "success":
        base_ref = PullRequestPayload.model_validate(pr).base_ref
        if not base_ref:
            return await _record_evaluation_error(watch, "base branch unavailable")
        required = await RequiredCheck.for_branch(pull.repo, base_ref)
        if required is None:
            return await _record_evaluation_error(watch, "required checks unavailable")
        if checks.unreported(required):
            await WATCHES.save(watch)
            return "pending"
        return await _finish_ready(watch)
    if state == "blocked":
        return await _finish_watch(
            watch,
            f"*`/baby-sit` needs owner triage:* {watch.pr_url} has terminal checks that "
            "are neither successful nor rerunnable failures.",
        )
    if watch.retry_count >= MAX_RETRIES_PER_HEAD:
        return await _finish_watch(
            watch,
            f"*`/baby-sit` stopped:* {watch.pr_url} is still failing after "
            f"{MAX_RETRIES_PER_HEAD} flaky reruns for `{head_sha[:12]}`.",
        )

    fingerprint = _failure_key(head_sha, watch.retry_count)
    dispatch_keys = list(watch.dispatch_keys)
    if fingerprint in dispatch_keys:
        await WATCHES.save(watch)
        return "duplicate"
    watch.dispatch_keys = [*dispatch_keys, fingerprint][-MAX_DISPATCH_KEYS:]
    watch = await WATCHES.save(watch)
    try:
        configurable = watch.dispatch_config()
        await dispatch_agent_run(
            watch.thread_id,
            watch.failure_prompt(failures, expedited=await _review_card(watch) is not None),
            configurable,
            source=str(configurable.get("source") or "github"),
            thread_title=None,
            metadata={},
            multitask_strategy="enqueue",
        )
    except Exception:
        latest = await WATCHES.get(key) or watch
        latest.dispatch_keys = [item for item in latest.dispatch_keys if item != fingerprint]
        await WATCHES.save(latest)
        logger.warning("Failed to dispatch baby-sit failure for %s", key, exc_info=True)
        return "error"
    return "dispatched"


async def handle_ci_webhook(
    payload: dict[str, Any], event_type: str, *, delivery_id: str | None = None
) -> dict[str, int]:
    if not is_completed_ci_payload(payload, event_type):
        return {"matched": 0, "dispatched": 0}
    repository = payload.get("repository")
    owner_node = repository.get("owner") if isinstance(repository, dict) else None
    owner = owner_node.get("login") if isinstance(owner_node, dict) else None
    repo = repository.get("name") if isinstance(repository, dict) else None
    head_sha = head_sha_from_check_payload(payload, event_type)
    if (
        not isinstance(owner, str)
        or not owner
        or not isinstance(repo, str)
        or not repo
        or not head_sha
    ):
        return {"matched": 0, "dispatched": 0}

    branch = branch_from_check_payload(payload, event_type)
    repo_watches = await WATCHES.list_active(owner=owner, repo=repo)
    watches = [
        watch
        for watch in repo_watches
        if watch.head_sha == head_sha or (branch and watch.head_ref == branch)
    ]
    if not watches:
        return {"matched": 0, "dispatched": 0}
    installation = payload.get("installation")
    installation_id = installation.get("id") if isinstance(installation, dict) else None
    dispatched = 0
    for watch in watches:
        async with _watch_lock(watch.key) as acquired:
            if not acquired:
                continue
            current = await WATCHES.get(watch.key)
            if not current or not current.active:
                continue
            if isinstance(installation_id, int) and installation_id != current.installation_id:
                current.installation_id = installation_id
            if delivery_id:
                delivery_ids = list(current.delivery_ids)
                if delivery_id in delivery_ids:
                    continue
                current.delivery_ids = [*delivery_ids, delivery_id][-MAX_DELIVERY_IDS:]
            await WATCHES.save(current)
            if await _evaluate_watch(current.key) == "dispatched":
                dispatched += 1
    return {"matched": len(watches), "dispatched": dispatched}


def _escape_slack(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")


def _safe_check_link(value: str) -> str:
    return value if value.startswith("https://github.com/") else ""


async def record_retry(
    key: str,
    *,
    thread_id: str,
    head_sha: str,
    check_name: str,
    evidence: str,
    details_url: str = "",
) -> dict[str, Any]:
    async with _watch_lock(key) as acquired:
        if not acquired:
            return {"success": False, "error": "A baby-sit update is already in progress"}
        return await _record_retry(
            key,
            thread_id=thread_id,
            head_sha=head_sha,
            check_name=check_name,
            evidence=evidence,
            details_url=details_url,
        )


async def _record_retry(
    key: str,
    *,
    thread_id: str,
    head_sha: str,
    check_name: str,
    evidence: str,
    details_url: str = "",
) -> dict[str, Any]:
    watch = await WATCHES.get(key)
    if not watch or not watch.active:
        return {"success": False, "error": "No active baby-sit watch for this pull request"}
    if not await watch.belongs_to(thread_id):
        return {"success": False, "error": "This watch belongs to another task"}
    if watch.head_sha != head_sha:
        return {
            "success": False,
            "error": "Pull request head changed before the rerun was recorded",
        }
    retries = watch.retry_count
    if retries >= MAX_RETRIES_PER_HEAD:
        return {"success": False, "error": "Flaky rerun limit reached"}

    retries += 1
    clean_name = check_name.strip()[:200] or "CI check"
    clean_evidence = " ".join(evidence.strip().split())[:500] or "transient failure evidence"
    safe_url = _safe_check_link(details_url.strip())
    alert_key = hashlib.sha256(f"{watch.head_sha}|{clean_name}|{safe_url}".encode()).hexdigest()
    alert_keys = list(watch.alert_keys)
    first_alert = alert_key not in alert_keys
    watch.retry_count = retries
    if first_alert:
        watch.alert_keys = [*alert_keys, alert_key][-MAX_ALERT_KEYS:]
    await WATCHES.save(watch)

    if first_alert:
        check_text = f"<{safe_url}|check details>" if safe_url else "check details unavailable"
        await _notify_watch(
            watch,
            f"*Flaky CI detected:* `{_escape_slack(clean_name)}`\n"
            f"• PR: <{watch.pr_url}|{watch.owner}/{watch.repo}#{watch.pr_number}>\n"
            f"• Evidence: {_escape_slack(clean_evidence)}\n"
            f"• Rerun: {retries}/{MAX_RETRIES_PER_HEAD}\n"
            f"• {check_text}",
        )
    return {"success": True, "retry_count": retries, "alerted": first_alert}
