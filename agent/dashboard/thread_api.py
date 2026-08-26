"""Dashboard thread list/detail/run/stream endpoints backed by LangGraph."""

import asyncio
import base64
import binascii
import json
import logging
import os
import posixpath
import re
import shlex
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from pydantic import BaseModel, ConfigDict, Field

from ..input_messages import (
    PersonIdentity,
    build_input_messages,
    dynamic_context_hashes_from_messages,
    injected_dynamic_context_hashes_from_metadata,
)
from ..utils.dashboard_handoff import DASHBOARD_HANDOFF_BODY
from ..utils.json_types import (
    JsonObject,
    ThreadLike,
    as_json_object,
    as_thread_dict,
    thread_metadata,
)
from ..utils.slack import (
    lookup_slack_thread_run_mapping,
    parse_github_pr_url,
    update_slack_trace_reply_for_web_handoff,
)
from ..utils.thread_ops import (
    get_thread_active_status,
    langgraph_client,
    langgraph_url,
    queue_message_for_thread,
)
from ..utils.thread_participants import PARTICIPANT_LOGINS_KEY, merge_participant_logins
from .admin import is_admin
from .agent_overrides import normalize_profile_overrides
from .environments import get_environment, slugify
from .options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_images,
    normalize_model_choice,
)
from .pr_diff import build_compare_diff_files, build_pr_diff_files
from .profiles import get_profile, get_valid_access_token
from .pull_request_status import get_pull_request_statuses
from .team_settings import get_team_default_model, get_team_fable_enabled
from .thread_registry import (
    ThreadCreate,
    ThreadEnvironment,
    ThreadRow,
    _as_utc,
    get_thread_registry,
    utcnow,
)
from .thread_transcript import messages_to_ui, ui_messages_to_state
from .ttft import AssistantTextEventDetector, record_dashboard_thread_ttft
from .user_mappings import email_for_login

logger = logging.getLogger(__name__)

_TTFT_OBSERVER_TASKS: set[asyncio.Task[None]] = set()
_ASSISTANT_ID = "agent"
_DASHBOARD_SOURCE = "dashboard"
# Modes required for the v2 event-stream protocol (`POST …/stream/events`).
_DASHBOARD_STREAM_MODES: tuple[str, ...] = (
    "values",
    "updates",
    "messages",
    "messages-tuple",
    "tools",
    "checkpoints",
    "events",
)
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_MAX_DASHBOARD_IMAGES = 5
_MAX_DASHBOARD_IMAGE_BYTES = 10 * 1024 * 1024
_PROXY_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_DISCOVERY_HISTORY_LIMIT = 5
_PROXY_STREAM_TIMEOUT = httpx.Timeout(None)
# Sources whose threads should surface in the Agents UI (besides "dashboard").
_SURFACED_SOURCES: tuple[str, ...] = ("dashboard", "github", "slack", "linear", "schedule")
# PR lifecycle states surfaced to the UI for a thread's associated pull request.
_RECOVERY_PATCH_LIMIT_BYTES = 25 * 1024 * 1024
_RECOVERY_PATCH_TIMEOUT_SECONDS = 120
_SANDBOX_CREATING_SENTINEL = "__creating__"


async def create_sandbox(*args: Any, **kwargs: Any) -> Any:
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from ..utils.sandbox import create_sandbox as _create_sandbox

    return await _create_sandbox(*args, **kwargs)


def _agent_version_metadata() -> dict[str, str]:
    revision = os.environ.get("LANGCHAIN_REVISION_ID")
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


