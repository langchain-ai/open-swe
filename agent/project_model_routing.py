"""Project-level model routing by delivery agent role."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from . import project_registry
from .utils.thread_ops import langgraph_client

MODEL_ROUTING_AUDIT_NAMESPACE: list[str] = ["delivery_project_model_routing_audit"]
MODEL_ROUTING_ROLES: frozenset[str] = frozenset(
    {
        "orchestrator",
        "explorer",
        "executor",
        "drupal_backend",
        "drupal_frontend",
        "content_editor",
        "design",
        "qa_reviewer",
        "vision",
        "helper",
        "fallback",
    }
)


def _client():
    return langgraph_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _selection(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value.strip():
        return {"model_id": value.strip(), "effort": "medium"}
    if not isinstance(value, Mapping):
        return None
    model_id = value.get("model_id") or value.get("model")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    effort = value.get("effort")
    return {
        "model_id": model_id.strip(),
        "effort": effort.strip() if isinstance(effort, str) and effort.strip() else "medium",
    }


def _allowed_model_ids(project: Mapping[str, Any]) -> set[str]:
    policy = _mapping(project.get("ai_hub_policy"))
    raw = policy.get("model_ids") or policy.get("models") or []
    if not isinstance(raw, list):
        return set()
    return {model.strip() for model in raw if isinstance(model, str) and model.strip()}


def _normal_routing(routing: Mapping[str, Any]) -> dict[str, Any]:
    roles = _mapping(routing.get("roles"))
    normalized_roles = {
        role: selection
        for role, value in roles.items()
        if role in MODEL_ROUTING_ROLES and (selection := _selection(value)) is not None
    }
    normalized: dict[str, Any] = {"roles": normalized_roles}
    if default := _selection(routing.get("default")):
        normalized["default"] = default
    if fallback := _selection(routing.get("fallback")):
        normalized["fallback"] = fallback
    return normalized


def validate_project_model_routing(project: Mapping[str, Any]) -> dict[str, Any]:
    routing = _normal_routing(_mapping(project.get("model_routing")))
    if not routing.get("default") and not routing.get("roles") and not routing.get("fallback"):
        return {"ready": True, "blockers": []}
    allowed = _allowed_model_ids(project)
    blockers = []
    for role, selection in _iter_routing_selections(routing):
        model_id = selection["model_id"]
        if model_id not in allowed:
            blockers.append(
                {
                    "code": f"invalid_model:{role}",
                    "message": f"Model {model_id} is not in the project's AI Hub model list.",
                }
            )
    return {"ready": not blockers, "blockers": blockers}


def _iter_routing_selections(routing: Mapping[str, Any]):
    default = _selection(routing.get("default"))
    if default:
        yield "default", default
    for role, value in _mapping(routing.get("roles")).items():
        if selection := _selection(value):
            yield role, selection
    fallback = _selection(routing.get("fallback"))
    if fallback:
        yield "fallback", fallback


def resolve_model_for_role(project: Mapping[str, Any], role: str) -> dict[str, str] | None:
    routing = _normal_routing(_mapping(project.get("model_routing")))
    role = role.strip()
    roles = _mapping(routing.get("roles"))
    if role in roles and (selection := _selection(roles[role])):
        return {"role": role, **selection, "source": "role"}
    if default := _selection(routing.get("default")):
        return {"role": role, **default, "source": "default"}
    if fallback := _selection(routing.get("fallback")):
        return {"role": role, **fallback, "source": "fallback"}
    return None


def build_model_routing_snapshot(
    project: Mapping[str, Any],
    roles: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "project_id": project.get("project_id"),
        "roles": {
            role: selection
            for role in roles
            if (selection := resolve_model_for_role(project, role)) is not None
        },
    }


async def _audit_model_change(
    *,
    project_id: str,
    actor: str,
    role: str,
    previous: str | None,
    new: str | None,
) -> None:
    record = {
        "project_id": project_id,
        "actor": actor,
        "role": role,
        "previous_model_id": previous,
        "new_model_id": new,
        "created_at": _now_iso(),
    }
    key = f"{project_id}:{record['created_at']}:{secrets.token_hex(8)}"
    await _client().store.put_item(MODEL_ROUTING_AUDIT_NAMESPACE, key, record)


def _model_for_audit(routing: Mapping[str, Any], role: str) -> str | None:
    if role == "default":
        selection = _selection(routing.get("default"))
    elif role == "fallback":
        selection = _selection(routing.get("fallback"))
    else:
        selection = _selection(_mapping(routing.get("roles")).get(role))
    return selection["model_id"] if selection else None


async def set_project_model_routing(
    project_id: str,
    routing: Mapping[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    normalized = _normal_routing(routing)
    candidate = {**project, "model_routing": normalized}
    validation = validate_project_model_routing(candidate)
    if not validation["ready"]:
        raise ValueError(validation["blockers"][0]["message"])

    previous = _mapping(project.get("model_routing"))
    roles = {
        "default",
        "fallback",
        *_mapping(normalized.get("roles")).keys(),
        *_mapping(previous.get("roles")).keys(),
    }
    for role in sorted(roles):
        before = _model_for_audit(previous, role)
        after = _model_for_audit(normalized, role)
        if before != after:
            await _audit_model_change(
                project_id=project_id,
                actor=actor,
                role=role,
                previous=before,
                new=after,
            )
    return await project_registry.upsert_delivery_project(
        {"project_id": project_id, "model_routing": normalized}
    )


async def list_model_routing_audit(project_id: str) -> list[dict[str, Any]]:
    result = await _client().store.search_items(
        MODEL_ROUTING_AUDIT_NAMESPACE,
        filter={"project_id": project_id},
        limit=1000,
    )
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    records = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            records.append(value)
    return sorted(records, key=lambda item: str(item.get("created_at", "")))
