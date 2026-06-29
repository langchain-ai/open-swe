"""Launch queued delivery work into agent worker runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from . import project_model_routing, project_registry, project_secrets
from .dashboard.provider_pat_vault import resolve_provider_pat
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
WORKER_OUTPUT_CONTRACT: dict[str, Any] = {
    "required_fields": [
        "cause",
        "changed_files",
        "before_proof",
        "after_proof",
        "executed_gates",
        "risks",
        "pull_request_summary",
    ],
    "before_after_proof_required": True,
    "pull_request_required": True,
}
WORKER_INPUT_KEYS = {
    "issue_context",
    "project_profile",
    "context_pack",
    "sandbox_profile",
    "gate_policy",
    "credential_policy",
    "output_contract",
}


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


def _mapping_from(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _slug(value: str) -> str:
    cleaned = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            cleaned.append(char)
            previous_dash = False
        elif not previous_dash:
            cleaned.append("-")
            previous_dash = True
    return "".join(cleaned).strip("-") or "delivery-item"


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


def _branch_policy(project: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping_from(project.get("branch_policy"))


def _branch_for_item(item: Mapping[str, Any], project: Mapping[str, Any]) -> str:
    existing = _first_text(item, "branch", "branch_name", "head_ref")
    if existing:
        return existing
    policy = _branch_policy(project)
    prefix = str(policy.get("branch_prefix") or "delivery").strip().strip("/")
    source = (
        _first_text(item, "external_work_item_id", "source_id")
        or _first_text(item, "title")
        or _first_text(item, "id", "dedupe_key")
    )
    return f"{prefix}/{_slug(source)}" if prefix else _slug(source)


def _base_branch_for_item(item: Mapping[str, Any], project: Mapping[str, Any]) -> str:
    return (
        _first_text(item, "base_branch", "base_ref")
        or str(_branch_policy(project).get("base_branch") or "main").strip()
        or "main"
    )


def _worktree_for_item(
    item: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    worker_thread_id: str,
    branch: str,
    base_branch: str,
) -> dict[str, Any]:
    existing = _mapping_value(item, "worktree")
    if existing:
        return existing
    sandbox_profile = {
        **_mapping_from(project.get("sandbox_profile")),
        **(_mapping_value(item, "sandbox_profile") or {}),
    }
    root = str(sandbox_profile.get("worktree_root") or "/tmp/open-swe-delivery-worktrees").strip()
    return {
        "path": f"{root.rstrip('/')}/{_slug(branch)}",
        "branch": branch,
        "base_branch": base_branch,
        "isolated": True,
        "worker_thread_id": worker_thread_id,
        "source": "delivery_runner",
    }


def _sandbox_profile_for_item(
    item: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    worktree: Mapping[str, Any],
) -> dict[str, Any]:
    sandbox_profile = {
        **_mapping_from(project.get("sandbox_profile")),
        **(_mapping_value(item, "sandbox_profile") or {}),
    }
    return {**sandbox_profile, "worktree": dict(worktree)}


def _credential_policy_for_item(
    item: Mapping[str, Any], project: Mapping[str, Any]
) -> dict[str, Any]:
    policy = _mapping_value(item, "credential_policy") or _mapping_from(
        project.get("credential_policy")
    )
    if not policy:
        vcs = _mapping_from(project.get("vcs"))
        policy = {
            "provider": vcs.get("provider") or "github",
            "scope": "user",
            "requires_user_pat": True,
        }
    provider = str(policy.get("provider") or "github").strip().lower() or "github"
    identity = _first_text(item, "credential_identity", "credentialIdentity")
    if not identity:
        login = _first_text(item, "github_login", "created_by")
        if login:
            identity = f"{provider}:user:{login}"
    if identity:
        policy = {**policy, "identity": identity}
    return policy


def _login_from_credential_identity(identity: str, provider: str) -> str:
    parts = [part.strip() for part in identity.split(":") if part.strip()]
    if len(parts) >= 3 and parts[0].lower() == provider.lower() and parts[1].lower() == "user":
        return parts[2]
    if len(parts) >= 2 and parts[0].lower() == provider.lower():
        return parts[-1]
    return ""


def _credential_owner_login(item: Mapping[str, Any], credential_policy: Mapping[str, Any]) -> str:
    provider = str(credential_policy.get("provider") or "github").strip().lower() or "github"
    identity = str(credential_policy.get("identity") or "").strip()
    if identity:
        return _login_from_credential_identity(identity, provider)
    return _first_text(item, "github_login", "created_by")


async def _resolve_user_pat_for_preflight(
    item: Mapping[str, Any],
    project: Mapping[str, Any],
    credential_policy: Mapping[str, Any],
) -> tuple[bool | None, dict[str, Any]]:
    if credential_policy.get("requires_user_pat") is not True:
        return None, {}
    provider = str(credential_policy.get("provider") or "github").strip().lower() or "github"
    login = _credential_owner_login(item, credential_policy)
    if not login:
        return False, {}
    resolved = await resolve_provider_pat(
        login,
        provider=provider,
        project_id=_first_text(project, "project_id") or _first_text(item, "project_id"),
        action="preflight",
    )
    if resolved is None:
        return False, {}
    identity = str(credential_policy.get("identity") or f"{provider}:user:{resolved.login}")
    return True, {
        "credential_identity": identity,
        "credential_audit": {
            "login": resolved.login,
            "provider": resolved.provider,
            "project_id": _first_text(project, "project_id") or _first_text(item, "project_id"),
            "action": "preflight",
            "token_last4": resolved.token_last4,
        },
    }


async def _ai_hub_ready_for_project(project: Mapping[str, Any]) -> bool | None:
    policy = _mapping_from(project.get("ai_hub_policy"))
    if policy.get("enabled") is not True:
        return None
    result = await project_secrets.evaluate_ai_hub_readiness(
        _first_text(project, "project_id"),
        environment=str(policy.get("environment") or project_secrets.DEFAULT_AI_HUB_ENVIRONMENT),
    )
    return bool(result.get("ready"))


def _model_routing_snapshot_for_project(project: Mapping[str, Any]) -> dict[str, Any]:
    return project_model_routing.build_model_routing_snapshot(
        project,
        (
            "orchestrator",
            "executor",
            "reviewer",
            "qa",
            "qa_reviewer",
            "drupal_backend",
            "drupal_frontend",
            "content",
            "content_editor",
            "vision",
            "browser_proof",
            "helper",
            "subagent",
            "fallback",
        ),
    )


def _selection_from_snapshot(snapshot: Mapping[str, Any], role: str) -> dict[str, Any]:
    roles = _mapping_from(snapshot.get("roles"))
    return _mapping_from(roles.get(role))


def build_delivery_worker_input(
    item: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    worker_thread_id: str,
) -> dict[str, Any]:
    branch = _branch_for_item(item, project)
    base_branch = _base_branch_for_item(item, project)
    worktree = _worktree_for_item(
        item,
        project,
        worker_thread_id=worker_thread_id,
        branch=branch,
        base_branch=base_branch,
    )
    repo = _repo_from_item(item)
    vcs = _mapping_from(project.get("vcs"))
    tracker = _mapping_from(project.get("tracker"))
    if repo is None:
        vcs_config = _mapping_from(vcs.get("config"))
        owner = vcs_config.get("owner")
        name = vcs_config.get("repo") or vcs_config.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            repo = {"owner": owner, "name": name}
    worker_input = {
        "issue_context": {
            "queue_item_id": _first_text(item, "id", "dedupe_key"),
            "provider": _first_text(item, "provider", "source_provider"),
            "external_work_item_id": _first_text(item, "external_work_item_id", "source_id"),
            "title": _first_text(item, "title"),
            "description": _first_text(item, "description", "body", "summary"),
            "url": _first_text(item, "url", "html_url", "web_url"),
            "repository": repo,
            "branch": branch,
            "base_branch": base_branch,
            "delivery_mode": _first_text(item, "delivery_mode", "deliveryMode") or "pull_request",
            "risk_class": _first_text(item, "risk_class", "riskClass") or "unknown",
        },
        "project_profile": {
            "project_id": _first_text(project, "project_id") or _first_text(item, "project_id"),
            "name": _first_text(project, "name"),
            "tracker_provider": tracker.get("provider"),
            "vcs_provider": vcs.get("provider"),
            "repository": repo,
            "branch_policy": _branch_policy(project),
            "delivery_modes": list(project.get("delivery_modes") or []),
        },
        "context_pack": _mapping_value(item, "context_pack")
        or _mapping_from(project.get("context_pack")),
        "sandbox_profile": _sandbox_profile_for_item(item, project, worktree=worktree),
        "gate_policy": _mapping_value(item, "gate_policy")
        or _mapping_from(project.get("gate_policy")),
        "credential_policy": _credential_policy_for_item(item, project),
        "output_contract": dict(WORKER_OUTPUT_CONTRACT),
    }
    return worker_input


def _item_with_worker_input(
    item: Mapping[str, Any],
    worker_input: Mapping[str, Any],
    project: Mapping[str, Any],
) -> dict[str, Any]:
    issue_context = _mapping_from(worker_input.get("issue_context"))
    sandbox_profile = _mapping_from(worker_input.get("sandbox_profile"))
    updated = {
        **dict(item),
        "branch": issue_context.get("branch"),
        "base_branch": issue_context.get("base_branch"),
        "sandbox_profile": sandbox_profile,
        "worktree": _mapping_from(sandbox_profile.get("worktree")),
        "gate_policy": _mapping_from(worker_input.get("gate_policy")),
        "credential_policy": _mapping_from(worker_input.get("credential_policy")),
        "context_pack": _mapping_from(worker_input.get("context_pack")),
    }
    repo = _mapping_from(issue_context.get("repository"))
    if repo:
        updated["repo"] = repo
    merge_policy = _mapping_from(project.get("merge_policy"))
    if merge_policy:
        updated["merge_policy"] = merge_policy
    return updated


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
    credential_audit = _mapping_value(item, "credential_audit")
    if credential_audit:
        metadata["credential_audit"] = credential_audit
    model_routing_snapshot = _mapping_value(item, "model_routing_snapshot")
    if model_routing_snapshot:
        metadata["model_routing_snapshot"] = model_routing_snapshot
    github_login = _first_text(item, "github_login", "created_by")
    if github_login:
        metadata["github_login"] = github_login
    if repo:
        metadata["repo"] = repo
        metadata["repo_owner"] = repo["owner"]
        metadata["repo_name"] = repo["name"]
    sandbox_profile = _mapping_value(item, "sandbox_profile")
    if sandbox_profile:
        metadata["sandbox_profile"] = sandbox_profile
    worktree = _mapping_value(item, "worktree")
    if worktree:
        metadata["worktree"] = worktree
    return metadata


def build_delivery_worker_configurable(
    item: Mapping[str, Any],
    worker_thread_id: str,
    worker_input: Mapping[str, Any],
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
        "delivery_worker_input": dict(worker_input),
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
    model_routing_snapshot = _mapping_value(item, "model_routing_snapshot")
    executor_model = _selection_from_snapshot(model_routing_snapshot or {}, "executor")
    helper_model = _selection_from_snapshot(model_routing_snapshot or {}, "helper")
    agent_model_id = _first_text(executor_model, "model_id") or _first_text(
        item, "agent_model_id", "model_id"
    )
    if agent_model_id:
        configurable["agent_model_id"] = agent_model_id
    agent_effort = _first_text(executor_model, "effort") or _first_text(
        item, "agent_effort", "effort"
    )
    if agent_effort:
        configurable["agent_effort"] = agent_effort
    helper_model_id = _first_text(helper_model, "model_id")
    if helper_model_id:
        configurable["agent_subagent_model_id"] = helper_model_id
    helper_effort = _first_text(helper_model, "effort")
    if helper_effort:
        configurable["agent_subagent_effort"] = helper_effort
    return configurable


def build_delivery_run_record(
    item: Mapping[str, Any],
    *,
    worker_thread_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    branch = _first_text(item, "branch", "branch_name", "head_ref")
    record = {
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
        "credential_audit": _mapping_value(item, "credential_audit"),
        "risk_class": _first_text(item, "risk_class", "riskClass") or "unknown",
        "gates": _list_value(item, "gates"),
        "artifacts": _list_value(item, "artifacts"),
        "blocker_reason": _first_text(item, "blocker_reason", "status_reason") or None,
    }
    if model_routing_snapshot := _mapping_value(item, "model_routing_snapshot"):
        record["model_routing_snapshot"] = model_routing_snapshot
    return record


def build_delivery_worker_prompt(worker_input: Mapping[str, Any]) -> str:
    issue_context = _mapping_from(worker_input.get("issue_context"))
    title = _first_text(issue_context, "title", "external_work_item_id") or "Delivery item"
    return "\n".join(
        [
            "Launch this queued delivery item as the implementation worker.",
            "",
            "",
            f"Title: {title}",
            "",
            "Delivery context:",
            json.dumps(worker_input, indent=2, sort_keys=True),
            "",
            WORKER_CONTRACT,
            "",
            "When the branch and pull request are ready, call "
            "`submit_delivery_worker_result` with the complete structured result so the "
            "platform can verify QA evidence and start review.",
        ]
    )


def _runtime_fields_for_item(item: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in (
        "branch",
        "base_branch",
        "sandbox_profile",
        "worktree",
        "gate_policy",
        "credential_policy",
        "context_pack",
        "repo",
        "merge_policy",
        "credential_identity",
        "credential_audit",
        "model_routing_snapshot",
    ):
        value = item.get(key)
        if value is not None:
            fields[key] = value
    return fields


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
    credential_policy = _credential_policy_for_item(item, project)
    credential_ready, credential_updates = await _resolve_user_pat_for_preflight(
        item,
        project,
        credential_policy,
    )
    start_checks_payload = dict(start_checks or {})
    if credential_ready is not None:
        start_checks_payload["github_credentials"] = credential_ready
    ai_hub_ready = await _ai_hub_ready_for_project(project)
    if ai_hub_ready is not None:
        start_checks_payload["ai_hub_ready"] = ai_hub_ready
    model_routing_result = await project_model_routing.validate_project_model_routing_ready(project)
    if not model_routing_result["ready"]:
        start_checks_payload["model_routing_invalid"] = True
    model_routing_snapshot = _model_routing_snapshot_for_project(project)
    preflight_item = {
        **dict(item),
        **credential_updates,
        "credential_policy": credential_policy,
    }
    if model_routing_snapshot.get("roles"):
        preflight_item["model_routing_snapshot"] = model_routing_snapshot
    start_result = evaluate_delivery_start_preflight(
        preflight_item,
        project,
        checks={**start_checks_payload, "duplicate_active_run": active_run is not None},
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

    worker_input = build_delivery_worker_input(
        preflight_item,
        project,
        worker_thread_id=worker_thread_id,
    )
    launch_item = {
        **_item_with_worker_input(preflight_item, worker_input, project),
        **credential_updates,
    }
    metadata = build_delivery_worker_metadata(launch_item, worker_thread_id)
    await _upsert_thread_metadata(client, worker_thread_id, metadata)
    run = await dispatch_agent_run(
        worker_thread_id,
        build_delivery_worker_prompt(worker_input),
        build_delivery_worker_configurable(launch_item, worker_thread_id, worker_input),
        source=DELIVERY_RUN_SOURCE,
        assistant_id="agent",
        metadata=metadata,
        client=client,
    )
    run_id = _run_id(run)
    run_record = build_delivery_run_record(
        launch_item,
        worker_thread_id=worker_thread_id,
        run_id=run_id,
    )
    previous_runs = item.get("runs") if isinstance(item.get("runs"), list) else []
    extra = {
        **_runtime_fields_for_item(launch_item),
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
