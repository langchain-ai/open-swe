"""Durable watch for a merged OpenSWE pull request.

A scheduler cron polls Homebase ``releases.locate_commit`` every 15 minutes.
The implementing thread is resumed only when an environment in the check newly
contains the merge SHA (dev immediately, staging and prod after one quiet
poll). ``rollout_page_check`` opens the page in the sandbox browser and does not log in.
"""

import json
import logging
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.errors import ConflictError
from pydantic import BaseModel, ConfigDict, Field

from agent.dispatch import dispatch_agent_run
from agent.mcp.instance import instance_mcp_source
from agent.mcp.runtime import load_mcp_tools
from agent.mcp.workspace import workspace_mcp_source
from agent.prompts import prompt
from agent.source_context import SourceContext
from agent.store import TypedStore, now_iso
from agent.thread_ids import rollout_lock_thread_id
from agent.threads.creation import create_lock_thread

logger = logging.getLogger(__name__)

WATCH_NAMESPACE = ["rollout_watches"]
WATCH_CRON_KIND = "rollout_watch"
WATCH_SCHEDULE = "*/15 * * * *"
WATCH_LOCK_TTL_MINUTES = 5
MAX_WATCH_AGE = timedelta(days=7)
_LOCATE_TOOL_NAMES = frozenset({"releases_locate_commit", "releases.locate_commit"})
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_ENVS = ("dev", "staging", "prod")
ROLLOUT_OWNER = "langchain-ai"
ROLLOUT_REPO = "langchainplus"


@asynccontextmanager
async def _watch_lock(key: str) -> AsyncIterator[bool]:
    client = get_client()
    lock_id = rollout_lock_thread_id(key)
    try:
        await create_lock_thread(client, lock_id, ttl_minutes=WATCH_LOCK_TTL_MINUTES)
    except ConflictError:
        yield False
        return
    except Exception:
        logger.warning("Failed to acquire rollout lock for %s", key, exc_info=True)
        yield False
        return
    try:
        yield True
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning("Failed to release rollout lock for %s", key, exc_info=True)


class RolloutWatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    active: bool = True
    thread_id: str = ""
    owner: str = ""
    repo: str = ""
    pr_number: int = 0
    pr_url: str = ""
    sha: str = ""
    author: str = ""
    envs: list[str] = Field(default_factory=lambda: ["dev", "staging"])
    page: str = ""
    expected: str = ""
    metrics: str = ""
    resolves_thread: bool = False
    workspace: str = ""
    run_config: dict[str, Any] = Field(default_factory=dict)
    source_context: dict[str, Any] = Field(default_factory=dict)
    seen: list[str] = Field(default_factory=list)
    dispatched: list[str] = Field(default_factory=list)
    check_id: str = ""
    cron_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def dispatch_config(self) -> dict[str, Any]:
        configurable = dict(self.run_config)
        configurable.update(
            {
                "source": configurable.get("source") or "github",
                "repo": {"owner": self.owner, "name": self.repo},
                "pr_number": self.pr_number,
                "rollout_watch_key": self.key,
            }
        )
        return configurable


class RolloutWatchStore(TypedStore[RolloutWatch]):
    def __init__(self) -> None:
        super().__init__(WATCH_NAMESPACE, RolloutWatch)

    async def save(self, watch: RolloutWatch) -> RolloutWatch:
        watch.updated_at = now_iso()
        return await self.put(watch.key, watch)


WATCHES = RolloutWatchStore()


def watch_key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}#{pr_number}"


def rollout_repo_allowed(owner: str, repo: str) -> bool:
    """Rollouts follow langchainplus only until the check is ready for other repos."""
    return owner.strip().lower() == ROLLOUT_OWNER and repo.strip().lower() == ROLLOUT_REPO


def rollout_payload_allowed(payload: Mapping[str, Any]) -> bool:
    identity = _repo_identity(payload)
    if identity is None:
        return False
    owner, repo, _number = identity
    return rollout_repo_allowed(owner, repo)


def rollout_watch_pending(metadata: Mapping[str, Any]) -> bool:
    """True while a stored check still has to run after merge.

    ``done`` only covers the check id written with it. A follow-up check recorded
    while an older watch is still running stays pending even if that watch later
    writes ``done``.
    """
    check = metadata.get("rollout_check")
    if not isinstance(check, dict):
        return False
    if metadata.get("rollout_status") != "done":
        return True
    check_id = check.get("check_id")
    finished_for = metadata.get("rollout_status_check_id")
    return isinstance(check_id, str) and bool(check_id) and finished_for != check_id


