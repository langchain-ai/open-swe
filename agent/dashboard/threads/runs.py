"""Starting, feeding, and stopping a dashboard thread's runs.

A message from the browser arrives one of two ways: as a ``run.start`` command
on an idle thread, or — when a run is already in flight — queued onto the
running one. Both land here, and both go through the same resolution: which
model, which repo, which environment, who is speaking, and what context the
agent still needs injected. The SDK proxy endpoints sit at the top of this
module because the enrichment they perform *is* that resolution; the transport
they use is :mod:`.proxy`.
"""

import base64
import binascii
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from langgraph_sdk.schema import Run
from pydantic import BaseModel, ConfigDict, Field

from ...config import agent_version_metadata, langgraph_client
from ...dispatch import dispatch_agent_run
from ...input_messages import (
    PersonIdentity,
    RunMessage,
    build_input_messages,
    dynamic_context_hashes_from_messages,
    injected_dynamic_context_hashes_from_metadata,
)
from ...store import now_ms
from ...utils.dashboard_handoff import DASHBOARD_HANDOFF_BODY
from ...utils.json_types import (
    JsonObject,
    as_json_object,
    as_thread_dict,
    thread_metadata,
)
from ...utils.run_metadata import resolve_run_email
from ...utils.slack_api import update_slack_trace_reply_for_web_handoff
from ...utils.slack_threads import lookup_slack_thread_run_mapping
from ...utils.thread_ops import get_thread_active_status, queue_message_for_thread
from ...utils.thread_participants import PARTICIPANT_LOGINS_KEY, merge_participant_logins
from ...utils.timing import phase
from ..admin import is_admin
from ..agent_overrides import normalize_profile_overrides
from ..authz import (
    DASHBOARD_SOURCE,
    assert_thread_owner,
    assert_thread_postable,
    assert_thread_readable,
    get_owned_thread,
    get_owned_thread_metadata,
    get_readable_thread_metadata,
    thread_owner_email,
    thread_owner_login,
    thread_source,
    user_owns_thread,
)
from ..environments import get_environment, slugify
from ..github_tokens import get_valid_access_token
from ..options import (
    default_vision_model_pair,
    gate_fable_model,
    model_supports_images,
    normalize_model_choice,
)
from ..profiles import get_profile
from ..team_settings import get_team_default_model, get_team_fable_enabled
from .listing import refresh_latest_run_metadata
from .proxy import (
    PROXY_STREAM_MODES,
    passthrough,
    proxy_commands,
    require_json_content_type,
    spawn_ttft_observer,
    stream_thread_events,
)
from .serialize import (
    is_thread_resolved,
    metadata_model_id,
    repo_config_from_metadata,
    slack_thread_context,
    slack_thread_ids,
    thread_is_busy,
    thread_summary,
)

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_MAX_DASHBOARD_IMAGES = 5
_MAX_DASHBOARD_IMAGE_BYTES = 10 * 1024 * 1024
_DISCOVERY_HISTORY_LIMIT = 5
_THREAD_POST_COMMAND_METHODS = frozenset(
    {"run.start", "input.respond", "input.inject", "state.fork"}
)


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


def resolve_repo_config(repo: str | None) -> dict[str, str]:
    """Resolve the run's repo from the request, or ``{}`` when none is given."""
    return _parse_repo(repo) or {}


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


async def create_dashboard_thread_record(
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
) -> JsonObject:
    """Create or update dashboard thread metadata without starting a run."""
    profile = await get_profile(login) or {}
    created_at_ms = now_ms()
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
        "source": DASHBOARD_SOURCE,
        "origin": DASHBOARD_SOURCE,
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
        "created_at_ms": created_at_ms,
        "updated_at_ms": created_at_ms,
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


