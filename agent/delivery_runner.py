"""Launch queued delivery work into agent worker runs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from . import project_registry
from .delivery_preflight import block_delivery_start, evaluate_delivery_start_preflight
from .delivery_queue import read_delivery_queue_item, transition_delivery_queue_status
from .dispatch import dispatch_agent_run
from .utils.thread_ops import langgraph_client

DELIVERY_RUN_SOURCE = "delivery_queue"
ACTIVE_RUN_STATUSES = ("pending", "running")

WORKER_CONTRACT = (
    "Take a ticket, bug report, failing behavior, or customer complaint and turn it into a "
    "review-ready patch. Reproduce the failure in the smallest representative environment, prove "
    "the root cause, make the smallest credible fix, and rerun the original reproduction plus "
    "relevant regression tests. If the issue cannot be reproduced after two serious attempts, say "
    "so. Do not fold unrelated refactors into the patch. Finish with the cause, changed files, "
    "before-and-after proof, risks, and pull-request summary."
)


def _client() -> Any:
    return langgraph_client()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _first_text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _mapping_value(record: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = record.get(key)
    return dict(value) if isinstance(value, Mapping) else None


def _list_value(record: Mapping[str, Any], key: str) -> list[Any]:
    value = record.get(key)
    return list(value) if isinstance(value, list) else []


def _repo_from_item(item: Mapping[str, Any]) -> dict[str, str] | None:
    repo = item.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}
    owner = _first_text(item, "repo_owner", "owner")
    name = _first_text(item, "repo_name", "repo")
    if owner and name:
        return {"owner": owner, "name": name}
    return None


def _worker_thread_id_for(item: Mapping[str, Any]) -> str:
    existing = _first_text(item, "worker_thread_id", "delivery_worker_thread_id")
    if existing:
        return existing
    item_id = _first_text(item, "id", "dedupe_key")
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
    return f"delivery-worker-{digest}"


def _run_id(run: Any) -> str | None:
    value = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    return value if isinstance(value, str) and value else None


def _run_status(run: Any) -> str | None:
    value = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    return value.lower() if isinstance(value, str) and value else None


def _runs_from_result(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        runs = result.get("runs")
        return runs if isinstance(runs, list) else []
    runs = getattr(result, "runs", None)
    return runs if isinstance(runs, list) else []


async def _active_run(client: Any, thread_id: str) -> dict[str, Any] | None:
    for status in ACTIVE_RUN_STATUSES:
        try:
            runs_result = await client.runs.list(thread_id, status=status, limit=10)
        except Exception:  # noqa: BLE001
            continue
        for run in _runs_from_result(runs_result):
            run_status = _run_status(run) or status
            if run_status in ACTIVE_RUN_STATUSES:
                return {"run_id": _run_id(run), "status": run_status}
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        thread = None
    thread_status = thread.get("status") if isinstance(thread, dict) else None
    if thread_status == "busy":
        return {"run_id": None, "status": "running"}
    return None


def build_delivery_worker_metadata(
    item: Mapping[str, Any], worker_thread_id: str
) -> dict[str, Any]:
    repo = _repo_from_item(item)
    branch = _first_text(item, "branch", "branch_name", "head_ref")
    base_branch = _first_text(item, "base_branch", "base_ref") or "main"
    item_id = _first_text(item, "id", "dedupe_key")
    metadata: dict[str, Any] = {
        "source": DELIVERY_RUN_SOURCE,
        "delivery_queue_item_id": item_id,
        "source_provider": _first_text(item, "provider", "source_provider"),
        "source_id": _first_text(item, "external_work_item_id", "source_id"),
        "worker_thread_id": worker_thread_id,
        "delivery_worker_thread_id": worker_thread_id,
        "branch": branch,
        "branch_name": branch,
        "base_branch": base_branch,
        "delivery_mode": _first_text(item, "delivery_mode", "deliveryMode") or "pull_request",
        "risk_class": _first_text(item, "risk_class", "riskClass") or "unknown",
        "model_snapshot": _first_value(item, "model_snapshot", "modelSnapshot"),
        "credential_identity": _first_text(
            item,
            "credential_identity",
            "credentialIdentity",
            "github_login",
            "created_by",
        ),
        "title": _first_text(item, "title") or item_id,
        "updated_at_ms": _now_ms(),
        "delivery": {
            "queue_status": "running",
            "worker_thread_id": worker_thread_id,
            "queue_item_id": item_id,
        },
    }
    github_login = _first_text(item, "github_login", "created_by")
    if github_login:
        metadata["github_login"] = github_login
    if repo:
        metadata["repo"] = repo
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
    return metadata


def build_delivery_worker_configurable(
    item: Mapping[str, Any], worker_thread_id: str
) -> dict[str, Any]:
    repo = _repo_from_item(item)
    branch = _first_text(item, "branch", "branch_name", "head_ref")
    configurable: dict[str, Any] = {
        "thread_id": worker_thread_id,
        "source": DELIVERY_RUN_SOURCE,
        "delivery_queue_item_id": _first_text(item, "id", "dedupe_key"),
        "source_provider": _first_text(item, "provider", "source_provider"),
        "source_id": _first_text(item, "external_work_item_id", "source_id"),
        "delivery_mode": _first_text(item, "delivery_mode", "deliveryMode") or "pull_request",
        "risk_class": _first_text(item, "risk_class", "riskClass") or "unknown",
    }
    if branch:
        configurable["branch_name"] = branch
    base_branch = _first_text(item, "base_branch", "base_ref")
    if base_branch:
        configurable["base_branch"] = base_branch
    github_login = _first_text(item, "github_login", "created_by")
    if github_login:
        configurable["github_login"] = github_login
    github_user_id = item.get("github_user_id")
    if github_user_id is not None:
        configurable["github_user_id"] = github_user_id
    if repo:
        configurable["repo"] = repo
    agent_model_id = _first_text(item, "agent_model_id", "model_id")
    if agent_model_id:
        configurable["agent_model_id"] = agent_model_id
    agent_effort = _first_text(item, "agent_effort", "effort")
    if agent_effort:
        configurable["agent_effort"] = agent_effort
    return configurable


def build_delivery_run_record(
    item: Mapping[str, Any],
    *,
    worker_thread_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    branch = _first_text(item, "branch", "branch_name", "head_ref")
    return {
        "run_id": run_id,
        "status": "running",
        "worker_thread_id": worker_thread_id,
        "branch": branch,
        "base_branch": _first_text(item, "base_branch", "base_ref") or "main",
        "sandbox_profile": _mapping_value(item, "sandbox_profile"),
        "worktree": _mapping_value(item, "worktree"),
        "model_snapshot": _first_value(item, "model_snapshot", "modelSnapshot"),
        "credential_identity": _first_text(
            item,
            "credential_identity",
            "credentialIdentity",
            "github_login",
            "created_by",
        ),
        "risk_class": _first_text(item, "risk_class", "riskClass") or "unknown",
        "gates": _list_value(item, "gates"),
        "artifacts": _list_value(item, "artifacts"),
        "blocker_reason": _first_text(item, "blocker_reason", "status_reason") or None,
    }


def build_delivery_worker_prompt(item: Mapping[str, Any]) -> str:
    repo = _repo_from_item(item)
    repo_text = f"{repo['owner']}/{repo['name']}" if repo else "not provided"
    title = (
        _first_text(item, "title") or _first_text(item, "external_work_item_id") or "Delivery item"
    )
    description = _first_text(item, "description", "body", "summary")
    branch = _first_text(item, "branch", "branch_name", "head_ref") or "not provided"
    return "\n".join(
        [
            "Launch this queued delivery item as the implementation worker.",
            "",
            f"Queue item: {_first_text(item, 'id', 'dedupe_key')}",
            f"Source: {_first_text(item, 'provider', 'source_provider')} "
            f"{_first_text(item, 'external_work_item_id', 'source_id')}",
            f"Repository: {repo_text}",
            f"Branch: {branch}",
            f"Delivery mode: {_first_text(item, 'delivery_mode', 'deliveryMode') or 'pull_request'}",
            f"Risk class: {_first_text(item, 'risk_class', 'riskClass') or 'unknown'}",
            "",
            f"Title: {title}",
            "",
            "Description:",
            description or "No additional description was provided.",
            "",
            WORKER_CONTRACT,
        ]
    )


async def _upsert_thread_metadata(client: Any, thread_id: str, metadata: dict[str, Any]) -> None:
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)


async def _project_for_item(item: Mapping[str, Any]) -> dict[str, Any]:
    project_id = _first_text(item, "project_id")
    project = await project_registry.get_delivery_project(project_id) if project_id else None
    if project is not None:
        return project
    return {
        "project_id": project_id,
        "active": True,
        "kill_switch": False,
        "sandbox_profile": _mapping_value(item, "sandbox_profile"),
    }


async def launch_delivery_worker(
    item_id: str,
    *,
    client: Any | None = None,
    start_checks: Mapping[str, Any] | None = None,
    auto_mode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = await read_delivery_queue_item(item_id)
    if item is None:
        return {"status": "refused", "reason": "missing_queue_item", "item_id": item_id}

    current_status = item.get("status")
    if current_status != "queued":
        return {
            "status": "refused",
            "reason": "not_queued",
            "item_id": item_id,
            "current_status": current_status,
        }

    client = client or _client()
    worker_thread_id = _worker_thread_id_for(item)
    active_run = await _active_run(client, worker_thread_id)
    project = await _project_for_item(item)
    start_result = evaluate_delivery_start_preflight(
        item,
        project,
        checks={**dict(start_checks or {}), "duplicate_active_run": active_run is not None},
        auto_mode=auto_mode,
    )
    if not start_result["ready"]:
        await block_delivery_start(item_id, start_result)
        refused = {
            "status": "refused",
            "reason": start_result["blockers"][0]["code"],
            "item_id": item_id,
            "worker_thread_id": worker_thread_id,
            "blockers": start_result["blockers"],
        }
        if active_run is not None:
            refused["active_run_id"] = active_run["run_id"]
            refused["active_run_status"] = active_run["status"]
        return refused

    metadata = build_delivery_worker_metadata(item, worker_thread_id)
    await _upsert_thread_metadata(client, worker_thread_id, metadata)
    run = await dispatch_agent_run(
        worker_thread_id,
        build_delivery_worker_prompt(item),
        build_delivery_worker_configurable(item, worker_thread_id),
        source=DELIVERY_RUN_SOURCE,
        assistant_id="agent",
        metadata=metadata,
        client=client,
    )
    run_id = _run_id(run)
    run_record = build_delivery_run_record(
        item,
        worker_thread_id=worker_thread_id,
        run_id=run_id,
    )
    previous_runs = item.get("runs") if isinstance(item.get("runs"), list) else []
    extra = {
        "worker_thread_id": worker_thread_id,
        "delivery_worker_thread_id": worker_thread_id,
        "delivery": metadata["delivery"],
        "latest_run": run_record,
        "runs": [*previous_runs, run_record],
    }
    if run_id:
        extra["latest_run_id"] = run_id

    updated = await transition_delivery_queue_status(
        item_id,
        "running",
        reason="worker launch dispatched",
        extra=extra,
    )
    return {
        "status": "launched",
        "item_id": item_id,
        "worker_thread_id": worker_thread_id,
        "run_id": run_id,
        "queue_status": updated["status"],
    }