def rollout_env(target_id: str, label: str = "") -> str | None:
    """Map a Homebase locate target onto dev, staging, or prod.

    Self-hosted tracks are ignored. Staging is matched before prod so a
    staging id is not treated as production.
    """
    ident = target_id.strip().lower()
    text = f"{ident} {label.strip().lower()}"
    if "self-hosted" in text:
        return None
    if "staging" in text:
        return "staging"
    if "prod" in text:
        return "prod"
    if "dev" in text:
        return "dev"
    return None


def envs_ready(targets: list[Any]) -> set[str]:
    """Environments whose every SaaS target contains the commit and has no error."""
    grouped: dict[str, list[Mapping[str, Any]]] = {env: [] for env in _ENVS}
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        env = rollout_env(str(target.get("id") or ""), str(target.get("label") or ""))
        if env is not None:
            grouped[env].append(target)
    ready: set[str] = set()
    for env, rows in grouped.items():
        if rows and all(not row.get("error") and row.get("contains") is True for row in rows):
            ready.add(env)
    return ready


def _clip(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _normalize_envs(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return ["dev", "staging"]
    envs = [env for env in _ENVS if env in raw]
    return envs or ["dev", "staging"]


def _payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict) and isinstance(raw.get("targets"), list):
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str):
            return _payload(content)
        if isinstance(content, list):
            texts = [
                block.get("text")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                return _payload("\n".join(texts))
    return None


def _expired(watch: RolloutWatch) -> bool:
    try:
        created = datetime.fromisoformat(watch.created_at)
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return datetime.now(UTC) - created >= MAX_WATCH_AGE


def _merge_sha(payload: Mapping[str, Any]) -> str:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, Mapping):
        return ""
    sha = pull_request.get("merge_commit_sha")
    if isinstance(sha, str) and _SHA_RE.fullmatch(sha.strip()):
        return sha.strip().lower()
    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if isinstance(head_sha, str) and _SHA_RE.fullmatch(head_sha.strip()):
        return head_sha.strip().lower()
    return ""


def _repo_identity(payload: Mapping[str, Any]) -> tuple[str, str, int] | None:
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, Mapping) or not isinstance(pull_request, Mapping):
        return None
    full_name = repository.get("full_name")
    number = pull_request.get("number")
    if not isinstance(full_name, str) or "/" not in full_name or not isinstance(number, int):
        return None
    owner, repo = full_name.split("/", 1)
    if not owner or not repo:
        return None
    return owner, repo, number