async def build_thread_configurable(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The run config for a thread — the only place one is assembled.

    Every run on a dashboard thread (a browser ``run.start``, a plan approval, a
    workflow-push approval) goes through here, so none of them can quietly drop
    the environment, admin intent, or source context the others pass.
    """
    profile = profile if profile is not None else await get_profile(login) or {}
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": thread_source(metadata),
        "github_login": login,
        # A thread started from Slack/Linear may know only the triggering email,
        # so it stands in when the login resolves to nothing.
        "user_email": await resolve_run_email(login, profile) or thread_owner_email(metadata),
    }
    repo_config = repo_config_from_metadata(metadata)
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


async def dispatch_thread_followup(
    thread_id: str, metadata: Mapping[str, Any], text: str, *, plan_mode: bool
) -> Run:
    """Continue an existing thread with a decision as a new instruction run."""
    login = thread_owner_login(metadata) or ""
    configurable = await build_thread_configurable(
        thread_id,
        login,
        metadata,
        overrides={"plan_mode": plan_mode},
    )
    return await dispatch_agent_run(
        thread_id,
        text,
        configurable,
        source=configurable["source"],
    )


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
    the shared ``create_dashboard_thread_record`` validate size/type/model.
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


@dataclass(frozen=True)
class _RunStartRequest:
    """What the browser asked for in one ``run.start``, read once."""

    configurable: dict[str, Any]
    content: Any
    images: list[DashboardImageBody]
    model: str | None
    effort: str | None
    plan_mode: bool

    @classmethod
    def read(
        cls, params: dict[str, Any], client_configurable: dict[str, Any]
    ) -> "_RunStartRequest":
        content = _command_message_content(params)
        model, effort = normalize_model_choice(
            client_configurable.get("agent_model_id"),
            client_configurable.get("agent_effort"),
        )
        return cls(
            configurable=client_configurable,
            content=content,
            images=_dashboard_images_from_content(content),
            model=model,
            effort=effort,
            plan_mode=client_configurable.get("plan_mode") is True,
        )


async def _stamp_new_thread(
    thread_id: str, login: str, request: _RunStartRequest, *, email: str | None
) -> tuple[JsonObject, dict[str, Any]]:
    """Own a client-minted thread id on its first run, and pick its model.

    Stamps the full dashboard thread record (owner, title, repo, model) and
    validates any attached images against the resolved model before the run is
    forwarded to LangGraph. The repo hint rides in the client configurable; it
    never reaches the run config, which is rebuilt from the stamped metadata.
    """
    thread = await create_dashboard_thread_record(
        thread_id,
        login=login,
        repo_config=resolve_repo_config(request.configurable.get("repo")),
        repo_explicitly_none=request.configurable.get("repo_explicitly_none") is True,
        prompt=_command_prompt_text(request.content),
        images=request.images,
        model_id=request.configurable.get("agent_model_id"),
        effort=request.configurable.get("agent_effort"),
        plan_mode=request.plan_mode,
        admin_thread=(
            request.configurable.get("admin_thread") is True and is_admin(email, login=login)
        ),
        environment=await _resolve_requested_environment(request.configurable.get("environment")),
    )
    metadata = thread_metadata(thread)
    if request.images:
        # The record may have swapped in a vision-capable model; the run has to
        # follow it, not the selection that could not read the image.
        resolved_model = metadata.get("resolved_model")
        resolved_effort = metadata.get("resolved_effort")
        if isinstance(resolved_model, str) and isinstance(resolved_effort, str):
            return metadata, {"agent_model_id": resolved_model, "agent_effort": resolved_effort}
        return metadata, {}
    if request.model and request.effort:
        return metadata, {"agent_model_id": request.model, "agent_effort": request.effort}
    return metadata, {}


def _run_model_for_thread(
    request: _RunStartRequest, metadata: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """The model this run should use: the request's, else the thread's own."""
    model = request.model or metadata_model_id(metadata)
    effort = request.effort
    if not effort:
        for key in ("resolved_effort", "effort"):
            value = metadata.get(key)
            if isinstance(value, str):
                effort = value
                break
    if request.images and model and effort:
        return _with_vision_fallback(model, effort, has_images=True)
    return model, effort


async def _structured_input_messages(
    thread_id: str,
    login: str,
    params: dict[str, Any],
    metadata: Mapping[str, Any],
    content: Any,
    *,
    email: str | None,
) -> tuple[list[RunMessage], set[str]]:
    """The user's message as agent input, plus the dynamic-context hashes it carries.

    Reads the thread's existing messages first: context already injected must not
    be injected twice, and the id the SDK minted for the submitted message has to
    survive so the optimistic bubble reconciles with the server's echo.
    """
    sender_id = f"github:{login}"
    injected = injected_dynamic_context_hashes_from_metadata(metadata)
    persisted_message_ids: set[str] = set()
    try:
        prior_state = await langgraph_client().threads.get_state(thread_id)
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

    person: PersonIdentity = {"id": sender_id, "platform": "github", "github_login": login}
    if email:
        person["email"] = email
    from_slack = metadata.get("source") == "slack"
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
            if from_slack
            else None
        ),
        injected_dynamic_context_hashes=injected,
    )
    if from_slack:
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
    return structured, injected


async def _append_run_to_thread(
    thread_id: str,
    login: str,
    params: dict[str, Any],
    metadata: JsonObject,
    request: _RunStartRequest,
    *,
    email: str | None,
) -> tuple[JsonObject, dict[str, Any]]:
    """Turn a follow-up ``run.start`` into agent input, and re-stamp the thread."""
    run_model, run_effort = _run_model_for_thread(request, metadata)
    _validate_command_images(request.content, model_id=run_model)
    structured, injected = await _structured_input_messages(
        thread_id,
        login,
        params,
        metadata,
        request.content if request.content is not None else "",
        email=email,
    )
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = structured

    overrides: dict[str, Any] = {}
    metadata_update: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        "plan_mode": request.plan_mode,
        PARTICIPANT_LOGINS_KEY: merge_participant_logins(
            metadata.get(PARTICIPANT_LOGINS_KEY), login
        ),
        "injected_dynamic_context_hashes": sorted(injected),
    }
    if request.images and run_model and run_effort:
        overrides = {"agent_model_id": run_model, "agent_effort": run_effort}
        metadata_update["model"] = run_model
        metadata_update["effort"] = run_effort
        metadata_update["resolved_model"] = run_model
        metadata_update["resolved_effort"] = run_effort
    elif request.model and request.effort:
        overrides = {"agent_model_id": request.model, "agent_effort": request.effort}
        metadata_update["model"] = request.model
        metadata_update["effort"] = request.effort
    if is_thread_resolved(metadata):
        metadata_update["resolved"] = False
        metadata_update["resolved_at_ms"] = None
    metadata_update["updated_at_ms"] = now_ms()
    metadata = {**metadata, **metadata_update}
    await langgraph_client().threads.update(thread_id=thread_id, metadata=metadata)
    return metadata, overrides


async def enrich_run_start_command(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    thread_busy: bool = False,
    creating: bool = False,
    email: str | None = None,
) -> dict[str, Any]:
    """Rewrite a browser ``run.start`` into the run the agent should actually get."""
    if command.get("method") != "run.start":
        return command

    if thread_busy:
        raise HTTPException(409, "thread is already running; queue message instead")

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
    request = _RunStartRequest.read(params, client_configurable)

    if creating:
        metadata, model_overrides = await _stamp_new_thread(thread_id, login, request, email=email)
    else:
        metadata, model_overrides = await _append_run_to_thread(
            thread_id, login, params, metadata, request, email=email
        )

    prepare_run_id = str(uuid.uuid4())
    merged_configurable = await build_thread_configurable(
        thread_id,
        login,
        metadata,
        overrides={"prepare_run_id": prepare_run_id, **model_overrides},
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
    params.setdefault("stream_mode", list(PROXY_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": merged_configurable}
    params["metadata"] = run_metadata
    command["params"] = params
    return command


async def _notify_slack_web_handoff(
    thread_id: str, metadata: Mapping[str, Any], client: Any
) -> None:
    if metadata.get("source") != "slack":
        return
    slack_thread = slack_thread_context(metadata)
    ids = slack_thread_ids(slack_thread)
    if slack_thread is None or ids is None:
        return
    channel_id, thread_ts = ids

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
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    assert_thread_postable(metadata, login, email)

    prompt = body.content.strip()
    chosen_model, chosen_effort = normalize_model_choice(body.model_id, body.effort)
    handoff_metadata = dict(metadata)
    metadata_update: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        "updated_at_ms": now_ms(),
        "plan_mode": body.plan_mode,
        PARTICIPANT_LOGINS_KEY: merge_participant_logins(
            metadata.get(PARTICIPANT_LOGINS_KEY), login
        ),
    }
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    if is_thread_resolved(metadata):
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

    active_model = metadata_model_id(metadata) if body.images else None
    content = _user_message_content(prompt, body.images, model_id=active_model)
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    queue_payload: dict[str, Any] = {
        "text": prompt,
        "source": DASHBOARD_SOURCE,
        "surface": "web",
        "sender": {
            "id": f"github:{login}",
            "platform": "github",
            "github_login": login,
            **({"email": email} if email else {}),
        },
        "from_owner": user_owns_thread(metadata, login, email),
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
    thread = await client.threads.get(thread_id)
    return await thread_summary(thread)


async def _cancel_active_thread_runs(client: Any, thread_id: str) -> None:
    run_ids: set[str] = set()
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            run_ids.update(
                run_id for run in runs if isinstance((run_id := run.get("run_id")), str) and run_id
            )
            if len(runs) < 100:
                break
            offset += len(runs)
    if run_ids:
        await client.runs.cancel_many(
            thread_id=thread_id,
            run_ids=sorted(run_ids),
            action="interrupt",
        )


async def _interrupt_thread(client: Any, thread_id: str) -> dict[str, Any]:
    try:
        await _cancel_active_thread_runs(client, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc

    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_status": "interrupted", "updated_at_ms": now_ms()},
    )
    return await thread_summary(await client.threads.get(thread_id))


async def cancel_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Interrupt every live run on a thread on behalf of its owner.

    Cancels by thread rather than by ``latest_run_id`` so the stop button works
    for runs this browser never started (Slack/Linear/GitHub triggers, CI
    auto-fix): the client-side ``stream.stop()`` can only cancel a run it
    dispatched itself, and cached ``latest_run_id`` metadata can lag the run the
    platform is actually executing.
    """
    await get_owned_thread(thread_id, login, email=email)
    return await _interrupt_thread(langgraph_client(), thread_id)


async def admin_cancel_dashboard_thread(thread_id: str) -> dict[str, Any]:
    client = langgraph_client()
    try:
        await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    return await _interrupt_thread(client, thread_id)


async def delete_dashboard_thread(thread_id: str, login: str, *, email: str | None = None) -> None:
    client = langgraph_client()
    metadata = await get_owned_thread_metadata(thread_id, login, email=email)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.delete(thread_id)


async def resolve_dashboard_thread(
    thread_id: str, login: str, *, resolved: bool, email: str | None = None
) -> dict[str, Any]:
    """Mark a thread resolved/unresolved via thread metadata."""
    client = langgraph_client()
    thread = await get_owned_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    metadata_update: dict[str, Any] = {
        "resolved": resolved,
        "resolved_at_ms": now_ms() if resolved else None,
    }
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not update resolved state for thread %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to update thread") from exc
    updated = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return await thread_summary(updated)


async def get_dashboard_thread_state(
    thread_id: str,
    login: str,
    *,
    email: str | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    record = timings if timings is not None else {}
    client = langgraph_client()
    with phase(record, "thread_get"):
        try:
            thread = await client.threads.get(thread_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, "thread not found") from exc
    assert_thread_readable(thread_metadata(thread))
    thread, latest_run_status, _ = await refresh_latest_run_metadata(client, thread, timings=record)
    metadata = thread_metadata(thread)
    with phase(record, "get_state"):
        state = await client.threads.get_state(thread_id)
    result = as_json_object(state)
    # The SDK's `useStream` opens its live event subscription only when the
    # hydrated `getState()` looks active (`next` non-empty / absent). When a
    # run was just started out-of-band (our REST run-create), the latest
    # checkpoint can still be the previous finished one with `next == []`,
    # which the SDK reads as idle and never opens the stream. Drop `next`
    # while a run is pending/running so the SDK treats the thread as active.
    if (
        thread_is_busy(thread)
        or latest_run_status in {"pending", "running"}
        or metadata.get("latest_run_status") in {"pending", "running"}
    ):
        result.pop("next", None)
    return result


@dataclass
class _CommandAuthorization:
    """What the command proxy learned while authorizing, for use after the hop."""

    method: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    creating: bool = False


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


async def _authorize_thread_command(
    thread_id: str, method: Any, login: str, email: str | None
) -> tuple[_CommandAuthorization, bool]:
    """Authorize one SDK command; returns what it learned plus whether the thread is busy.

    The dashboard mints the thread id client-side and submits straight away, so
    the very first ``run.start`` may target a thread that doesn't exist yet.
    That command lazily creates + stamps + owns the thread (in
    ``enrich_run_start_command``); any other command against a missing thread is
    a 404. On an existing thread, ``run.start`` (the posting path) is open to any
    org member and attributed in ``enrich_run_start_command``. Input commands on
    admin threads require an admin; other threads keep unattributed commands
    such as ``input.respond`` owner-only.
    """
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception:  # noqa: BLE001
        thread = None

    if thread is None:
        if method != "run.start":
            raise HTTPException(404, "thread not found")
        return _CommandAuthorization(method, {}, creating=True), False

    metadata = thread_metadata(thread)
    post_command = method in _THREAD_POST_COMMAND_METHODS
    if post_command:
        assert_thread_postable(metadata, login, email)
    else:
        assert_thread_readable(metadata)
    if method != "run.start" and not (post_command and metadata.get("admin_thread") is True):
        assert_thread_owner(metadata, login, email)
    busy = thread_is_busy(thread) or metadata.get("latest_run_status") in {"pending", "running"}
    return _CommandAuthorization(method, metadata, creating=False), busy


async def proxy_dashboard_thread_commands(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    received_at_ms = now_ms()
    authorization = _CommandAuthorization()

    async def enrich(command: dict[str, Any]) -> dict[str, Any]:
        nonlocal authorization
        authorization, thread_busy = await _authorize_thread_command(
            thread_id, command.get("method"), login, email
        )
        enriched = await enrich_run_start_command(
            thread_id,
            login,
            command,
            metadata=authorization.metadata,
            thread_busy=thread_busy,
            creating=authorization.creating,
            email=email,
        )
        if authorization.method == "run.start":
            params = enriched.get("params")
            if isinstance(params, dict):
                run_metadata = params.get("metadata")
                if not isinstance(run_metadata, dict):
                    run_metadata = {}
                    params["metadata"] = run_metadata
                run_metadata["dashboard_ttft_started_at_ms"] = received_at_ms
        return enriched

    status_code, content, media_type = await proxy_commands(
        thread_id, body, enrich=enrich, content_type=content_type
    )

    try:
        response_payload = json.loads(content) if content else None
    except json.JSONDecodeError:
        response_payload = None
    run_id = _extract_run_id_from_command_response(response_payload)
    if not (
        authorization.method == "run.start"
        and status_code in {200, 202, 204}
        and isinstance(response_payload, dict)
        and response_payload.get("type") == "success"
        and run_id is not None
    ):
        return status_code, content, media_type

    if not authorization.creating:
        try:
            await _notify_slack_web_handoff(thread_id, authorization.metadata, langgraph_client())
        except Exception:
            logger.exception(
                "Failed to update Slack message for dashboard handoff on %s", thread_id
            )

    spawn_ttft_observer(thread_id, run_id, received_at_ms)
    try:
        await langgraph_client().threads.update(
            thread_id=thread_id,
            metadata={
                "latest_run_id": run_id,
                "latest_run_status": "pending",
                "updated_at_ms": now_ms(),
            },
        )
    except Exception:
        logger.warning(
            "Failed to persist started dashboard run %s on thread %s",
            run_id,
            thread_id,
            exc_info=True,
        )
    return status_code, content, media_type


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
    require_json_content_type(content_type)
    await get_readable_thread_metadata(thread_id)
    return stream_thread_events(thread_id, body, content_type)


async def proxy_dashboard_thread_history(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    require_json_content_type(content_type)
    await get_readable_thread_metadata(thread_id)
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
    return await passthrough(
        "POST",
        thread_id,
        "history",
        body=json.dumps(payload).encode(),
        content_type=content_type,
    )


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    await get_owned_thread_metadata(thread_id, login, email=email)
    status_code, content, media_type = await passthrough(
        "POST",
        thread_id,
        f"runs/{run_id}/cancel",
        params={"wait": wait, "action": action},
    )
    if status_code in {200, 202, 204}:
        try:
            await langgraph_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "latest_run_status": "interrupted",
                    "updated_at_ms": now_ms(),
                },
            )
        except Exception:
            logger.debug(
                "Could not update thread metadata after run cancel for %s",
                thread_id,
                exc_info=True,
            )
    return status_code, content, media_type


async def stream_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None, last_event_id: str | None = None
) -> AsyncIterator[str]:
    await get_readable_thread_metadata(thread_id)

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
