"""Named environments: a custom prompt plus a sandbox snapshot.

An environment bundles the two things a team needs to make runs start warm: a
prompt appended to the agent's system prompt, and a LangSmith snapshot new
sandboxes boot from. It may cover several repositories — the snapshot is captured
from a live sandbox after the agent has cloned and provisioned whatever the
environment needs, so its contents are whatever was set up in that sandbox.

Snapshots are captured (not built from a Dockerfile) so the setup steps are
ordinary sandbox commands an admin can iterate on in an admin thread. Each
capture is named ``<prefix>-environment-<slug>`` (prefix from
``ENVIRONMENT_SNAPSHOT_PREFIX``); the platform appends its own ``:latest`` tag and
rejects a name that carries one. The previous snapshot is deleted once the new one
is ready, so an environment resolves to exactly one live snapshot.

A run uses the environment it selected — from the dashboard picker, or an
``env:<name>`` tag on the Slack message that opened the thread — and otherwise
the one named ``default``. Nothing here is required: with no environment, or one
whose snapshot is not ready, runs fall back to the per-repo snapshot and then to
the configured base snapshot.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, JsonValue, field_validator

from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

ENVIRONMENTS_NAMESPACE: list[str] = ["environments"]
DEFAULT_ENVIRONMENT_SLUG = "default"

SnapshotStatus = Literal["none", "capturing", "ready", "failed"]


class SandboxResources(TypedDict, total=False):
    mem_bytes: int
    vcpus: int
    fs_capacity_bytes: int


NAME_MAX_CHARS = 80
PROMPT_MAX_CHARS = 20_000
MAX_REPOS = 50
CREATE_PARAMS_MAX_CHARS = 20_000
CAPTURE_NAME_ATTEMPTS = 5

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SENSITIVE_CREATE_PARAM_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "pat",
        "private_key",
        "secret",
        "token",
    }
)
_SENSITIVE_CREATE_PARAM_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_pat",
    "_private_key",
    "_secret",
    "_token",
)
_SENSITIVE_CREATE_PARAM_PREFIXES = (
    "api_key_",
    "authorization_",
    "credential_",
    "credentials_",
    "password_",
    "private_key_",
    "secret_",
    "token_",
)
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "cookie", "proxy_authorization", "x_api_key"})
# `env:my-box` anywhere in a message, as a whole word.
_ENV_TAG_RE = re.compile(r"(?:(?<=\s)|^)env:([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|$)")


def slugify(name: str) -> str:
    """Return the storage key for an environment name.

    Also the snapshot name stem, so it is restricted to what a Docker-style tag
    accepts: lowercase alphanumerics and single hyphens.
    """
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one letter or digit")
    return slug[:NAME_MAX_CHARS]


def snapshot_name_prefix() -> str:
    """Prefix for captured snapshot names, so one workspace can host several deployments.

    A configured prefix carrying a colon would produce a name the platform
    rejects, so it is dropped rather than passed through.
    """
    prefix = os.environ.get("ENVIRONMENT_SNAPSHOT_PREFIX", "").strip()
    if ":" in prefix:
        logger.warning(
            "ENVIRONMENT_SNAPSHOT_PREFIX %r contains a colon, which snapshot names "
            "may not; falling back to the default prefix",
            prefix,
        )
        prefix = ""
    return prefix or "openswe"


def snapshot_name_for(slug: str, attempt: int = 1) -> str:
    """``<prefix>-environment-<slug>``, with ``-2``, ``-3``, … past the first attempt.

    No tag: the platform rejects a colon in the name and appends ``:latest``
    itself. The numeric suffix exists because a capture can collide with a name
    the platform still holds (a prior snapshot mid-delete, a concurrent capture);
    the record stores whichever name won.
    """
    stem = f"{snapshot_name_prefix()}-environment-{slug}"
    return stem if attempt == 1 else f"{stem}-{attempt}"


def _validate_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("name must not be empty")
    if len(text) > NAME_MAX_CHARS:
        raise ValueError(f"name must be at most {NAME_MAX_CHARS} characters")
    slugify(text)
    return text


def _validate_prompt(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > PROMPT_MAX_CHARS:
        raise ValueError(f"prompt must be at most {PROMPT_MAX_CHARS} characters")
    return text


def _validate_repos(value: list[str] | None) -> list[str]:
    if not value:
        return []
    if len(value) > MAX_REPOS:
        raise ValueError(f"at most {MAX_REPOS} repositories per environment")
    return list(dict.fromkeys(normalize_repo_full_name(entry) for entry in value))


def _normalize_create_param_name(value: str) -> str:
    snake_value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", snake_value.lower()).strip("_")


def _is_sensitive_create_param_name(value: str) -> bool:
    normalized = _normalize_create_param_name(value)
    return (
        normalized in _SENSITIVE_CREATE_PARAM_KEYS
        or normalized.endswith(_SENSITIVE_CREATE_PARAM_SUFFIXES)
        or normalized.startswith(_SENSITIVE_CREATE_PARAM_PREFIXES)
    )


def _has_sensitive_create_param(value: JsonValue) -> bool:
    if isinstance(value, dict):
        header_name = value.get("name")
        if isinstance(header_name, str):
            normalized_header = _normalize_create_param_name(header_name)
            if normalized_header in _SENSITIVE_HEADER_NAMES or _is_sensitive_create_param_name(
                normalized_header
            ):
                return True
        for key, nested in value.items():
            if _is_sensitive_create_param_name(key):
                return True
            if _has_sensitive_create_param(nested):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_create_param(item) for item in value)
    return False


def _validate_create_params(value: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    params = value or {}
    proxy_config = params.get("proxy_config")
    if proxy_config is not None:
        if not isinstance(proxy_config, dict):
            raise ValueError("create_params.proxy_config must be a JSON object")
        if "rules" in proxy_config and not isinstance(proxy_config["rules"], list):
            raise ValueError("create_params.proxy_config.rules must be a JSON array")
    try:
        serialized = json.dumps(params, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("create_params must contain only valid JSON values") from exc
    if len(serialized) > CREATE_PARAMS_MAX_CHARS:
        raise ValueError(f"create_params must be at most {CREATE_PARAMS_MAX_CHARS} JSON characters")
    if _has_sensitive_create_param(params):
        raise ValueError("create_params must not contain secrets or authentication credentials")
    return params


class EnvironmentCreate(BaseModel):
    name: str
    prompt: str = ""
    repos: list[str] = Field(default_factory=list)
    mem_bytes: int | None = Field(default=None, gt=0)
    vcpus: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str) -> str:
        return _validate_prompt(v)

    @field_validator("repos")
    @classmethod
    def _check_repos(cls, v: list[str]) -> list[str]:
        return _validate_repos(v)

    @field_validator("create_params")
    @classmethod
    def _check_create_params(cls, v: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_create_params(v)


class EnvironmentUpdate(BaseModel):
    """Partial update: only the fields present are written."""

    name: str | None = None
    prompt: str | None = None
    repos: list[str] | None = None
    mem_bytes: int | None = Field(default=None, gt=0)
    vcpus: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return None if v is None else _validate_name(v)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str | None) -> str | None:
        return None if v is None else _validate_prompt(v)

    @field_validator("repos")
    @classmethod
    def _check_repos(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _validate_repos(v)

    @field_validator("create_params")
    @classmethod
    def _check_create_params(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return None if v is None else _validate_create_params(v)


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _item_value(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def get_environment(slug: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(ENVIRONMENTS_NAMESPACE, slug)
    except Exception as e:  # noqa: BLE001
        logger.debug("environment lookup failed for %s: %s", slug, e)
        return None
    return _item_value(item)


async def list_environments() -> list[dict[str, Any]]:
    try:
        result = await _client().store.search_items(ENVIRONMENTS_NAMESPACE, limit=1000)
    except Exception as e:  # noqa: BLE001
        logger.debug("environment search failed: %s", e)
        return []
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out = [value for item in items or [] if (value := _item_value(item)) is not None]
    out.sort(key=lambda record: record.get("name", ""))
    return out


async def create_environment(create: EnvironmentCreate, created_by: str) -> dict[str, Any]:
    slug = slugify(create.name)
    existing = await get_environment(slug)
    if existing is not None:
        raise ValueError(f"environment {create.name!r} already exists")
    record = {
        "slug": slug,
        "name": create.name.strip(),
        "prompt": create.prompt,
        "repos": create.repos,
        "mem_bytes": create.mem_bytes,
        "vcpus": create.vcpus,
        "fs_capacity_bytes": create.fs_capacity_bytes,
        "create_params": create.create_params,
        "snapshot_id": None,
        "snapshot_name": None,
        "snapshot_status": "none",
        "status_message": None,
        "source_sandbox_id": None,
        "last_captured_at": None,
        "created_by": created_by,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await _client().store.put_item(ENVIRONMENTS_NAMESPACE, slug, record)
    return record


async def update_environment(slug: str, update: EnvironmentUpdate) -> dict[str, Any]:
    existing = await get_environment(slug)
    if existing is None:
        raise ValueError(f"no environment named {slug!r}")
    if update.name is not None and slugify(update.name) != slug:
        raise ValueError("renaming an environment across slugs is not supported; create a new one")
    record = {**existing, "updated_at": _now_iso()}
    if update.name is not None:
        record["name"] = update.name.strip()
    if update.prompt is not None:
        record["prompt"] = update.prompt
    if update.repos is not None:
        record["repos"] = update.repos
    for field in ("mem_bytes", "vcpus", "fs_capacity_bytes", "create_params"):
        if field in update.model_fields_set:
            record[field] = getattr(update, field)
    await _client().store.put_item(ENVIRONMENTS_NAMESPACE, slug, record)
    return record


async def delete_environment(slug: str) -> bool:
    existing = await get_environment(slug)
    if existing is None:
        return False
    try:
        await _client().store.delete_item(ENVIRONMENTS_NAMESPACE, slug)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to delete environment %s: %s", slug, e)
        return False
    await _delete_snapshot(existing.get("snapshot_id"))
    return True


async def resolve_default_environment() -> dict[str, Any] | None:
    """Return the environment named ``default``, or ``None``.

    Never raises: a store failure resolves to ``None`` so runs fall back to the
    per-repo and base snapshots with no environment prompt.
    """
    try:
        return await get_environment(DEFAULT_ENVIRONMENT_SLUG)
    except Exception:  # noqa: BLE001
        logger.debug("default environment resolution failed", exc_info=True)
        return None


async def resolve_environment(slug: str | None) -> dict[str, Any] | None:
    """Return the environment a run uses: the one it selected, else ``default``.

    Never raises, and a selection that no longer exists falls back to ``default``
    rather than failing the run.
    """
    if not slug or slug == DEFAULT_ENVIRONMENT_SLUG:
        return await resolve_default_environment()
    try:
        record = await get_environment(slug)
    except Exception:  # noqa: BLE001
        logger.debug("environment resolution failed for %s", slug, exc_info=True)
        record = None
    if record is None:
        logger.info("Environment %s is not configured; falling back to the default", slug)
        return await resolve_default_environment()
    return record


async def list_environment_options() -> list[dict[str, Any]]:
    """Name/slug/snapshot-state only, for the non-admin environment picker.

    Prompts and snapshot ids stay admin-only; picking an environment needs
    neither.
    """
    return [
        {
            "slug": record.get("slug"),
            "name": record.get("name"),
            "has_snapshot": record.get("snapshot_status") == "ready",
        }
        for record in await list_environments()
        if isinstance(record.get("slug"), str)
    ]


def parse_environment_tag(text: str) -> tuple[str | None, str]:
    """Split a leading-or-inline ``env:<name>`` tag off a message.

    Returns ``(slug, text_without_the_tag)``; ``(None, text)`` when there is no
    tag. The caller decides whether the slug names a real environment — an
    unresolvable tag should be left in the text rather than silently dropped.
    """
    match = _ENV_TAG_RE.search(text or "")
    if match is None:
        return None, text
    try:
        slug = slugify(match.group(1))
    except ValueError:
        return None, text
    before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
    return slug, f"{before} {after}".strip() if before and after else f"{before}{after}".strip()


def environment_snapshot_id(record: dict[str, Any] | None) -> str | None:
    """The snapshot new sandboxes boot from, or ``None`` when not captured yet."""
    if not record or record.get("snapshot_status") != "ready":
        return None
    snapshot_id = record.get("snapshot_id")
    return snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None


def environment_prompt(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    prompt = record.get("prompt")
    return prompt.strip() or None if isinstance(prompt, str) else None


def environment_sandbox_resources(record: dict[str, Any] | None) -> SandboxResources:
    if not record:
        return {}
    resources: SandboxResources = {}
    for field in ("mem_bytes", "vcpus", "fs_capacity_bytes"):
        value = record.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            resources[field] = value
    return resources


def environment_sandbox_create_params(
    record: dict[str, Any] | None,
) -> dict[str, JsonValue]:
    if not record:
        return {}
    value = record.get("create_params")
    if not isinstance(value, dict):
        return {}
    try:
        return _validate_create_params(value)
    except ValueError:
        logger.warning(
            "Ignoring invalid sandbox create params for environment %s", record.get("slug")
        )
        return {}


async def _set_snapshot_state(
    slug: str,
    status: SnapshotStatus,
    *,
    status_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = await get_environment(slug)
    if existing is None:
        return None
    record = {
        **existing,
        "snapshot_status": status,
        "status_message": status_message,
        "updated_at": _now_iso(),
        **(extra or {}),
    }
    await _client().store.put_item(ENVIRONMENTS_NAMESPACE, slug, record)
    return record


def _require_capture_support() -> None:
    """Only the langsmith provider has a snapshot API to capture into."""
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type != "langsmith":
        raise RuntimeError(
            f"capturing an environment snapshot needs SANDBOX_TYPE=langsmith, not {sandbox_type!r}"
        )


async def _delete_snapshot(snapshot_id: object) -> None:
    """Best-effort delete of a superseded snapshot."""
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return
    from agent.integrations.langsmith import get_async_sandbox_client

    try:
        async with get_async_sandbox_client() as client:
            await client.delete_snapshot(snapshot_id)
    except Exception:  # noqa: BLE001
        logger.warning("failed to delete superseded snapshot %s", snapshot_id, exc_info=True)


def _is_name_conflict(exc: BaseException) -> bool:
    if exc.__class__.__name__ in {"ResourceAlreadyExistsError", "ResourceNameConflictError"}:
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    return status_code == 409


async def _capture_with_name_retry(
    client: Any, sandbox_id: str, slug: str, timeout: int
) -> tuple[Any, str]:
    """Capture, walking the name suffix forward past whatever the platform still holds."""
    for attempt in range(1, CAPTURE_NAME_ATTEMPTS + 1):
        snapshot_name = snapshot_name_for(slug, attempt)
        try:
            snapshot = await client.capture_snapshot(sandbox_id, snapshot_name, timeout=timeout)
        except Exception as exc:
            if attempt == CAPTURE_NAME_ATTEMPTS or not _is_name_conflict(exc):
                raise
            logger.info(
                "Snapshot name %s is taken; retrying environment %s with the next suffix",
                snapshot_name,
                slug,
            )
            continue
        return snapshot, snapshot_name
    raise RuntimeError("unreachable snapshot capture retry state")


async def capture_environment_snapshot(
    slug: str,
    sandbox_id: str,
    *,
    timeout: int = 600,
) -> dict[str, Any]:
    """Capture ``sandbox_id``'s filesystem as this environment's snapshot.

    The previous snapshot survives a failed capture, in both senses: it is deleted
    only once the new one is ready, and the record stays ``ready`` so runs keep
    booting from it instead of dropping to the base image.

    Only the langsmith provider can capture; other providers have no snapshot API
    to capture into, so this raises rather than failing deep in the SDK.
    """
    from agent.integrations.langsmith import get_async_sandbox_client

    _require_capture_support()

    record = await get_environment(slug)
    if record is None:
        raise ValueError(f"no environment named {slug!r}")

    previous_snapshot_id = record.get("snapshot_id")
    previous_was_ready = bool(previous_snapshot_id) and record.get("snapshot_status") == "ready"
    await _set_snapshot_state(slug, "capturing")
    try:
        async with get_async_sandbox_client() as client:
            snapshot, snapshot_name = await _capture_with_name_retry(
                client, sandbox_id, slug, timeout
            )
    except Exception as exc:
        logger.warning("snapshot capture failed for environment %s", slug, exc_info=True)
        await _set_snapshot_state(
            slug,
            "ready" if previous_was_ready else "failed",
            status_message=str(exc)[:1000],
        )
        raise

    updated = await _set_snapshot_state(
        slug,
        "ready",
        extra={
            "snapshot_id": snapshot.id,
            "snapshot_name": snapshot_name,
            "source_sandbox_id": sandbox_id,
            "last_captured_at": _now_iso(),
        },
    )
    if previous_snapshot_id != snapshot.id:
        await _delete_snapshot(previous_snapshot_id)
    logger.info("Captured snapshot %s (%s) for environment %s", snapshot.id, snapshot_name, slug)
    return updated or record
