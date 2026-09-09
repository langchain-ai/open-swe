"""Turning a dashboard request into a LangGraph run: bodies, models, images, commands."""

import base64
import binascii
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from pydantic import BaseModel, ConfigDict, Field

from agent.dashboard.admin import is_admin
from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.environments import ENVIRONMENTS, slugify
from agent.dashboard.options import (
    DEPRECATED_MODEL_IDS,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_images,
    normalize_model_choice,
)
from agent.dashboard.profiles import get_profile
from agent.dashboard.team_settings import get_team_default_model, get_team_fable_enabled
from agent.dashboard.threads.access import (
    _ensure_dashboard_github_token,
    agent_version_metadata,
    resolve_run_email,
)
from agent.dashboard.threads.summary import (
    _DASHBOARD_SOURCE,
    _is_thread_resolved,
    _metadata_model_id,
    _now_ms,
    _parse_repo,
    repo_config_from_metadata,
    thread_source,
)
from agent.input_messages import (
    PersonIdentity,
    build_input_messages,
    dynamic_context_hashes_from_messages,
    injected_dynamic_context_hashes_from_metadata,
)
from agent.slack.client import (
    lookup_slack_thread_run_mapping,
    update_slack_trace_reply_for_web_handoff,
)
from agent.source_context import SourceContext
from agent.utils.dashboard_handoff import DASHBOARD_HANDOFF_BODY
from agent.utils.json_types import JsonObject, as_thread_dict, thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    merge_participants,
)
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
# Modes required for the v3 event-stream protocol (`POST …/stream/events`).
DASHBOARD_STREAM_MODES: tuple[str, ...] = (
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
    client_message_id: uuid.UUID | None = None


class ThreadRenameBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)


class ThreadResolveBody(BaseModel):
    resolved: bool = True


async def _resolve_agent_model_choice(
    profile: dict[str, Any],
    model_id: str | None,
    effort: str | None,
) -> tuple[str, str]:
    resolved_model, resolved_effort = await get_team_default_model("agent")
    if model_id not in DEPRECATED_MODEL_IDS:
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
    return slug if await ENVIRONMENTS.get(slug) is not None else None


def _resolve_repo_config(repo: str | None) -> dict[str, str]:
    """Resolve the run's repo from the request, or ``{}`` when none is given."""
    return _parse_repo(repo) or {}


async def _create_dashboard_thread_record(
    thread_id: str,
    *,
    login: str,
    email: str | None = None,
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
        PARTICIPANT_LOGINS_KEY: merge_participants(None, login),
        PARTICIPANT_EMAILS_KEY: merge_participants(None, email),
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
    thread = await client.threads.get(thread_id)
    return as_thread_dict(thread)


async def _build_dashboard_configurable(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile if profile is not None else await get_profile(login) or {}
    source = thread_source(metadata)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": source,
        "github_login": login,
        "user_email": await resolve_run_email(login, profile),
    }
    repo_config = repo_config_from_metadata(metadata)
    if repo_config:
        configurable["repo"] = repo_config
    elif metadata.get("repo_explicitly_none") is True:
        configurable["repo_explicitly_none"] = True
    for key, value in SourceContext.from_metadata(metadata).dump().items():
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
            email=email,
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
        PARTICIPANT_LOGINS_KEY: merge_participants(metadata.get(PARTICIPANT_LOGINS_KEY), login),
        PARTICIPANT_EMAILS_KEY: merge_participants(metadata.get(PARTICIPANT_EMAILS_KEY), email),
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
    metadata_update["updated_at_ms"] = _now_ms()
    pr_linked = any(metadata.get(key) for key in ("pr_url", "pr_urls", "pull_requests"))
    if not creating and (pr_linked or metadata.get("auto_resolved_by_prs") is True):
        async with agent_thread_pr_state_lock(client, thread_id):
            current = await client.threads.get(thread_id)
            metadata = thread_metadata(current)
            if _is_thread_resolved(metadata):
                metadata_update["resolved"] = False
                metadata_update["resolved_at_ms"] = None
            if metadata.get("auto_resolved_by_prs") is True:
                metadata_update["auto_resolved_by_prs"] = False
            if metadata.get("attention_reason"):
                metadata_update["attention_reason"] = None
            metadata = {**metadata, **metadata_update}
            await client.threads.update(thread_id=thread_id, metadata=metadata)
    else:
        if _is_thread_resolved(metadata):
            metadata_update["resolved"] = False
            metadata_update["resolved_at_ms"] = None
        if metadata.get("attention_reason"):
            metadata_update["attention_reason"] = None
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
        **agent_version_metadata(),
        "prepare_run_id": prepare_run_id,
    }

    params["assistant_id"] = _ASSISTANT_ID
    params.setdefault("stream_mode", list(DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": merged_configurable}
    params["metadata"] = run_metadata
    command["params"] = params
    return command


def _slack_thread_context(metadata: Mapping[str, Any]) -> JsonObject | None:
    context = SourceContext.from_metadata(metadata)
    if context.slack_thread is None:
        return None
    return context.dump()["slack_thread"]


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
