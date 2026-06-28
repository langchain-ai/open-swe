"""Encrypted project-scoped secrets and AI Hub readiness checks."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from langgraph_sdk import get_client

from .encryption import decrypt_token, encrypt_token

DELIVERY_PROJECT_SECRETS_NAMESPACE: list[str] = ["delivery_project_secrets"]
DEFAULT_AI_HUB_ENVIRONMENT = "default"
AI_HUB_BASE_URL_SECRET = "AI_HUB_BASE_URL"
AI_HUB_API_KEY_SECRET = "AI_HUB_API_KEY"
AI_HUB_MODELS_ENV_SUFFIX = "MODELS"
PROJECT_SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PROJECT_SECRET_NAME_LENGTH = 128
MAX_PROJECT_SECRET_VALUE_LENGTH = 32_768
DEFAULT_AI_HUB_ENV_PREFIXES = ("AI_HUB", "OPENCODE_AI_HUB", "OPENCODE_GO_AI_HUB")


@dataclass(frozen=True)
class AIHubCredentials:
    base_url: str
    api_key: str


AIHubValidator = Callable[[AIHubCredentials], bool]


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


def _normalize_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value:
        raise ValueError("project_id is required")
    return value


def _normalize_environment(environment: str | None) -> str:
    value = (environment or DEFAULT_AI_HUB_ENVIRONMENT).strip().lower()
    if not value:
        raise ValueError("environment is required")
    return value


def _normalize_secret_name(name: str) -> str:
    value = name.strip()
    if (
        not value
        or len(value) > MAX_PROJECT_SECRET_NAME_LENGTH
        or not PROJECT_SECRET_NAME_PATTERN.match(value)
    ):
        raise HTTPException(422, "invalid project secret name")
    return value


def _normalize_secret_value(value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(422, "secret value must be a non-empty string")
    if len(value) > MAX_PROJECT_SECRET_VALUE_LENGTH:
        raise HTTPException(422, "secret value is too long")
    return value


def _namespace(project_id: str, environment: str | None) -> list[str]:
    return [
        *DELIVERY_PROJECT_SECRETS_NAMESPACE,
        _normalize_project_id(project_id),
        _normalize_environment(environment),
    ]


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _redact(record: Mapping[str, Any] | None, *, project_id: str, environment: str, name: str):
    if not record:
        return {
            "connected": False,
            "project_id": project_id,
            "environment": environment,
            "name": name,
        }
    return {
        "connected": True,
        "project_id": record.get("project_id", project_id),
        "environment": record.get("environment", environment),
        "name": record.get("name", name),
        "kind": record.get("kind", "api_key"),
        "value_last4": record.get("value_last4", ""),
        "version": record.get("version", 1),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "updated_by": record.get("updated_by", ""),
    }


async def _get_secret_record(
    project_id: str,
    *,
    environment: str | None,
    name: str,
) -> dict[str, Any] | None:
    project_id = _normalize_project_id(project_id)
    environment = _normalize_environment(environment)
    name = _normalize_secret_name(name)
    return _value_from_item(
        await _client().store.get_item(_namespace(project_id, environment), name)
    )


async def upsert_project_secret(
    project_id: str,
    *,
    environment: str | None,
    name: str,
    value: str,
    updated_by: str,
    kind: str = "api_key",
) -> dict[str, Any]:
    project_id = _normalize_project_id(project_id)
    environment = _normalize_environment(environment)
    name = _normalize_secret_name(name)
    value = _normalize_secret_value(value)
    existing = await _get_secret_record(project_id, environment=environment, name=name) or {}
    now = _now_iso()
    record = {
        "project_id": project_id,
        "environment": environment,
        "name": name,
        "kind": kind.strip() or "api_key",
        "encrypted_value": encrypt_token(value),
        "value_last4": _last4(value),
        "version": int(existing.get("version") or 0) + 1,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "updated_by": updated_by,
    }
    await _client().store.put_item(_namespace(project_id, environment), name, record)
    return _redact(record, project_id=project_id, environment=environment, name=name)


async def list_project_secrets(
    project_id: str,
    *,
    environment: str | None,
) -> list[dict[str, Any]]:
    project_id = _normalize_project_id(project_id)
    environment = _normalize_environment(environment)
    result = await _client().store.search_items(_namespace(project_id, environment), limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    records = [
        _redact(value, project_id=project_id, environment=environment, name=str(value.get("name")))
        for item in items or []
        if (value := _value_from_item(item)) is not None
    ]
    return sorted(records, key=lambda item: str(item.get("name", "")))


async def resolve_project_secret(
    project_id: str,
    *,
    environment: str | None,
    name: str,
) -> str | None:
    record = await _get_secret_record(project_id, environment=environment, name=name)
    if not record:
        return None
    value = decrypt_token(str(record.get("encrypted_value", "")))
    return value or None


async def revoke_project_secret(
    project_id: str,
    *,
    environment: str | None,
    name: str,
) -> dict[str, Any]:
    project_id = _normalize_project_id(project_id)
    environment = _normalize_environment(environment)
    name = _normalize_secret_name(name)
    await _client().store.delete_item(_namespace(project_id, environment), name)
    return {
        "connected": False,
        "project_id": project_id,
        "environment": environment,
        "name": name,
    }


async def test_project_secret(
    project_id: str,
    *,
    environment: str | None,
    name: str,
) -> dict[str, Any]:
    project_id = _normalize_project_id(project_id)
    environment = _normalize_environment(environment)
    name = _normalize_secret_name(name)
    value = await resolve_project_secret(project_id, environment=environment, name=name)
    return {
        "ready": bool(value),
        "project_id": project_id,
        "environment": environment,
        "name": name,
    }


def _default_ai_hub_validator(credentials: AIHubCredentials) -> bool:
    parsed = urlparse(credentials.base_url)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(credentials.api_key.strip())
    )


async def evaluate_ai_hub_readiness(
    project_id: str,
    *,
    environment: str | None,
    validator: AIHubValidator | None = None,
) -> dict[str, Any]:
    environment = _normalize_environment(environment)
    base_url = await resolve_project_secret(
        project_id,
        environment=environment,
        name=AI_HUB_BASE_URL_SECRET,
    )
    api_key = await resolve_project_secret(
        project_id,
        environment=environment,
        name=AI_HUB_API_KEY_SECRET,
    )
    blockers = []
    if not base_url:
        blockers.append(
            {"code": "missing_ai_hub_base_url", "message": "AI Hub base URL is missing."}
        )
    if not api_key:
        blockers.append({"code": "missing_ai_hub_api_key", "message": "AI Hub API key is missing."})
    if blockers:
        return {"ready": False, "blockers": blockers, "environment": environment}

    validator = validator or _default_ai_hub_validator
    try:
        valid = validator(AIHubCredentials(base_url=base_url, api_key=api_key))
    except Exception:  # noqa: BLE001
        valid = False
    if not valid:
        return {
            "ready": False,
            "blockers": [
                {
                    "code": "invalid_ai_hub_credentials",
                    "message": "AI Hub credentials are invalid.",
                }
            ],
            "environment": environment,
        }
    return {"ready": True, "blockers": [], "environment": environment}


def _env_prefixes(prefixes: Sequence[str] | None = None) -> tuple[str, ...]:
    if prefixes is not None:
        return tuple(prefix.strip() for prefix in prefixes if prefix.strip())
    configured = os.environ.get("PROJECT_AI_HUB_ENV_PREFIXES", "")
    if configured.strip():
        return tuple(prefix.strip() for prefix in configured.split(",") if prefix.strip())
    return DEFAULT_AI_HUB_ENV_PREFIXES


def import_ai_hub_shape_from_env(
    env: Mapping[str, str] | None = None,
    *,
    prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    candidates = []
    for prefix in _env_prefixes(prefixes):
        base_url_env = f"{prefix}_BASE_URL"
        api_key_env = f"{prefix}_API_KEY"
        models_env = f"{prefix}_{AI_HUB_MODELS_ENV_SUFFIX}"
        candidates.append(
            {
                "prefix": prefix,
                "required_secrets": [
                    {
                        "name": AI_HUB_BASE_URL_SECRET,
                        "source_env": base_url_env,
                        "present": bool(str(env.get(base_url_env, "")).strip()),
                    },
                    {
                        "name": AI_HUB_API_KEY_SECRET,
                        "source_env": api_key_env,
                        "present": bool(str(env.get(api_key_env, "")).strip()),
                    },
                ],
                "model_list_env": models_env,
                "model_list_present": bool(str(env.get(models_env, "")).strip()),
            }
        )
    return {"provider": "ai_hub", "candidates": candidates}


async def import_ai_hub_secrets_from_env(
    project_id: str,
    *,
    environment: str | None,
    updated_by: str,
    env: Mapping[str, str] | None = None,
    prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    shape = import_ai_hub_shape_from_env(env, prefixes=prefixes)
    imported: list[dict[str, Any]] = []
    source_prefix = ""
    for candidate in shape["candidates"]:
        required = candidate.get("required_secrets")
        if not isinstance(required, list) or not all(secret.get("present") for secret in required):
            continue
        source_prefix = str(candidate.get("prefix") or "")
        for secret in required:
            source_env = str(secret["source_env"])
            imported.append(
                await upsert_project_secret(
                    project_id,
                    environment=environment,
                    name=str(secret["name"]),
                    value=str(env[source_env]),
                    updated_by=updated_by,
                    kind="ai_hub_credential",
                )
            )
        break
    return {
        "provider": "ai_hub",
        "project_id": _normalize_project_id(project_id),
        "environment": _normalize_environment(environment),
        "source_prefix": source_prefix,
        "imported": imported,
        "shape": shape,
    }