async def _create_watch_cron(key: str) -> str:
    cron = await get_client().crons.create(
        "scheduler",
        schedule=WATCH_SCHEDULE,
        input={"task": "rollout", "watch_key": key},
        config={"configurable": {"task": "rollout", "watch_key": key}},
        metadata={"kind": WATCH_CRON_KIND, "watch_key": key},
        timezone="UTC",
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError("rollout cron creation did not return a cron_id")
    return cron_id


async def _ensure_watch_cron(key: str) -> str:
    crons = await get_client().crons.search(
        metadata={"kind": WATCH_CRON_KIND, "watch_key": key},
        limit=10,
    )
    cron_ids = [
        cron_id
        for cron in crons or []
        if isinstance(cron, dict) and isinstance((cron_id := cron.get("cron_id")), str) and cron_id
    ]
    if cron_ids:
        for duplicate in cron_ids[1:]:
            try:
                await get_client().crons.delete(duplicate)
            except Exception:
                logger.warning("Failed to delete duplicate rollout cron %s", duplicate)
        return cron_ids[0]
    return await _create_watch_cron(key)


async def _mark_thread(thread_id: str, metadata: dict[str, Any]) -> None:
    try:
        await get_client().threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:
        logger.warning("Failed to update rollout status for %s", thread_id, exc_info=True)


def _status_metadata(status: str, check_id: str) -> dict[str, Any]:
    metadata = {"rollout_status": status}
    if check_id:
        metadata["rollout_status_check_id"] = check_id
    return metadata


def _stored_check_id(thread: Any) -> str:
    metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    check = metadata.get("rollout_check") if isinstance(metadata, Mapping) else None
    check_id = check.get("check_id") if isinstance(check, Mapping) else None
    return check_id if isinstance(check_id, str) else ""


async def _superseded(watch: RolloutWatch) -> bool:
    """True when the thread has recorded a newer check than this watch."""
    if not watch.check_id:
        return False
    try:
        thread = await get_client().threads.get(watch.thread_id)
    except Exception:
        logger.warning("Failed to read rollout check for %s", watch.thread_id, exc_info=True)
        return False
    current = _stored_check_id(thread)
    return bool(current) and current != watch.check_id


async def _retire(watch: RolloutWatch) -> None:
    watch.active = False
    await _stop_cron(watch)
    await WATCHES.save(watch)


async def start_watch(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    sha: str,
    author: str,
    envs: list[str],
    page: str,
    expected: str,
    metrics: str,
    resolves_thread: bool,
    run_config: dict[str, Any],
    source_context: dict[str, Any],
    check_id: str = "",
) -> RolloutWatch:
    key = watch_key(owner, repo, pr_number)
    existing = await WATCHES.get(key)
    if existing and existing.active and existing.thread_id not in {"", thread_id}:
        raise ValueError("This pull request is already watched from another agent thread")
    if existing and existing.active and existing.thread_id == thread_id and existing.sha == sha:
        if check_id:
            existing.check_id = check_id
        existing.cron_id = await _ensure_watch_cron(key)
        saved = await WATCHES.save(existing)
        await _mark_thread(thread_id, _status_metadata("watching", saved.check_id))
        return saved

    workspace = ""
    for field in ("workspace", "environment"):
        value = run_config.get(field)
        if isinstance(value, str) and value.strip():
            workspace = value.strip()
            break
    now = now_iso()
    watch = RolloutWatch(
        key=key,
        active=True,
        thread_id=thread_id,
        owner=owner.lower(),
        repo=repo.lower(),
        pr_number=pr_number,
        pr_url=f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        sha=sha,
        author=author,
        envs=_normalize_envs(envs),
        page=page,
        expected=expected,
        metrics=metrics,
        resolves_thread=resolves_thread,
        workspace=workspace,
        run_config=run_config,
        source_context=source_context,
        check_id=check_id,
        cron_id=existing.cron_id if existing else None,
        created_at=now,
        updated_at=now,
    )
    watch = await WATCHES.save(watch)
    try:
        watch.cron_id = await _ensure_watch_cron(key)
        saved = await WATCHES.save(watch)
    except Exception:
        if existing is None:
            if watch.cron_id:
                try:
                    await get_client().crons.delete(watch.cron_id)
                except Exception:
                    logger.warning("Failed to roll back rollout cron %s", watch.cron_id)
            await WATCHES.delete(key)
        raise
    await _mark_thread(thread_id, _status_metadata("watching", saved.check_id))
    return saved


async def start_from_merge(
    thread_id: str, metadata: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    """Start the watch when a linked agent thread's PR merges with a stored check."""
    if not rollout_watch_pending(metadata) or metadata.get("kind") == "reviewer":
        return
    identity = _repo_identity(payload)
    sha = _merge_sha(payload)
    if identity is None or not sha:
        logger.warning("Rollout watch skipped; merge payload has no repo or SHA")
        return
    check = metadata.get("rollout_check")
    if not isinstance(check, dict):
        return
    owner, repo, number = identity
    if not rollout_repo_allowed(owner, repo):
        return
    pull_requests = metadata.get("pull_requests")
    resolves_thread = isinstance(pull_requests, list) and any(
        isinstance(record, dict) and record.get("resolves_thread") is True
        for record in pull_requests
    )
    pull_request = payload.get("pull_request")
    user = pull_request.get("user") if isinstance(pull_request, Mapping) else None
    author = user.get("login") if isinstance(user, Mapping) else None
    if not isinstance(author, str) or not author.strip():
        author = _clip(check.get("author"), 100)
    run_config = check.get("run_config")
    source_context = check.get("source_context")
    await start_watch(
        thread_id=thread_id,
        owner=owner,
        repo=repo,
        pr_number=number,
        sha=sha,
        author=author.strip(),
        envs=_normalize_envs(check.get("envs")),
        page=_clip(check.get("page"), 300),
        expected=_clip(check.get("expected"), 1000),
        metrics=_clip(check.get("metrics"), 1000),
        resolves_thread=resolves_thread,
        run_config=run_config if isinstance(run_config, dict) else {},
        source_context=source_context if isinstance(source_context, dict) else {},
        check_id=_clip(check.get("check_id"), 80),
    )


async def _stop_cron(watch: RolloutWatch) -> None:
    if not watch.cron_id:
        return
    try:
        await get_client().crons.delete(watch.cron_id)
    except Exception:
        logger.warning("Failed to delete rollout cron %s", watch.cron_id, exc_info=True)
    watch.cron_id = None


async def _finish(watch: RolloutWatch) -> None:
    superseded = await _superseded(watch)
    await _retire(watch)
    if superseded:
        return
    metadata = _status_metadata("done", watch.check_id)
    if watch.resolves_thread:
        metadata["resolved"] = True
        metadata["resolved_at_ms"] = int(datetime.now(UTC).timestamp() * 1000)
        metadata["auto_resolved_by_prs"] = True
    await _mark_thread(watch.thread_id, metadata)


async def _dispatch(watch: RolloutWatch, content: str) -> bool:
    try:
        await dispatch_agent_run(
            watch.thread_id,
            content,
            watch.dispatch_config(),
            source=str(watch.run_config.get("source") or "github"),
            thread_title=None,
            metadata={},
            multitask_strategy="enqueue",
            source_context=SourceContext.parse(watch.source_context),
        )
    except Exception:
        logger.warning("Failed to dispatch rollout update for %s", watch.key, exc_info=True)
        return False
    return True


def _check_prompt(watch: RolloutWatch, env: str, *, last: bool) -> str:
    return prompt(
        "runs/rollout-check",
        env=env,
        sha=watch.sha,
        pr_url=watch.pr_url,
        author=watch.author,
        page=watch.page,
        expected=watch.expected,
        metrics=watch.metrics,
        last=last,
    )


async def locate_commit(workspace: str, sha: str) -> dict[str, Any] | None:
    """Call the workspace Homebase locate tool. Returns None when it is unavailable."""
    if not workspace:
        return None
    try:
        tools = await load_mcp_tools(instance_mcp_source(), workspace_mcp_source(workspace))
    except Exception:
        logger.warning("Rollout locate tools failed to load", exc_info=True)
        return None
    tool = next(
        (
            item
            for item in tools
            if str((getattr(item, "metadata", None) or {}).get("mcp_tool_name") or "").replace(
                ".", "_"
            )
            in _LOCATE_TOOL_NAMES
            or str((getattr(item, "metadata", None) or {}).get("mcp_tool_name") or "")
            in _LOCATE_TOOL_NAMES
        ),
        None,
    )
    if tool is None:
        logger.info("releases.locate_commit is not on the workspace MCP")
        return None
    try:
        raw = await tool.ainvoke({"commit": sha})
    except Exception:
        logger.warning("releases.locate_commit failed", exc_info=True)
        return None
    return _payload(raw)


async def evaluate_rollout(key: str) -> str:
    """One cron tick. Dispatches the thread only when an environment newly qualifies."""
    async with _watch_lock(key) as acquired:
        if not acquired:
            return "locked"
        watch = await WATCHES.get(key)
        if watch is None or not watch.active:
            return "inactive"
        if await _superseded(watch):
            await _retire(watch)
            return "superseded"
        if _expired(watch):
            waiting = ", ".join(env for env in watch.envs if env not in watch.dispatched) or "none"
            sent = await _dispatch(
                watch,
                prompt(
                    "runs/rollout-expired",
                    pr_url=watch.pr_url,
                    sha=watch.sha,
                    waiting=waiting,
                ),
            )
            if not sent:
                return "dispatch_failed"
            await _finish(watch)
            return "expired"
        if not watch.workspace:
            return "missing_workspace"
        report = await locate_commit(watch.workspace, watch.sha)
        if report is None:
            return "locate_unavailable"
        raw_targets = report.get("targets")
        targets: list[Any] = raw_targets if isinstance(raw_targets, list) else []
        ready = envs_ready(targets)
        changed = False
        for env in watch.envs:
            if env in watch.dispatched or env not in ready:
                continue
            if env != "dev" and env not in watch.seen:
                watch.seen.append(env)
                changed = True
                continue
            last = set(watch.dispatched) | {env} >= set(watch.envs)
            content = (
                prompt("runs/rollout-dev", sha=watch.sha, pr_url=watch.pr_url)
                if env == "dev"
                else _check_prompt(watch, env, last=last)
            )
            if not await _dispatch(watch, content):
                await WATCHES.save(watch)
                return "dispatch_failed"
            watch.dispatched.append(env)
            changed = True
        if set(watch.envs) <= set(watch.dispatched):
            await _finish(watch)
            return "done"
        if changed:
            await WATCHES.save(watch)
        return "waiting"