def _require_json_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def _langgraph_proxy_headers(
    *, content_type: str = "application/json", accept: str | None = None
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if accept:
        headers["Accept"] = accept
    api_key = (
        os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
        or os.environ.get("LANGSMITH_API_KEY_PROD")
    )
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


async def _resolve_run_email(login: str, profile: dict[str, Any]) -> str | None:
    """Email used for GitHub/LangSmith auth on a run.

    Prefers the admin/self GitHub→email mapping (the work email known to
    the org) over the OAuth profile email, which may be a personal account
    that isn't an org member.
    """
    mapped = await email_for_login(login)
    return mapped or profile.get("email")


class DashboardImageBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str | None = None
    base64: str = Field(min_length=1)
    mime_type: str = Field(alias="mimeType", min_length=1)
    file_name: str | None = Field(default=None, alias="fileName")


class ThreadMessageBody(BaseModel):
    content: str = Field(default="", max_length=20_000)
    images: list[DashboardImageBody] = Field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    plan_mode: bool = False


class ThreadResolveBody(BaseModel):
    resolved: bool = True


class ThreadCreateBody(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    title: str = Field(default="Untitled agent", max_length=500)
    repo: str | None = Field(default=None, max_length=500)
    branch: str | None = Field(default=None, max_length=500)
    environment: Literal["cloud", "local"] = "cloud"
    device_id: str | None = Field(default=None, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    effort: str | None = Field(default=None, max_length=100)


class ThreadPatchBody(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    resolved: bool | None = None
    viewed_run_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=255)
    effort: str | None = Field(default=None, max_length=100)


class ThreadHandoffBody(BaseModel):
    target: Literal["cloud", "local"]
    device_id: str | None = Field(default=None, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)
    git_checkpoint: dict[str, Any] | None = None


class LocalReportBody(BaseModel):
    thread_id: str | None = Field(default=None, max_length=128)
    device_id: str = Field(max_length=255)
    device_name: str = Field(max_length=255)
    run_id: str | None = Field(default=None, max_length=128)
    status: Literal["queued", "running", "interrupted", "finished", "error"] | None = None
    error: str | None = Field(default=None, max_length=10_000)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    git_checkpoint: dict[str, Any] | None = None


async def _resolve_agent_model_choice(
    profile: dict[str, Any],
    model_id: str | None,
    effort: str | None,
) -> tuple[str, str]:
    resolved_model, resolved_effort = await get_team_default_model("agent")
    profile_model, profile_effort = normalize_profile_overrides(profile)
    if profile_model and profile_effort:
        resolved_model, resolved_effort = profile_model, profile_effort
    chosen_model, chosen_effort = normalize_model_choice(model_id, effort)
    if chosen_model and chosen_effort:
        resolved_model, resolved_effort = chosen_model, chosen_effort
    resolved_model, resolved_effort = gate_fable_model(
        resolved_model, resolved_effort, fable_enabled=await get_team_fable_enabled()
    )
    if not isinstance(resolved_effort, str):
        raise ValueError("team default model must include a reasoning effort")
    return resolved_model, resolved_effort


def _with_vision_fallback(model_id: str, effort: str, *, has_images: bool) -> tuple[str, str]:
    if not has_images or model_supports_images(model_id):
        return model_id, effort
    fallback_model_id, fallback_effort = default_vision_model_pair()
    logger.info(
        "Using vision fallback model %s for dashboard image input; configured model %s "
        "does not support images",
        fallback_model_id,
        model_id,
    )
    return fallback_model_id, fallback_effort


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _parse_repo(full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str):
        return None
    parts = full_name.strip().split("/", 1)
    if len(parts) != 2:
        return None
    owner, name = parts[0].strip(), parts[1].strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


def _decode_dashboard_image(image: DashboardImageBody) -> bytes:
    if image.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        raise HTTPException(422, f"unsupported image type: {image.mime_type}")
    try:
        data = base64.b64decode(image.base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(422, "invalid image data") from exc
    if len(data) > _MAX_DASHBOARD_IMAGE_BYTES:
        raise HTTPException(422, "image exceeds 10MB limit")
    return data


def _image_blocks(
    images: list[DashboardImageBody], *, model_id: str | None
) -> list[ImageContentBlock]:
    if len(images) > _MAX_DASHBOARD_IMAGES:
        raise HTTPException(422, f"at most {_MAX_DASHBOARD_IMAGES} images are supported")
    if images and (not model_id or not model_supports_images(model_id)):
        model_label = model_id or "the current model"
        raise HTTPException(422, f"model {model_label} does not support image input")
    return [
        create_image_block(
            base64=base64.b64encode(_decode_dashboard_image(image)).decode("ascii"),
            mime_type=image.mime_type,
        )
        for image in images
    ]


def _user_message_content(
    prompt: str, images: list[DashboardImageBody], *, model_id: str | None = None
) -> str | list[ImageContentBlock | dict[str, str]]:
    text = prompt.strip()
    if not text and not images:
        raise HTTPException(422, "prompt or image required")
    if not images:
        return text
    return [
        *_image_blocks(images, model_id=model_id),
        *([{"type": "text", "text": text}] if text else []),
    ]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


def _thread_owner_login(metadata: Mapping[str, Any]) -> str | None:
    login = metadata.get("github_login")
    return login.strip() if isinstance(login, str) and login.strip() else None


def _thread_owner_email(metadata: Mapping[str, Any]) -> str | None:
    email = metadata.get("triggering_user_email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _thread_source(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("source")
    return source if isinstance(source, str) and source else _DASHBOARD_SOURCE


def _metadata_model_id(metadata: Mapping[str, Any]) -> str | None:
    for key in ("resolved_model", "model"):
        model = metadata.get(key)
        if isinstance(model, str) and model in SUPPORTED_MODEL_IDS:
            return model
        canonical = canonical_model_pair(model)
        if canonical is not None:
            return canonical[0]
    return None


def _user_owns_thread(metadata: Mapping[str, Any], login: str, email: str | None) -> bool:
    if _thread_source(metadata) not in _SURFACED_SOURCES:
        return False
    if _thread_owner_login(metadata) == login:
        return True
    if email and _thread_owner_email(metadata) == email.strip().lower():
        return True
    return False


def _assert_thread_owner(metadata: Mapping[str, Any], login: str, email: str | None = None) -> None:
    if not _user_owns_thread(metadata, login, email):
        raise HTTPException(404, "thread not found")


def _attribution_prefix(metadata: Mapping[str, Any], login: str, email: str | None) -> str:
    """Attribution prefix for a message; empty when the poster owns the thread.

    Teammates can post into any surfaced-source thread (read access is already
    org-gated). Their messages are tagged with the verified session login so the
    agent and the thread owner can tell who sent them.
    """
    if _user_owns_thread(metadata, login, email):
        return ""
    return f"@{login}: "


def _thread_is_readable(metadata: Mapping[str, Any]) -> bool:
    """Any surfaced-source thread is readable by authenticated users.

    Dashboard login is already gated by ``ALLOWED_GITHUB_ORGS`` (see
    ``oauth.enforce_org_login_gate``), so any logged-in user is a trusted
    org member. This lets teammates open "Open in Web" links shared in Slack
    threads with read-only access.
    """
    return _thread_source(metadata) in _SURFACED_SOURCES


def _assert_thread_readable(metadata: Mapping[str, Any]) -> None:
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")


def _assert_thread_postable(
    metadata: Mapping[str, Any], login: str, email: str | None = None
) -> None:
    _assert_thread_readable(metadata)
    if metadata.get("admin_thread") is True and not is_admin(email, login=login):
        raise HTTPException(403, "only admins can send messages in admin threads")


def _metadata_repo(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return owner, name, f"{owner}/{name}"
    repo = metadata.get("repo")
    if isinstance(repo, dict):
        o = repo.get("owner")
        n = repo.get("name")
        if isinstance(o, str) and isinstance(n, str) and o and n:
            return o, n, f"{o}/{n}"
    return "", "", ""


def _is_thread_resolved(metadata: Mapping[str, Any]) -> bool:
    return metadata.get("resolved") is True


async def get_dashboard_terminal_sandbox(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[str, str | None]:
    row = await _registry_thread(thread_id, login, email=email)
    if row.environment != "cloud":
        raise HTTPException(409, "local terminals are served by the assigned device")
    if not row.sandbox_id or row.sandbox_id == _SANDBOX_CREATING_SENTINEL:
        raise HTTPException(404, "thread sandbox is not ready")
    repo_name = row.repo_full_name.rsplit("/", 1)[-1] if row.repo_full_name else None
    return row.sandbox_id, repo_name


async def _resolve_requested_environment(requested: Any) -> str | None:
    """Normalize a requested environment slug, dropping one that does not exist.

    The picker only offers configured environments, so a miss means a stale client
    — the thread falls back to the default rather than booting from nothing.
    """
    if not isinstance(requested, str) or not requested.strip():
        return None
    try:
        slug = slugify(requested)
    except ValueError:
        return None
    return slug if await get_environment(slug) is not None else None


def _resolve_repo_config(repo: str | None) -> dict[str, str]:
    """Resolve the run's repo from the request, or ``{}`` when none is given."""
    return _parse_repo(repo) or {}


async def _create_dashboard_thread_record(
    thread_id: str,
    *,
    login: str,
    repo_config: dict[str, str],
    repo_explicitly_none: bool = False,
    prompt: str,
    images: list[DashboardImageBody] | None = None,
    title: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    plan_mode: bool = False,
    admin_thread: bool = False,
    environment: str | None = None,
) -> dict[str, Any]:
    """Create or update dashboard thread metadata without starting a run."""
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    prompt = prompt.strip()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, model_id, effort)
    resolved_model, resolved_effort = _with_vision_fallback(
        resolved_model,
        resolved_effort,
        has_images=bool(images),
    )
    _user_message_content(prompt, images or [], model_id=resolved_model)
    chosen_model, chosen_effort = normalize_model_choice(model_id, effort)
    metadata_model = chosen_model or profile.get("default_model") or "Default"
    metadata_effort = chosen_effort or profile.get("reasoning_effort")
    if images and not model_supports_images(str(metadata_model)):
        metadata_model = resolved_model
        metadata_effort = resolved_effort
    has_repo = bool(repo_config.get("owner") and repo_config.get("name"))
    initial_title = title or prompt[:80] or "New agent"
    metadata: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "origin": _DASHBOARD_SOURCE,
        "thread_category": "interactive",
        "trigger_kind": "user",
        "github_login": login,
        PARTICIPANT_LOGINS_KEY: [login],
        "title": initial_title,
        "base_branch": profile.get("base_branch") or "main",
        "branch_prefix": profile.get("branch_prefix"),
        "model": metadata_model,
        "effort": metadata_effort,
        "resolved_model": resolved_model,
        "resolved_effort": resolved_effort,
        "plan_mode": plan_mode,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    if admin_thread:
        metadata["admin_thread"] = True
    if environment:
        metadata["environment"] = environment
    if not title:
        metadata["title_seed"] = initial_title
    if has_repo:
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    elif repo_explicitly_none:
        metadata["repo_explicitly_none"] = True

    client = langgraph_client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    registry = await get_thread_registry()
    await registry.create(
        ThreadCreate(
            id=thread_id,
            owner_login=login,
            owner_email=await _resolve_run_email(login, profile),
            title=initial_title,
            repo_full_name=(f"{repo_config['owner']}/{repo_config['name']}" if has_repo else None),
            branch=str(metadata.get("base_branch") or "main"),
            environment="cloud",
            source=_DASHBOARD_SOURCE,
            category="interactive",
            trigger_kind="user",
            model=str(metadata_model) if metadata_model else None,
            effort=str(metadata_effort) if metadata_effort else None,
            metadata=metadata,
        )
    )
    thread = await client.threads.get(thread_id)
    return as_thread_dict(thread)


def _repo_config_from_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    owner, name, _ = _metadata_repo(metadata)
    if owner and name:
        return {"owner": owner, "name": name}
    return {}


async def _build_dashboard_configurable(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile if profile is not None else await get_profile(login) or {}
    thread_source = _thread_source(metadata)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": thread_source,
        "github_login": login,
        "user_email": await _resolve_run_email(login, profile),
    }
    repo_config = _repo_config_from_metadata(metadata)
    if repo_config:
        configurable["repo"] = repo_config
    elif metadata.get("repo_explicitly_none") is True:
        configurable["repo_explicitly_none"] = True
    source_context = metadata.get("source_context")
    if isinstance(source_context, dict):
        for key, value in source_context.items():
            configurable.setdefault(key, value)
    if metadata.get("plan_mode") is True:
        configurable["plan_mode"] = True
    # The agent re-checks the requesting user against CONFIGURED_ADMINS before it
    # hands out the environment tools, so this only marks intent.
    if metadata.get("admin_thread") is True:
        configurable["admin_thread"] = True
    environment = metadata.get("environment")
    if isinstance(environment, str) and environment:
        configurable["environment"] = environment
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                configurable[key] = value
    return configurable


def _extract_run_id_from_command_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for candidate in (
        payload.get("run_id"),
        payload.get("result", {}).get("run_id")
        if isinstance(payload.get("result"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _command_message_content(params: dict[str, Any]) -> Any:
    """The most recent user message content from a ``run.start`` command."""
    run_input = params.get("input")
    if not isinstance(run_input, dict):
        return None
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    return last.get("content") if isinstance(last, dict) else None


def _command_message_id(params: dict[str, Any]) -> str | None:
    """The client-minted id of a ``run.start`` command's newest user message."""
    run_input = params.get("input")
    if not isinstance(run_input, dict):
        return None
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if not isinstance(last, dict):
        return None
    message_id = last.get("id")
    return message_id if isinstance(message_id, str) and message_id else None


def _set_command_last_message_content(params: dict[str, Any], content: Any) -> None:
    run_input = params.get("input")
    if not isinstance(run_input, dict):
        return
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    last = messages[-1]
    if isinstance(last, dict):
        last["content"] = content


def _prefix_message_content(content: Any, prefix: str) -> Any:
    if not prefix:
        return content
    if isinstance(content, str):
        return f"{prefix}{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": prefix.rstrip()}, *content]
    return content


def _prepend_message_content_block(content: Any, text: str) -> Any:
    block = {"type": "text", "text": text}
    if isinstance(content, str):
        return [block, {"type": "text", "text": content}]
    if isinstance(content, list):
        return [block, *content]
    if content is None:
        return [block]
    return content


def _command_prompt_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(text for text in texts if isinstance(text, str)).strip()
    return ""


def _dashboard_images_from_content(content: Any) -> list[DashboardImageBody]:
    """Reconstruct typed image bodies from a command's message content blocks.

    The client sends image blocks as ``{"type": "image", "base64", "mime_type",
    "file_name"}`` (see the prompt bar). Rebuilding them lets
    the shared ``_create_dashboard_thread_record`` validate size/type/model.
    """
    if not isinstance(content, list):
        return []
    images: list[DashboardImageBody] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = block.get("base64")
        mime = block.get("mime_type") or block.get("mimeType")
        if not isinstance(data, str) or not isinstance(mime, str):
            raise HTTPException(422, "invalid image data")
        file_name = block.get("file_name") or block.get("fileName")
        images.append(
            DashboardImageBody(
                base64=data,
                mimeType=mime,
                fileName=file_name if isinstance(file_name, str) else None,
            )
        )
    return images


def _validate_command_images(content: Any, *, model_id: str | None) -> None:
    """Reject images for text-only models / oversize attachments (raises 422)."""
    images = _dashboard_images_from_content(content)
    if images:
        _image_blocks(images, model_id=model_id)


async def _enrich_run_start_command(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    thread_busy: bool = False,
    creating: bool = False,
    email: str | None = None,
) -> dict[str, Any]:
    if command.get("method") != "run.start":
        return command

    if thread_busy:
        raise HTTPException(409, "thread is already running; queue message instead")

    client = langgraph_client()
    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
        command["params"] = params

    await _ensure_dashboard_github_token(login)

    client_config = params.get("config")
    if not isinstance(client_config, dict):
        client_config = {}
    client_configurable = client_config.get("configurable")
    if not isinstance(client_configurable, dict):
        client_configurable = {}

    chosen_model, chosen_effort = normalize_model_choice(
        client_configurable.get("agent_model_id"),
        client_configurable.get("agent_effort"),
    )
    plan_mode_requested = client_configurable.get("plan_mode") is True
    content = _command_message_content(params)
    command_images = _dashboard_images_from_content(content)
    prepare_run_id = str(uuid.uuid4())
    overrides: dict[str, Any] = {"prepare_run_id": prepare_run_id}
    run_model: str | None = None
    run_effort: str | None = None

    if creating:
        # First ``run.start`` for a client-minted thread id: stamp the full
        # dashboard thread record (owner, title, repo, model) and validate any
        # attached images against the resolved model before the run is
        # forwarded to LangGraph. The repo hint rides in the client
        # configurable; it never reaches the run config (which is rebuilt from
        # the stamped metadata below).
        thread = await _create_dashboard_thread_record(
            thread_id,
            login=login,
            repo_config=_parse_repo(client_configurable.get("repo")) or {},
            repo_explicitly_none=client_configurable.get("repo_explicitly_none") is True,
            prompt=_command_prompt_text(content),
            images=command_images,
            model_id=client_configurable.get("agent_model_id"),
            effort=client_configurable.get("agent_effort"),
            plan_mode=plan_mode_requested,
            admin_thread=(
                client_configurable.get("admin_thread") is True and is_admin(email, login=login)
            ),
            environment=await _resolve_requested_environment(
                client_configurable.get("environment")
            ),
        )
        metadata = thread_metadata(thread)
        run_model = _metadata_model_id(metadata)
        resolved_effort = metadata.get("resolved_effort")
        if isinstance(resolved_effort, str):
            run_effort = resolved_effort
        if command_images and run_model and run_effort:
            overrides["agent_model_id"] = run_model
            overrides["agent_effort"] = run_effort
        elif chosen_model and chosen_effort:
            overrides["agent_model_id"] = chosen_model
            overrides["agent_effort"] = chosen_effort
    else:
        run_model = chosen_model or _metadata_model_id(metadata)
        run_effort = chosen_effort
        if not run_effort:
            for key in ("resolved_effort", "effort"):
                value = metadata.get(key)
                if isinstance(value, str):
                    run_effort = value
                    break
        if command_images and run_model and run_effort:
            run_model, run_effort = _with_vision_fallback(run_model, run_effort, has_images=True)
        _validate_command_images(content, model_id=run_model)

    if content is None:
        content = ""
    sender_id = f"github:{login}"
    injected = injected_dynamic_context_hashes_from_metadata(metadata)
    persisted_message_ids: set[str] = set()
    if not creating:
        try:
            prior_state = await client.threads.get_state(thread_id)
            values = prior_state.get("values") if isinstance(prior_state, dict) else None
            if isinstance(values, dict):
                messages = values.get("messages")
                injected.update(dynamic_context_hashes_from_messages(messages))
                if isinstance(messages, list):
                    persisted_message_ids = {
                        message_id
                        for message in messages
                        if isinstance(message, Mapping)
                        and isinstance(message_id := message.get("id"), str)
                    }
        except Exception:
            logger.debug("Could not read dashboard thread history for %s", thread_id, exc_info=True)
    person: PersonIdentity = {
        "id": sender_id,
        "platform": "github",
        "github_login": login,
    }
    if email:
        person["email"] = email
    structured = build_input_messages(
        content,
        {"sender_id": sender_id, "surface": "web", "kind": "human"},
        people=[person],
        systems=(
            [
                {
                    "id": "system:dashboard-handoff",
                    "display_name": "Dashboard handoff",
                    "platform": "open-swe",
                }
            ]
            if metadata.get("source") == "slack"
            else None
        ),
        injected_dynamic_context_hashes=injected,
    )
    if metadata.get("source") == "slack":
        structured.insert(
            -1,
            build_input_messages(
                DASHBOARD_HANDOFF_BODY,
                {
                    "sender_id": "system:dashboard-handoff",
                    "surface": "automation",
                    "kind": "system",
                },
                injected_dynamic_context_hashes={"system:dashboard-handoff"},
            )[0],
        )
    client_message_id = _command_message_id(params)
    if client_message_id and client_message_id not in persisted_message_ids:
        structured[-1]["id"] = client_message_id
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = structured
    metadata_update: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "plan_mode": plan_mode_requested,
        PARTICIPANT_LOGINS_KEY: merge_participant_logins(
            metadata.get(PARTICIPANT_LOGINS_KEY), login
        ),
        "injected_dynamic_context_hashes": sorted(injected),
    }
    if command_images and run_model and run_effort:
        overrides["agent_model_id"] = run_model
        overrides["agent_effort"] = run_effort
        metadata_update["model"] = run_model
        metadata_update["effort"] = run_effort
        metadata_update["resolved_model"] = run_model
        metadata_update["resolved_effort"] = run_effort
    elif chosen_model and chosen_effort:
        overrides["agent_model_id"] = chosen_model
        overrides["agent_effort"] = chosen_effort
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    if _is_thread_resolved(metadata):
        metadata_update["resolved"] = False
        metadata_update["resolved_at_ms"] = None
    metadata_update["updated_at_ms"] = _now_ms()
    metadata = {**metadata, **metadata_update}
    await client.threads.update(thread_id=thread_id, metadata=metadata)

    merged_configurable = await _build_dashboard_configurable(
        thread_id,
        login,
        metadata,
        overrides=overrides,
    )

    run_metadata = params.get("metadata")
    if not isinstance(run_metadata, dict):
        run_metadata = {}
    run_metadata = {
        **run_metadata,
        **_agent_version_metadata(),
        "prepare_run_id": prepare_run_id,
    }

    params["assistant_id"] = _ASSISTANT_ID
    params.setdefault("stream_mode", list(_DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": merged_configurable}
    params["metadata"] = run_metadata
    command["params"] = params
    return command


def _slack_thread_context(metadata: Mapping[str, Any]) -> JsonObject | None:
    source_context = metadata.get("source_context")
    if not isinstance(source_context, dict):
        return None
    slack_thread = source_context.get("slack_thread")
    return slack_thread if isinstance(slack_thread, dict) else None


async def _notify_slack_web_handoff(
    thread_id: str, metadata: Mapping[str, Any], client: Any
) -> None:
    if metadata.get("source") != "slack":
        return
    slack_thread = _slack_thread_context(metadata)
    if not slack_thread:
        return
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id:
        return
    if not isinstance(thread_ts, str) or not thread_ts:
        return

    trace_message_ts = slack_thread.get("trace_message_ts")
    if not isinstance(trace_message_ts, str) or not trace_message_ts:
        mapping = await lookup_slack_thread_run_mapping(client, channel_id, thread_ts)
        if isinstance(mapping, dict):
            candidate = mapping.get("trace_message_ts")
            if isinstance(candidate, str) and candidate:
                trace_message_ts = candidate
    if not isinstance(trace_message_ts, str) or not trace_message_ts:
        logger.info(
            "Skipping Slack web handoff update for thread %s: missing trace message ts", thread_id
        )
        return

    await update_slack_trace_reply_for_web_handoff(channel_id, trace_message_ts, thread_id)


async def send_dashboard_message(
    thread_id: str, login: str, body: ThreadMessageBody, *, email: str | None = None
) -> dict[str, Any]:
    row = await _registry_thread(thread_id, login, email=email)
    if row.environment != "cloud":
        raise HTTPException(409, "local messages must be sent through the assigned device")
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_postable(metadata, login, email)

    prompt = body.content.strip()
    now_ms = _now_ms()
    chosen_model, chosen_effort = normalize_model_choice(body.model_id, body.effort)
    handoff_metadata = dict(metadata)
    metadata_update: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "updated_at_ms": now_ms,
        "plan_mode": body.plan_mode,
        PARTICIPANT_LOGINS_KEY: merge_participant_logins(
            metadata.get(PARTICIPANT_LOGINS_KEY), login
        ),
    }
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    if _is_thread_resolved(metadata):
        metadata_update["resolved"] = False
        metadata_update["resolved_at_ms"] = None

    active = await get_thread_active_status(thread_id)
    if active is None:
        raise HTTPException(502, "could not determine whether thread is active")
    if not active:
        raise HTTPException(
            409,
            "thread is idle; start a run via the stream commands endpoint",
        )

    active_model = _metadata_model_id(metadata) if body.images else None
    content = _user_message_content(prompt, body.images, model_id=active_model)
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    queue_payload: dict[str, Any] = {
        "text": prompt,
        "source": _DASHBOARD_SOURCE,
        "surface": "web",
        "sender": {
            "id": f"github:{login}",
            "platform": "github",
            "github_login": login,
            **({"email": email} if email else {}),
        },
        "from_owner": _user_owns_thread(metadata, login, email),
    }
    if isinstance(content, list):
        queue_payload["images"] = [
            block for block in content if isinstance(block, dict) and block.get("type") != "text"
        ]
    queued = await queue_message_for_thread(thread_id, queue_payload)
    if not queued:
        raise HTTPException(502, "failed to queue follow-up message")
    try:
        await _notify_slack_web_handoff(thread_id, handoff_metadata, client)
    except Exception:
        logger.exception("Failed to update Slack message for dashboard handoff on %s", thread_id)
    registry_fields: dict[str, Any] = {
        "resolved": False,
        "resolved_at": None,
        "metadata": {**row.metadata, **metadata_update},
    }
    if chosen_model and chosen_effort:
        registry_fields.update(model=chosen_model, effort=chosen_effort)
    row = await (await get_thread_registry()).update_meta(thread_id, **registry_fields)
    return row.api_dict()


async def get_dashboard_thread_pull_request_status(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Return live GitHub health for every pull request tracked by the thread."""
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    records = metadata.get("pull_requests")
    tracked = list(records) if isinstance(records, list) else []
    if not tracked:
        pr_url = metadata.get("pr_url")
        pr_ref = parse_github_pr_url(pr_url) if isinstance(pr_url, str) else None
        if pr_ref:
            tracked = [
                {
                    "repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
                    "number": pr_ref.number,
                }
            ]
    if not tracked:
        return {"pullRequests": []}
    token = await _github_token_for_login(login)
    return {"pullRequests": await get_pull_request_statuses(tracked, token)}


async def _authorized_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadLike:
    await _registry_thread(thread_id, login, email=email)
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    return thread


async def _readable_thread(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> ThreadLike:
    if login is None:
        raise HTTPException(401, "authentication required")
    await _registry_thread(thread_id, login, email=email)
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    return thread


async def _readable_thread_metadata(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    metadata = thread_metadata(thread)
    return metadata


def _recovery_patch_filename(thread_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in thread_id)
    return f"open-swe-{(safe or 'thread')[:80]}.patch"


def _response_output(result: Any) -> str:
    output = result.get("output") if isinstance(result, dict) else getattr(result, "output", "")
    return output if isinstance(output, str) else str(output or "")


def _response_exit_code(result: Any) -> int | None:
    value = (
        result.get("exit_code") if isinstance(result, dict) else getattr(result, "exit_code", None)
    )
    return value if isinstance(value, int) else None


def _download_content(result: Any) -> bytes | None:
    for attr in ("content", "data", "bytes"):
        value = result.get(attr) if isinstance(result, dict) else getattr(result, attr, None)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
    file_data = (
        result.get("file_data") if isinstance(result, dict) else getattr(result, "file_data", None)
    )
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode()
    if isinstance(file_data, dict):
        for key in ("content", "data", "bytes"):
            value = file_data.get(key)
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode()
    return None


def _recovery_patch_command(metadata: Mapping[str, Any], thread_id: str) -> str:
    _, name, _ = _metadata_repo(metadata)
    payload = {
        "repo_name": name,
        "base_branch": metadata.get("base_branch")
        if isinstance(metadata.get("base_branch"), str)
        else "main",
        "thread_key": _recovery_patch_filename(thread_id).removesuffix(".patch"),
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = r"""python - <<'PY'
import base64
import json
import subprocess
import sys
from pathlib import Path

PAYLOAD = json.loads(base64.b64decode('__PAYLOAD__').decode())
WORKSPACE_FALLBACK = Path('/workspace')


def git(repo, args, check=True):
    result = subprocess.run(
        ['git', '-C', str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors='replace').strip()
        raise RuntimeError(detail or 'git ' + ' '.join(args) + ' failed')
    return result


def search_roots():
    roots = [Path.cwd().resolve(), WORKSPACE_FALLBACK]
    seen = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        if root.exists():
            yield root


def repo_paths():
    repo_name = PAYLOAD.get('repo_name')
    for root in search_roots():
        if isinstance(repo_name, str) and repo_name:
            yield root / Path(repo_name).name
        yield root
        for child in sorted(root.iterdir()):
            if child.is_dir():
                yield child


def find_repo():
    seen = set()
    for path in repo_paths():
        if path in seen:
            continue
        seen.add(path)
        if not (path / '.git').exists():
            continue
        result = git(path, ['rev-parse', '--show-toplevel'], check=False)
        if result.returncode == 0:
            root = Path(result.stdout.decode(errors='replace').strip())
            if root.exists():
                return root
    raise RuntimeError('no git repository found in sandbox workspace')


def safe_ref(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith('-') or '\x00' in value or '\n' in value or '\r' in value:
        return None
    return value


def commit_for(repo, ref):
    result = git(repo, ['rev-parse', '--verify', ref + '^{commit}'], check=False)
    if result.returncode == 0:
        return result.stdout.decode(errors='replace').strip()
    return None


def merge_base(repo):
    base_branch = safe_ref(PAYLOAD.get('base_branch')) or 'main'
    refs = ['origin/' + base_branch, base_branch, 'origin/main', 'main', 'origin/master', 'master', 'HEAD~1']
    for ref in refs:
        commit = commit_for(repo, ref)
        if not commit:
            continue
        result = git(repo, ['merge-base', 'HEAD', commit], check=False)
        if result.returncode == 0:
            return result.stdout.decode(errors='replace').strip()
        return commit
    return git(repo, ['hash-object', '-t', 'tree', '/dev/null']).stdout.decode(errors='replace').strip()


def write_patch(repo, base):
    patch_path = Path('/tmp') / ((PAYLOAD.get('thread_key') or 'open-swe-recovery') + '.patch')
    with patch_path.open('wb') as patch_file:
        tracked = git(repo, ['diff', '--binary', '--full-index', base, '--', '.']).stdout
        patch_file.write(tracked)
        untracked = git(repo, ['ls-files', '--others', '--exclude-standard', '-z']).stdout
        for raw_path in [p for p in untracked.split(b'\0') if p]:
            rel_path = raw_path.decode('utf-8', errors='surrogateescape')
            full_path = repo / rel_path
            if not full_path.is_file():
                continue
            result = git(
                repo,
                ['diff', '--no-index', '--binary', '--full-index', '--', '/dev/null', rel_path],
                check=False,
            )
            if result.returncode not in {0, 1}:
                detail = result.stderr.decode(errors='replace').strip()
                raise RuntimeError(detail or 'failed to diff untracked file ' + rel_path)
            if result.stdout:
                if patch_file.tell() and not result.stdout.startswith(b'\n'):
                    patch_file.write(b'\n')
                patch_file.write(result.stdout)
    return patch_path


try:
    repo = find_repo()
    base = merge_base(repo)
    patch_path = write_patch(repo, base)
    print(json.dumps({'ok': True, 'path': str(patch_path), 'size': patch_path.stat().st_size}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
    sys.exit(1)
PY"""
    return script.replace("__PAYLOAD__", encoded)


async def get_dashboard_thread_recovery_patch(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[bytes, str]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no recoverable sandbox")

    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not connect to sandbox %s for recovery", sandbox_id, exc_info=True)
        raise HTTPException(502, "could not connect to thread sandbox") from exc

    try:
        result = await sandbox.aexecute(
            _recovery_patch_command(metadata, thread_id),
            timeout=_RECOVERY_PATCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch generation failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    output = _response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.debug("Invalid recovery patch response for %s: %s", thread_id, output)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    if _response_exit_code(result) not in {0, None} or payload.get("ok") is not True:
        detail = payload.get("error") if isinstance(payload.get("error"), str) else None
        logger.debug("Recovery patch generation failed for %s: %s", thread_id, detail)
        raise HTTPException(502, detail or "failed to generate recovery patch")

    size = payload.get("size")
    if not isinstance(size, int):
        raise HTTPException(502, "failed to generate recovery patch")
    if size == 0:
        raise HTTPException(404, "thread has no recoverable changes")
    if size > _RECOVERY_PATCH_LIMIT_BYTES:
        raise HTTPException(413, "recovery patch is too large to download")

    patch_path = payload.get("path")
    if not isinstance(patch_path, str) or not patch_path.startswith("/tmp/"):
        raise HTTPException(502, "failed to generate recovery patch")

    try:
        downloads = await sandbox.adownload_files([patch_path])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch download failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to download recovery patch") from exc
    if not downloads:
        raise HTTPException(502, "failed to download recovery patch")
    content = _download_content(downloads[0])
    if content is None:
        raise HTTPException(502, "failed to download recovery patch")
    return content, _recovery_patch_filename(thread_id)


# No app-installation-token fallback: PR file contents must be fetched with
# the user's own credential so GitHub enforces their current repo access.
async def _github_token_for_login(login: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    return token


def _missing_diff() -> dict[str, Any]:
    return {
        "status": "missing",
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
    }


async def get_dashboard_thread_working_tree_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Return the sandbox's live working tree against HEAD."""
    from ..utils.sandbox_paths import aresolve_sandbox_work_dir
    from ..utils.turn_checkpoint import read_turn_diff

    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return _missing_diff()
    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not connect to sandbox %s for working tree diff", sandbox_id, exc_info=True
        )
        return _missing_diff()
    work_dir = await aresolve_sandbox_work_dir(sandbox)
    _, repo_name, _ = _metadata_repo(metadata)
    repo_path = posixpath.join(work_dir, repo_name) if repo_name else None
    return await read_turn_diff(sandbox, work_dir, "HEAD", None, repo_path=repo_path)


_UNSAFE_REF_CHARACTERS = set(" ~^:?*[\\\x7f") | {chr(code) for code in range(32)}


def _safe_git_ref(value: Any) -> str | None:
    """A branch name safe to place in a GitHub API path, or ``None``."""
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith("-") or value.startswith("/") or value.endswith("/"):
        return None
    if ".." in value or "@{" in value or value.endswith(".lock"):
        return None
    if any(character in _UNSAFE_REF_CHARACTERS for character in value):
        return None
    return value


async def get_dashboard_thread_branch_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Everything the thread's branch changes against its base.

    Served from GitHub rather than the sandbox, so it outlives the workspace.
    A thread with a pull request reads that PR; one without compares its branch
    to the base it was cut from, which is the same three-dot range the PR would
    eventually show.
    """
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    pr_number = metadata.get("pr_number")
    pr_ref = parse_github_pr_url(str(metadata.get("pr_url") or ""))
    _, _, full_name = _metadata_repo(metadata)
    if pr_ref and pr_ref.number == pr_number:
        full_name = f"{pr_ref.owner}/{pr_ref.repo}"
    if not full_name:
        raise HTTPException(404, "thread has no repository")
    pull_request: int | None = pr_number if isinstance(pr_number, int) else None

    base_ref = _safe_git_ref(metadata.get("base_branch")) or "main"
    head_ref = _safe_git_ref(metadata.get("branch_name"))
    if pull_request is None and head_ref == base_ref:
        raise HTTPException(404, "thread never branched off its base")

    token = await _github_token_for_login(login)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(headers=headers, timeout=_PROXY_REQUEST_TIMEOUT) as client:
        if pull_request is not None:
            diff = await build_pr_diff_files(client, full_name, pull_request)
        elif head_ref is not None:
            diff = await build_compare_diff_files(client, full_name, base_ref, head_ref)
        else:
            raise HTTPException(404, "thread has no branch")

    return {
        "prNumber": pull_request,
        "baseRef": base_ref,
        "headRef": head_ref,
        "baseSha": diff["base_sha"],
        "headSha": diff["head_sha"],
        "truncated": diff["truncated"],
        "files": diff["files"],
    }


async def proxy_dashboard_thread_stream_events(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> AsyncIterator[bytes]:
    # Preflight here (not in the generator) so auth/content-type failures
    # surface as real HTTP errors before the SSE response starts streaming.
    _require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    return _stream_thread_events(thread_id, body, content_type)


async def _stream_thread_events(
    thread_id: str,
    body: bytes,
    content_type: str,
) -> AsyncIterator[bytes]:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/stream/events"
    headers = _langgraph_proxy_headers(content_type=content_type, accept="text/event-stream")

    try:
        async with httpx.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    payload = {
                        "status": response.status_code,
                        "detail": error_body.decode(errors="replace") or response.reason_phrase,
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n".encode()
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception:
        logger.warning("LangGraph stream/events proxy closed for %s", thread_id, exc_info=True)


async def _observe_dashboard_run_ttft(
    thread_id: str,
    run_id: str,
    started_at_ms: int,
) -> None:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/stream"
    headers = _langgraph_proxy_headers(accept="text/event-stream")
    headers["Last-Event-ID"] = "-1"
    detector = AssistantTextEventDetector(run_id)
    try:
        async with httpx.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                params={"stream_mode": "messages"},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    for observation in detector.feed(chunk):
                        await record_dashboard_thread_ttft(
                            observation,
                            thread_id=thread_id,
                            started_at_ms=started_at_ms,
                        )
                        return
    except Exception:
        logger.warning(
            "Dashboard TTFT observer closed for run %s on thread %s",
            run_id,
            thread_id,
            exc_info=True,
        )


async def proxy_dashboard_thread_commands(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    received_at_ms = _now_ms()
    _require_json_content_type(content_type)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")

    method = parsed.get("method")
    registry = await get_thread_registry()
    registry_row = await registry.get(thread_id)
    if registry_row is not None:
        _assert_registry_owner(registry_row, login, email)
        if registry_row.environment != "cloud":
            raise HTTPException(409, "local threads execute on their assigned device")
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception:  # noqa: BLE001
        thread = None

    creating = False
    if thread is None:
        if method != "run.start":
            raise HTTPException(404, "thread not found")
        creating = True
        metadata = dict(registry_row.metadata) if registry_row else {}
    else:
        if registry_row is None:
            raise HTTPException(404, "thread not found")
        metadata = thread_metadata(thread)
    thread_busy = bool(registry_row and registry_row.status in {"queued", "running"})

    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/commands"
    headers = _langgraph_proxy_headers(content_type=content_type)

    enriched = await _enrich_run_start_command(
        thread_id,
        login,
        parsed,
        metadata=metadata,
        thread_busy=thread_busy,
        creating=creating,
        email=email,
    )
    outgoing = json.dumps(enriched).encode()

    if method == "run.start":
        params = enriched.get("params")
        if isinstance(params, dict):
            run_metadata = params.get("metadata")
            if not isinstance(run_metadata, dict):
                run_metadata = {}
                params["metadata"] = run_metadata
            run_metadata["dashboard_ttft_started_at_ms"] = received_at_ms
            outgoing = json.dumps(enriched).encode()

    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=outgoing, headers=headers)

    try:
        response_payload = json.loads(response.content) if response.content else None
    except json.JSONDecodeError:
        response_payload = None
    run_id = _extract_run_id_from_command_response(response_payload)
    run_start_succeeded = (
        parsed.get("method") == "run.start"
        and response.status_code in {200, 202, 204}
        and isinstance(response_payload, dict)
        and response_payload.get("type") == "success"
        and run_id is not None
    )
    if run_start_succeeded and not creating:
        try:
            await _notify_slack_web_handoff(thread_id, metadata, langgraph_client())
        except Exception:
            logger.exception(
                "Failed to update Slack message for dashboard handoff on %s", thread_id
            )

    if run_start_succeeded and run_id is not None:
        try:
            await registry.transition(
                thread_id,
                run_id,
                "queued",
                environment="cloud",
                guard_run_id=registry_row.status_run_id if registry_row else None,
            )
        except (KeyError, ValueError):
            logger.exception(
                "Failed to record queued dashboard run %s on thread %s", run_id, thread_id
            )
        task = asyncio.create_task(
            _observe_dashboard_run_ttft(
                thread_id,
                run_id,
                received_at_ms,
            )
        )
        _TTFT_OBSERVER_TASKS.add(task)
        task.add_done_callback(_TTFT_OBSERVER_TASKS.discard)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_history(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    _require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "history body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "history body must be a JSON object")
    limit = payload.get("limit", _DISCOVERY_HISTORY_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise HTTPException(400, "history limit must be a positive integer")
    if not any(payload.get(key) for key in ("before", "checkpoint", "metadata")):
        payload["limit"] = min(limit, _DISCOVERY_HISTORY_LIMIT)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/history"
    headers = _langgraph_proxy_headers(content_type=content_type)
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    row = await _registry_thread(thread_id, login, email=email)
    if row.environment != "cloud":
        raise HTTPException(409, "local threads execute on their assigned device")
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/cancel"
    headers = _langgraph_proxy_headers()
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=headers,
            params={"wait": wait, "action": action},
        )
    if response.status_code in {200, 202, 204}:
        try:
            await (await get_thread_registry()).transition(
                thread_id,
                run_id,
                "interrupted",
                environment="cloud",
            )
        except (KeyError, ValueError):
            logger.debug("Cancelled run %s is no longer current for %s", run_id, thread_id)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def stream_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None, last_event_id: str | None = None
) -> AsyncIterator[str]:
    row = await _registry_thread(thread_id, login, email=email)
    if row.environment != "cloud":
        raise HTTPException(409, "local streams are served by the assigned device")

    stream = await langgraph_client().threads.join_stream(
        thread_id,
        last_event_id=last_event_id,
    )
    async for part in stream:
        event = getattr(part, "event", None) or (
            part.get("event") if isinstance(part, dict) else None
        )
        data = getattr(part, "data", None) if not isinstance(part, dict) else part.get("data")
        event_id = getattr(part, "id", None) if not isinstance(part, dict) else part.get("id")
        payload: dict[str, Any] = {"event": event, "data": data}
        if event_id is not None:
            payload["id"] = event_id
        chunk = f"data: {json.dumps(payload, default=str)}\n\n"
        if event_id is not None:
            chunk = f"id: {event_id}\n{chunk}"
        yield chunk


# Registry-backed lifecycle API. LangGraph endpoints above remain execution-only.
def _assert_registry_owner(row: ThreadRow, login: str, email: str | None = None) -> None:
    if row.owner_login == login:
        return
    if email and row.owner_email and row.owner_email.lower() == email.lower():
        return
    raise HTTPException(404, "thread not found")


async def _registry_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadRow:
    row = await (await get_thread_registry()).get(thread_id)
    if row is None:
        raise HTTPException(404, "thread not found")
    _assert_registry_owner(row, login, email)
    return row


async def create_dashboard_registry_thread(
    login: str,
    body: ThreadCreateBody,
    *,
    email: str | None = None,
) -> dict[str, Any]:
    if body.environment == "local" and not body.device_id:
        raise HTTPException(422, "local threads require device_id")
    repo = _parse_repo(body.repo)
    if body.repo and repo is None:
        raise HTTPException(422, "repo must be in owner/name form")
    thread_id = body.id or str(uuid.uuid4())
    registry = await get_thread_registry()
    existing = await registry.get(thread_id)
    if existing is not None:
        _assert_registry_owner(existing, login, email)
        raise HTTPException(409, "thread already exists")
    row = await registry.create(
        ThreadCreate(
            id=thread_id,
            owner_login=login,
            owner_email=email,
            title=body.title,
            repo_full_name=(f"{repo['owner']}/{repo['name']}" if repo else None),
            branch=body.branch,
            environment=body.environment,
            device_id=body.device_id,
            device_name=body.device_name,
            model=body.model,
            effort=body.effort,
            metadata={"execution_environment": body.environment},
        )
    )
    if body.environment == "cloud":
        metadata = {
            "github_login": login,
            "triggering_user_email": email,
            "title": body.title,
            "source": "dashboard",
            "execution_environment": "cloud",
            "repo_owner": repo.get("owner") if repo else None,
            "repo_name": repo.get("name") if repo else None,
            "base_branch": body.branch or "main",
            "model": body.model,
            "effort": body.effort,
        }
        try:
            await langgraph_client().threads.create(
                thread_id=thread_id, metadata=metadata, if_exists="do_nothing"
            )
        except Exception as exc:  # noqa: BLE001
            await registry.delete(thread_id)
            raise HTTPException(502, "could not create execution thread") from exc
    return row.api_dict()


async def patch_dashboard_registry_thread(
    thread_id: str,
    login: str,
    body: ThreadPatchBody,
    *,
    email: str | None = None,
) -> dict[str, Any]:
    row = await _registry_thread(thread_id, login, email=email)
    fields = body.model_dump(exclude_unset=True)
    if "resolved" in fields:
        fields["resolved_at"] = utcnow() if fields["resolved"] else None
    if fields.get("viewed_run_id") is None and "viewed_run_id" in fields:
        fields["viewed_run_id"] = row.last_finished_run_id
    try:
        updated = await (await get_thread_registry()).update_meta(thread_id, **fields)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return updated.api_dict()


async def list_dashboard_threads(
    login: str, *, email: str | None = None, limit: int = 50, include_all: bool = False
) -> list[dict[str, Any]]:
    page = await (await get_thread_registry()).list(
        None if include_all else login,
        limit=min(max(limit, 1), 200),
    )
    return [row.api_dict(is_owner=row.owner_login == login) for row in page.items]


async def list_dashboard_threads_sidebar(
    login: str,
    *,
    email: str | None = None,
    active_limit: int = 50,
    resolved_limit: int = 20,
    active_thread_id: str | None = None,
    include_automations: bool = False,
    include_all: bool = False,
    timings: dict[str, float] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    del email, active_thread_id, timings, counts
    registry = await get_thread_registry()
    owner = None if include_all else login
    scope = "all" if include_automations else "interactive"
    active, resolved = await asyncio.gather(
        registry.list(owner, resolved=False, scope=scope, limit=active_limit),
        registry.list(owner, resolved=True, scope=scope, limit=resolved_limit),
    )
    return {
        "active": {
            "items": [row.api_dict(is_owner=row.owner_login == login) for row in active.items],
            "ids": [row.id for row in active.items],
            "cursor": active.cursor,
            "limit": min(max(active_limit, 1), 200),
            "hasMore": active.has_more,
        },
        "resolved": {
            "items": [row.api_dict(is_owner=row.owner_login == login) for row in resolved.items],
            "ids": [row.id for row in resolved.items],
            "cursor": resolved.cursor,
            "limit": min(max(resolved_limit, 1), 200),
            "hasMore": resolved.has_more,
        },
    }


async def list_dashboard_threads_page(
    login: str,
    *,
    email: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    include_all: bool = False,
    resolved: bool | None = None,
    viewed: bool | None = None,
    environment: str | None = None,
    source: str | None = None,
    status: str | None = None,
    query: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    filter_owner_login: str | None = None,
    surfaced_only: bool = False,
    sort_by: Literal["created_at", "updated_at"] = "updated_at",
    offset: int | None = None,
) -> dict[str, Any]:
    del email, surfaced_only, sort_by
    if offset not in (None, 0):
        raise HTTPException(400, "offset pagination was removed; use cursor")
    owner = None if include_all else (filter_owner_login or login)
    try:
        page = await (await get_thread_registry()).list(
            owner,
            resolved=resolved,
            viewed=viewed,
            environment=environment,
            source=source,
            status=status,
            q=query,
            scope=scope,
            automation_id=automation_id,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "items": [row.api_dict(is_owner=row.owner_login == login) for row in page.items],
        "ids": [row.id for row in page.items],
        "limit": min(max(limit, 1), 200),
        "cursor": page.cursor,
        "hasMore": page.has_more,
    }


async def get_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None, mark_viewed: bool = True
) -> dict[str, Any]:
    row = await _registry_thread(thread_id, login, email=email)
    if mark_viewed and row.status not in {"queued", "running"} and not row.viewed:
        row = await (await get_thread_registry()).update_meta(
            thread_id, viewed_run_id=row.last_finished_run_id
        )
    return row.api_dict()


async def get_dashboard_thread_messages(
    thread_id: str,
    login: str,
    *,
    email: str | None = None,
    after_seq: int = 0,
) -> dict[str, Any]:
    await _registry_thread(thread_id, login, email=email)
    items = await (await get_thread_registry()).get_messages(thread_id, after_seq=max(0, after_seq))
    return {"items": items, "lastSeq": items[-1]["seq"] if items else after_seq}


async def delete_dashboard_thread(thread_id: str, login: str, *, email: str | None = None) -> None:
    row = await _registry_thread(thread_id, login, email=email)
    if row.status in {"queued", "running"} and row.status_run_id:
        raise HTTPException(409, "stop the thread before deleting it")
    if row.environment == "cloud":
        try:
            await langgraph_client().threads.delete(thread_id)
        except Exception:
            logger.debug("Execution thread %s was already absent", thread_id, exc_info=True)
    await (await get_thread_registry()).delete(thread_id)


async def resolve_dashboard_thread(
    thread_id: str, login: str, *, resolved: bool, email: str | None = None
) -> dict[str, Any]:
    return await patch_dashboard_registry_thread(
        thread_id, login, ThreadPatchBody(resolved=resolved), email=email
    )


async def cancel_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    row = await _registry_thread(thread_id, login, email=email)
    run_id = row.status_run_id
    if run_id and row.status in {"queued", "running"}:
        if row.environment == "cloud":
            try:
                await langgraph_client().runs.cancel(
                    thread_id, run_id, wait=False, action="interrupt"
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(502, "failed to request thread cancellation") from exc
        row = await (await get_thread_registry()).transition(
            thread_id,
            run_id,
            "interrupted",
            environment=cast(ThreadEnvironment, row.environment),
            device_id=row.device_id,
        )
    return row.api_dict()


async def admin_cancel_dashboard_thread(thread_id: str) -> dict[str, Any]:
    row = await (await get_thread_registry()).get(thread_id)
    if row is None:
        raise HTTPException(404, "thread not found")
    if row.status_run_id and row.status in {"queued", "running"}:
        if row.environment == "cloud":
            await langgraph_client().runs.cancel(
                thread_id, row.status_run_id, wait=False, action="interrupt"
            )
        row = await (await get_thread_registry()).transition(
            thread_id,
            row.status_run_id,
            "interrupted",
            environment=cast(ThreadEnvironment, row.environment),
            device_id=row.device_id,
        )
    return row.api_dict()


async def get_dashboard_thread_state(
    thread_id: str,
    login: str,
    *,
    email: str | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    del timings
    row = await _registry_thread(thread_id, login, email=email)
    if row.environment != "cloud":
        raise HTTPException(409, "local thread state is served by its device")
    try:
        state = await langgraph_client().threads.get_state(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "execution state is unavailable") from exc
    result = as_json_object(state)
    if row.status in {"queued", "running"}:
        result.pop("next", None)
    return result


async def report_local_run(
    login: str,
    body: LocalReportBody,
    *,
    email: str | None = None,
) -> dict[str, Any]:
    registry = await get_thread_registry()
    await registry.record_heartbeat(body.device_id, login, body.device_name)
    if body.thread_id is None:
        return {"deviceId": body.device_id, "deviceName": body.device_name}
    row = await _registry_thread(body.thread_id, login, email=email)
    if row.environment != "local" or row.device_id != body.device_id:
        raise HTTPException(409, "thread is not assigned to this device")
    if body.status is not None:
        if not body.run_id:
            raise HTTPException(422, "run_id is required with status")
        try:
            row = await registry.transition(
                body.thread_id,
                body.run_id,
                body.status,
                environment="local",
                device_id=body.device_id,
                error=body.error,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    if body.messages:
        await registry.append_messages(body.thread_id, body.run_id, messages_to_ui(body.messages))
    if body.git_checkpoint is not None:
        checkpoint = body.git_checkpoint
        if not all(
            isinstance(checkpoint.get(key), str) and checkpoint[key]
            for key in ("repo", "ref", "branch")
        ) or not isinstance(checkpoint.get("pushed"), bool):
            raise HTTPException(422, "invalid git checkpoint")
        row = await registry.update_meta(body.thread_id, git_checkpoint=checkpoint)
    return row.api_dict()


async def _cloud_handoff_checkpoint(row: ThreadRow) -> dict[str, Any]:
    if not row.sandbox_id or not row.repo_full_name:
        if row.git_checkpoint and row.git_checkpoint.get("pushed") is True:
            return row.git_checkpoint
        raise HTTPException(409, "cloud work is not available to checkpoint")
    from ..utils.sandbox_paths import aresolve_sandbox_work_dir

    repo_name = row.repo_full_name.split("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo_name):
        raise HTTPException(422, "invalid repository name")
    branch = row.branch or f"open-swe/handoff-{row.id[:12]}"
    if _safe_git_ref(branch) is None:
        branch = f"open-swe/handoff-{re.sub(r'[^A-Za-z0-9._-]', '-', row.id)[:12]}"
    try:
        sandbox = await create_sandbox(row.sandbox_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox)
        repo_dir = posixpath.join(work_dir, repo_name)
        command = " && ".join(
            (
                f"cd {shlex.quote(repo_dir)}",
                "git add -A -- .",
                "git diff --cached --quiet || git commit -m 'Open SWE handoff checkpoint'",
                f"git push origin HEAD:refs/heads/{shlex.quote(branch)}",
                "git rev-parse HEAD",
            )
        )
        result = await sandbox.aexecute(command, timeout=120)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            409, "could not commit and push cloud work; check repository write access"
        ) from exc
    output = _response_output(result).strip().splitlines()
    commit = output[-1] if output else ""
    if _response_exit_code(result) not in {0, None} or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise HTTPException(409, "could not create a pushed cloud checkpoint")
    return {
        "repo": row.repo_full_name,
        "ref": commit,
        "branch": branch,
        "pushed": True,
    }


async def _seed_cloud_handoff(row: ThreadRow, checkpoint: Mapping[str, Any]) -> None:
    owner, repo = row.repo_full_name.split("/", 1) if row.repo_full_name else ("", "")
    metadata = {
        **row.metadata,
        "github_login": row.owner_login,
        "triggering_user_email": row.owner_email,
        "source": row.source,
        "execution_environment": "cloud",
        "repo_owner": owner,
        "repo_name": repo,
        "base_branch": checkpoint["branch"],
        "branch_name": checkpoint["branch"],
        "git_checkpoint_ref": checkpoint["ref"],
        "resumed_from_environment": "local",
    }
    client = langgraph_client()
    await client.threads.create(thread_id=row.id, metadata=metadata, if_exists="do_nothing")
    messages = await (await get_thread_registry()).get_messages(row.id)
    seeded = ui_messages_to_state(messages)
    if seeded:
        await client.threads.update_state(row.id, values={"messages": seeded})


async def handoff_dashboard_thread(
    thread_id: str,
    login: str,
    body: ThreadHandoffBody,
    *,
    email: str | None = None,
) -> dict[str, Any]:
    row = await _registry_thread(thread_id, login, email=email)
    if row.status in {"queued", "running"}:
        raise HTTPException(409, "interrupt the active run before handing off")
    if not row.repo_full_name:
        raise HTTPException(422, "repo-less threads cannot be handed off")
    if body.target == row.environment:
        return row.api_dict()
    target_device: dict[str, Any] | None = None
    if body.target == "local":
        if not body.device_id:
            raise HTTPException(422, "device_id is required for a local handoff")
        target_device = await (await get_thread_registry()).device(body.device_id, login)
        if target_device is None:
            raise HTTPException(404, "target device has not checked in")
        last_seen = _as_utc(target_device.get("last_seen_at"))
        if last_seen is None or (utcnow() - last_seen).total_seconds() > 300:
            raise HTTPException(409, "target device is offline")
    checkpoint = (
        await _cloud_handoff_checkpoint(row)
        if row.environment == "cloud"
        else body.git_checkpoint or row.git_checkpoint
    )
    if not isinstance(checkpoint, dict) or checkpoint.get("pushed") is not True:
        raise HTTPException(409, "commit and push the current work before handing off")
    if not all(
        isinstance(checkpoint.get(key), str) and checkpoint[key]
        for key in ("repo", "ref", "branch")
    ):
        raise HTTPException(422, "invalid git checkpoint")
    if checkpoint["repo"] != row.repo_full_name:
        raise HTTPException(422, "git checkpoint repository does not match thread repository")
    fields: dict[str, Any] = {
        "environment": body.target,
        "git_checkpoint": checkpoint,
        "branch": checkpoint["branch"],
        "device_id": None,
        "device_name": None,
    }
    if body.target == "local":
        assert target_device is not None
        fields.update(
            device_id=body.device_id,
            device_name=body.device_name or target_device["name"],
        )
    else:
        try:
            await _seed_cloud_handoff(row, checkpoint)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not seed cloud execution for handoff %s", thread_id)
            raise HTTPException(502, "could not prepare cloud execution for handoff") from exc
        fields["sandbox_id"] = None
    updated = await (await get_thread_registry()).update_meta(thread_id, **fields)
    return updated.api_dict()


async def stream_thread_registry_events(
    login: str,
    *,
    cursor: int = 0,
    include_all: bool = False,
) -> AsyncIterator[str]:
    registry = await get_thread_registry()
    current = max(0, cursor)
    owner = None if include_all else login
    while True:
        events = await registry.events_since(current, owner)
        if not events:
            yield ": heartbeat\n\n"
            await registry.wait_for_events(15.0)
            continue
        for event in events:
            current = event.id
            yield f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps(event.api_dict(), default=str)}\n\n"
