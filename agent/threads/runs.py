"""Turning a dashboard request into a LangGraph run: bodies, models, images, commands."""

import base64
import binascii
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from pydantic import BaseModel, ConfigDict, Field

from agent.bridge.store import Bridge, BridgeStore, SandboxBridgeBinding
from agent.dashboard.admin import is_admin
from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.options import (
    DEPRECATED_MODEL_IDS,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_images,
    normalize_model_choice,
)
from agent.dashboard.profiles import get_profile
from agent.dashboard.repo_access import require_repo_access_for_workspace
from agent.dashboard.workspace_settings import (
    get_workspace_settings,
)
from agent.database import postgres
from agent.dispatch import FOLLOW_UP_PICKUP_KIND, create_durable_run, dispatch_agent_run
from agent.input_messages import (
    PersonIdentity,
    RunMessage,
    build_input_messages,
    dynamic_context_hashes_from_messages,
    injected_dynamic_context_hashes_from_metadata,
)
from agent.invocation import new_invocation_id, with_invocation_id
from agent.slack.client import (
    lookup_slack_thread_run_mapping,
    update_slack_trace_reply_for_web_handoff,
)
from agent.source_context import SourceContext
from agent.threads.access import (
    _ensure_dashboard_github_token,
    agent_version_metadata,
    resolve_run_email,
)
from agent.threads.principals import STARTED_BY_ID, STARTED_BY_NAME, Principal, ThreadType
from agent.threads.summary import (
    DASHBOARD_SOURCE,
    TRANSCRIPT_VERSION,
    _is_thread_resolved,
    _metadata_model_id,
    _now_ms,
    _parse_repo,
    repo_config_from_metadata,
    thread_source,
)
from agent.transcript.attachments import PendingAttachment
from agent.transcript.engine import Command, append
from agent.transcript.events import (
    MessageAttachment,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    TurnFailed,
    TurnQueued,
    TurnRequested,
)
from agent.transcript.turns import open_turn_id, recorded_turn_id
from agent.users import User
from agent.utils.dashboard_handoff import DASHBOARD_HANDOFF_BODY
from agent.utils.json_types import JsonObject, as_thread_dict, thread_metadata
from agent.utils.thread_ops import langgraph_client, queue_message_for_thread
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    merge_participants,
)
from agent.utils.thread_pr_state import agent_thread_pr_state_lock
from agent.workspaces.routing import resolve_workspace, workspace_for_repo

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
API_SOURCE = "api"
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
    workspace: str | None,
) -> tuple[str, str]:
    settings = await get_workspace_settings(workspace)
    resolved_model, resolved_effort = settings.default_model("agent")
    if model_id not in DEPRECATED_MODEL_IDS:
        profile_model, profile_effort = normalize_profile_overrides(profile)
        if profile_model and profile_effort:
            resolved_model, resolved_effort = profile_model, profile_effort
        chosen_model, chosen_effort = normalize_model_choice(model_id, effort)
        if chosen_model and chosen_effort:
            resolved_model, resolved_effort = chosen_model, chosen_effort
    resolved_model, resolved_effort = gate_fable_model(
        resolved_model,
        resolved_effort,
        fable_enabled=settings.fable_enabled,
    )
    if not isinstance(resolved_effort, str):
        raise ValueError("workspace default model must include a reasoning effort")
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


async def _resolve_requested_workspace(
    requested: object, repo_config: dict[str, str] | None, *, login: str | None
) -> str:
    """The workspace a new dashboard thread lands in.

    An explicit pick from the composer wins when it names a real workspace;
    otherwise the repository's owner, then the signed-in user's default,
    else the instance default.
    """
    tag = requested if isinstance(requested, str) and requested.strip() else None
    repo = (
        (repo_config["owner"], repo_config["name"])
        if repo_config and repo_config.get("owner") and repo_config.get("name")
        else None
    )
    return (await resolve_workspace(tag=tag, repo=repo, login=login)).slug


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
    model_selection: str = "auto",
    visibility: Literal["public", "private"] = "public",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Create a dashboard thread with immutable ownership and visibility."""
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    prompt = prompt.strip()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(
        profile, model_id, effort, workspace
    )
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
        "owner_type": "user",
        "owner_login": login.strip(),
        "visibility": visibility,
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
        "model_selection": model_selection,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    if visibility == "private" and is_admin(email, login=login):
        metadata["admin_thread"] = True
    if workspace:
        metadata["workspace"] = workspace
    if not title:
        metadata["title_seed"] = initial_title
    if has_repo:
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    elif repo_explicitly_none:
        metadata["repo_explicitly_none"] = True

    # A deployment without PostgreSQL has nowhere to keep a transcript, so the
    # thread is not stamped as one and keeps reading LangGraph state.
    transcribed = postgres.configured()
    if transcribed:
        metadata["transcript"] = TRANSCRIPT_VERSION

    client = langgraph_client()
    await client.threads.create(
        thread_id=thread_id,
        metadata={**metadata, "feedback_initiator_login": login},
        if_exists="raise",
    )
    thread = await client.threads.get(thread_id)
    if not transcribed:
        return as_thread_dict(thread)
    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}:created",
                event=ThreadCreated(
                    title=initial_title,
                    source=DASHBOARD_SOURCE,
                    owner_login=login.strip(),
                    visibility=visibility,
                    repo_owner=repo_config["owner"] if has_repo else None,
                    repo_name=repo_config["name"] if has_repo else None,
                    model_id=resolved_model,
                    effort=resolved_effort,
                    # The mirror the transcript read path authorizes against, so
                    # it is LangGraph's own metadata rather than a rebuild of it.
                    metadata=thread_metadata(thread),
                ),
                actor_kind="user",
            )
        ],
    )
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
    model_selection = metadata.get("model_selection")
    if model_selection in {"auto", "explicit"}:
        configurable["model_selection"] = model_selection
    # The agent re-checks the requesting user against CONFIGURED_ADMINS before it
    # hands out the workspace tools, so this only marks intent.
    if metadata.get("admin_thread") is True:
        configurable["admin_thread"] = True
    continued_from = metadata.get("continued_from_thread_id")
    if isinstance(continued_from, str) and continued_from:
        configurable["continued_from_thread_id"] = continued_from
    workspace = metadata.get("workspace") or metadata.get("environment")
    if isinstance(workspace, str) and workspace:
        # ``environment`` is kept for one release so older graph code paths
        # that still read it from the run config keep working.
        configurable["workspace"] = workspace
        configurable["environment"] = workspace
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
    """Read inline image blocks for size, format, and model validation."""
    if not isinstance(content, list):
        return []
    images: list[DashboardImageBody] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image_url":
            image_url = block.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str):
                raise HTTPException(422, "invalid image data")
            header, separator, data = url.partition(",")
            if (
                not separator
                or not header.startswith("data:image/")
                or not header.endswith(";base64")
            ):
                raise HTTPException(422, "images must be embedded as base64 data URLs")
            images.append(DashboardImageBody(base64=data, mimeType=header[5:-7]))
            continue
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


def _transcript_attachments(
    images: list[DashboardImageBody], message_id: str
) -> tuple[list[MessageAttachment], tuple[PendingAttachment, ...]]:
    """Attachment rows for a command's images, and the metadata the event carries.

    ``_decode_dashboard_image`` re-applies the type allowlist and the 10MB cap
    the run already validated, so nothing reaches the database unchecked.
    """
    metadata: list[MessageAttachment] = []
    pending: list[PendingAttachment] = []
    for position, image in enumerate(images):
        attachment_id = uuid.uuid7()
        pending.append(
            PendingAttachment(
                attachment_id=attachment_id,
                message_id=message_id,
                position=position,
                mime_type=image.mime_type,
                file_name=image.file_name,
                data=_decode_dashboard_image(image),
            )
        )
        metadata.append(
            MessageAttachment(
                mime_type=image.mime_type,
                file_name=image.file_name,
                attachment_id=attachment_id,
            )
        )
    return metadata, tuple(pending)


def _validate_command_images(content: Any, *, model_id: str | None) -> None:
    """Reject images for text-only models / oversize attachments (raises 422)."""
    images = _dashboard_images_from_content(content)
    if images:
        _image_blocks(images, model_id=model_id)


async def _resolve_sandbox_bridge(
    requested: object, *, owner_id: str, creating: bool
) -> Bridge | None:
    """The live bridge a new thread asked to run on, validated before it exists.

    Checked before the thread record is written: a thread stamped with a bridge
    nobody is answering can never be given a different sandbox later.
    """
    if requested is None:
        return None
    if not isinstance(requested, str) or not requested.strip():
        raise HTTPException(422, "sandbox_bridge_id must be a non-empty string")
    if not creating:
        raise HTTPException(409, "sandbox_bridge_id is only accepted when creating a thread")
    return await BridgeStore.require_open(requested.strip(), owner_id=owner_id)


async def _bind_thread_to_bridge(
    thread_id: str, bridge: Bridge, metadata: dict[str, Any]
) -> dict[str, Any]:
    binding = SandboxBridgeBinding.of(bridge).dump()
    await langgraph_client().threads.update(thread_id=thread_id, metadata=binding)
    logger.info(
        "Bound a thread to a sandbox bridge",
        extra={"bridge_id": bridge.bridge_id, "bridge_thread": thread_id},
    )
    return {**metadata, **binding}


def requested_thread_type(configurable: Mapping[str, Any]) -> ThreadType | None:
    """The kind of thread a creating command asks for, if it names one."""
    requested = configurable.get("thread_type")
    if requested is None:
        return None
    if requested not in ("system", "workspace", "private"):
        raise HTTPException(422, "thread_type must be system, workspace, or private")
    return cast(ThreadType, requested)


async def _requested_visibility(
    configurable: Mapping[str, Any],
) -> Literal["public", "private"]:
    """How visible a person's new thread is, from the kind they asked for.

    ``workspace`` and ``private`` are the two a person may create, and they are
    what "public" and "private" have always meant here. A client that names
    neither starts a workspace thread.
    """
    requested = requested_thread_type(configurable)
    if requested == "system":
        # The principal check upstream allows this only for an admin, and an admin's
        # system thread is not created through the dashboard record at all.
        raise HTTPException(500, "system threads are not created as a person's thread")
    if requested is not None:
        return "private" if requested == "private" else "public"
    visibility = configurable.get("visibility") or "public"
    if visibility not in ("public", "private"):
        raise HTTPException(422, "visibility must be public or private")
    return cast(Literal["public", "private"], visibility)


async def _attributed_run_messages(
    thread_id: str,
    login: str,
    *,
    metadata: Mapping[str, Any],
    content: Any,
    creating: bool,
    email: str | None,
    client: Any,
) -> tuple[list[RunMessage], set[str], set[str]]:
    """The human message a dashboard command carries, attributed to its sender.

    Returns the structured messages, the dynamic-context hashes already in the
    conversation, and the ids of the messages the graph already holds.
    """
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
    person: PersonIdentity = {"id": sender_id, "github_login": login}
    if email:
        person["email"] = email
    sender_id = (await User.canonical_person(person))["id"]
    structured = build_input_messages(
        content,
        {"sender_id": sender_id, "surface": "web", "kind": "human"},
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
    return structured, injected, persisted_message_ids


async def _enrich_run_start_command(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    creating: bool = False,
    email: str | None = None,
) -> dict[str, Any]:
    if command.get("method") != "run.start":
        return command

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
    model_selection = client_configurable.get("model_selection")
    if model_selection not in {"auto", "explicit"}:
        if client_configurable.get("agent_model_id"):
            model_selection = "explicit"
        else:
            model_selection = "auto" if creating else metadata.get("model_selection")
    offloading = offload_requested(params)
    sandbox_bridge = await _resolve_sandbox_bridge(
        client_configurable.get("sandbox_bridge_id"),
        owner_id=Principal.of_login(login, email).sender_id,
        creating=creating,
    )
    content = _command_message_content(params)
    if offloading and creating:
        raise HTTPException(400, "offloading requires an existing conversation")
    command_images = _dashboard_images_from_content(content)
    invocation_id = new_invocation_id()
    invocation_started_at = datetime.now(UTC).isoformat()
    overrides = with_invocation_id(None, invocation_id)
    overrides["invocation_started_at"] = invocation_started_at
    run_model: str | None = None
    run_effort: str | None = None

    if creating:
        # First ``run.start`` for a client-minted thread id: stamp the full
        # dashboard thread record (owner, title, repo, model) and validate any
        # attached images against the resolved model before the run is
        # forwarded to LangGraph. The repo hint rides in the client
        # configurable; it never reaches the run config (which is rebuilt from
        # the stamped metadata below).
        visibility = await _requested_visibility(client_configurable)
        repo_config = _parse_repo(client_configurable.get("repo")) or {}
        thread = await _create_dashboard_thread_record(
            thread_id,
            login=login,
            email=email,
            repo_config=repo_config,
            repo_explicitly_none=client_configurable.get("repo_explicitly_none") is True,
            visibility=visibility,
            prompt=_command_prompt_text(content),
            images=command_images,
            model_id=client_configurable.get("agent_model_id"),
            effort=client_configurable.get("agent_effort"),
            model_selection=model_selection or "auto",
            workspace=await _resolve_requested_workspace(
                client_configurable.get("workspace") or client_configurable.get("environment"),
                repo_config,
                login=login,
            ),
        )
        metadata = thread_metadata(thread)
        if sandbox_bridge is not None:
            metadata = await _bind_thread_to_bridge(thread_id, sandbox_bridge, metadata)
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

    structured, injected, persisted_message_ids = await _attributed_run_messages(
        thread_id,
        login,
        metadata=metadata,
        content=content,
        creating=creating,
        email=email,
        client=client,
    )
    # The transcript keys a human message by the id the graph will carry, so the
    # id is minted here when the client did not send a usable one.
    transcribed = (creating and postgres.configured()) or metadata.get(
        "transcript"
    ) == TRANSCRIPT_VERSION
    client_message_id = _command_message_id(params)
    message_id: str | None = None
    if client_message_id and client_message_id not in persisted_message_ids:
        message_id = client_message_id
    elif transcribed:
        message_id = str(uuid.uuid7())
    if message_id:
        structured[-1]["id"] = message_id
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = structured
    metadata_update: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        # Continuing on the web promotes a `/oswe` question thread for good.
        "unlisted": False,
        "model_selection": model_selection,
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
    metadata_update["feedback_last_activity_at_ms"] = metadata_update["updated_at_ms"]
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
            await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    else:
        if _is_thread_resolved(metadata):
            metadata_update["resolved"] = False
            metadata_update["resolved_at_ms"] = None
        if metadata.get("attention_reason"):
            metadata_update["attention_reason"] = None
        metadata = {**metadata, **metadata_update}
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)

    # Offloading starts a run with no human message, so it has no turn to
    # request; the middleware's ``turn.started`` opens that turn instead.
    if transcribed:
        turn_id = uuid.uuid7()
        if message_id is not None and not offloading:
            # Keyed by the message, not the turn: a retried ``run.start`` mints
            # a new turn id but asks for the same message, so its receipt
            # deduplicates it — and the run then has to join the turn that was
            # recorded the first time rather than one nothing was written under.
            command_id = f"message:{message_id}:requested"
            attachments, pending = _transcript_attachments(command_images, message_id)
            appended = await append(
                thread_id,
                [
                    Command(
                        command_id=command_id,
                        event=TurnRequested(
                            turn_id=turn_id,
                            message_id=message_id,
                            # The envelope, not the bare prompt: it is what
                            # carries the sender and surface a reader attributes
                            # the message by.
                            text=_command_prompt_text(structured[-1].get("content")),
                            sender=MessageSender(login=login, kind=DASHBOARD_SOURCE),
                            attachments=attachments,
                            model_id=run_model,
                            effort=run_effort,
                        ),
                        actor_kind="user",
                        turn_id=turn_id,
                        attachments=pending,
                    )
                ],
            )
            if not any(event.command_id == command_id for event in appended.events):
                turn_id = await recorded_turn_id(thread_id, command_id) or turn_id
        overrides["transcript_turn_id"] = str(turn_id)

    overrides["model_selection"] = model_selection
    merged_configurable = await _build_dashboard_configurable(
        thread_id,
        login,
        metadata,
        overrides=overrides,
    )

    run_metadata = params.get("metadata")
    if not isinstance(run_metadata, dict):
        run_metadata = {}
    run_metadata = with_invocation_id(
        {
            **{
                key: value
                for key, value in run_metadata.items()
                if key not in {"visibility", "owner_type", "owner_login", "system_authorization"}
            },
            **agent_version_metadata(),
            "invocation_started_at": invocation_started_at,
            **(
                {RUN_MODEL_KEY: merged_configurable["agent_model_id"]}
                if isinstance(merged_configurable.get("agent_model_id"), str)
                else {}
            ),
        },
        invocation_id,
    )

    if offloading:
        merged_configurable["offload_conversation"] = True
        params["input"] = {}

    params["assistant_id"] = _ASSISTANT_ID
    params.setdefault("stream_mode", list(DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": merged_configurable}
    params["metadata"] = run_metadata
    command["params"] = params
    return command


QUEUED_BY_KEY = "queued_by"
# The model a dashboard run was started with; thread metadata moves on as soon
# as a follow-up is queued with another one.
RUN_MODEL_KEY = "agent_model_id"


def offload_requested(params: dict[str, Any]) -> bool:
    """Whether a ``run.start`` asks to offload the conversation."""
    config = params.get("config")
    configurable = config.get("configurable") if isinstance(config, dict) else None
    content = _command_message_content(params)
    return (
        isinstance(configurable, dict) and configurable.get("offload_conversation") is True
    ) or (isinstance(content, str) and content.strip() == "/offload")


async def steer_running_thread(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    email: str | None = None,
) -> dict[str, Any]:
    """Deliver a ``run.start`` sent while a run is live into that run.

    The message joins the active turn instead of opening a new one: it is
    recorded on the transcript right away and left for the run to pick up
    before its next model call. The reply mirrors the protocol's success
    envelope so the caller cannot tell a steer from a start.
    """
    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
    content = _command_message_content(params)
    command_images = _dashboard_images_from_content(content)
    if not _command_prompt_text(content) and not command_images:
        raise HTTPException(422, "a follow-up needs a message")

    client = langgraph_client()
    latest_run_id = metadata.get("latest_run_id")
    live_run_id = latest_run_id if isinstance(latest_run_id, str) and latest_run_id else None
    # The run keeps the model it started with, so images are held to it. Thread
    # metadata may already name the model of a follow-up queued behind it.
    run_model = (await _run_metadata(client, thread_id, live_run_id)).get(RUN_MODEL_KEY)
    image_blocks = _image_blocks(
        command_images,
        model_id=run_model if isinstance(run_model, str) else _metadata_model_id(metadata),
    )

    structured, _, persisted_message_ids = await _attributed_run_messages(
        thread_id,
        login,
        metadata=metadata,
        content=content,
        creating=False,
        email=email,
        client=client,
    )
    client_message_id = _command_message_id(params)
    message_id = (
        client_message_id
        if client_message_id and client_message_id not in persisted_message_ids
        else str(uuid.uuid7())
    )
    structured[-1]["id"] = message_id

    # The live run's own turn, never a follow-up queued behind it.
    turn_id = (
        await open_turn_id(thread_id, live_run_id)
        if metadata.get("transcript") == TRANSCRIPT_VERSION
        else None
    )
    if turn_id is not None:
        attachments, pending = _transcript_attachments(command_images, message_id)
        await append(
            thread_id,
            [
                Command(
                    command_id=f"human:{message_id}",
                    event=MessageCompleted(
                        turn_id=turn_id,
                        message_id=message_id,
                        role="human",
                        text=_command_prompt_text(structured[-1].get("content")),
                        sender=MessageSender(login=login, kind=DASHBOARD_SOURCE),
                        attachments=attachments or None,
                        created_at=datetime.now(UTC),
                    ),
                    actor_kind="user",
                    turn_id=turn_id,
                    attachments=pending,
                )
            ],
        )

    payload: dict[str, Any] = {
        "text": _command_prompt_text(content),
        "images": list(image_blocks),
        "sender": {
            "id": f"github:{login}",
            "platform": "github",
            "github_login": login,
            **({"email": email} if email else {}),
        },
        "queue_id": message_id,
        "surface": "web",
        "created_at_ms": _now_ms(),
    }
    if metadata.get("source") == "slack":
        payload["source"] = DASHBOARD_SOURCE
    if not await queue_message_for_thread(thread_id, payload):
        raise HTTPException(502, "failed to deliver the follow-up to the running agent")
    # The run may have ended between the busy check and the store write, past
    # the completion hook's own look at the store. ``reject`` keeps the two
    # from racing each other into a second run.
    if not await _run_is_live(client, thread_id, live_run_id):
        try:
            dispatched = await dispatch_pending_follow_ups(
                thread_id, login, metadata, client=client, multitask_strategy="reject"
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not start a run for a follow-up steered after the run ended",
                exc_info=True,
                extra={"steer": {"thread_id": thread_id, "message_id": message_id}},
            )
        else:
            live_run_id = dispatched or live_run_id

    now_ms = _now_ms()
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "updated_at_ms": now_ms,
            "feedback_last_activity_at_ms": now_ms,
            PARTICIPANT_LOGINS_KEY: merge_participants(metadata.get(PARTICIPANT_LOGINS_KEY), login),
            PARTICIPANT_EMAILS_KEY: merge_participants(metadata.get(PARTICIPANT_EMAILS_KEY), email),
        },
    )
    try:
        await _notify_slack_web_handoff(thread_id, metadata, client)
    except Exception:
        logger.exception("Failed to update Slack message for dashboard handoff on %s", thread_id)
    return {
        "id": command.get("id"),
        "type": "success",
        "result": {
            "thread_id": thread_id,
            "run_id": live_run_id,
            "message_id": message_id,
            "steered": True,
        },
    }


async def _run_is_live(client: Any, thread_id: str, run_id: str | None) -> bool:
    if run_id is None:
        return False
    try:
        run = await client.runs.get(thread_id, run_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not read run %s after steering", run_id, exc_info=True)
        return False
    status = run.get("status") if isinstance(run, Mapping) else getattr(run, "status", None)
    return status in {"pending", "running"}


async def _run_metadata(client: Any, thread_id: str, run_id: str | None) -> Mapping[str, Any]:
    if run_id is None:
        return {}
    try:
        run = await client.runs.get(thread_id, run_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not read run %s before steering", run_id, exc_info=True)
        return {}
    metadata = run.get("metadata") if isinstance(run, Mapping) else None
    return metadata if isinstance(metadata, Mapping) else {}


async def queue_follow_up_run(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    email: str | None = None,
) -> dict[str, Any]:
    """Hold a ``run.start`` sent while a run is live until that run ends.

    The message gets its own run, created with LangGraph's ``enqueue`` strategy
    so the platform starts it when the thread goes idle, from any client and
    with this browser long gone. The transcript records the turn as requested
    right away and learns the run id so it can be cancelled before it starts.
    """
    enriched = await _enrich_run_start_command(
        thread_id, login, command, metadata=metadata, email=email
    )
    enriched_params: dict[str, Any] = enriched["params"]
    configurable: dict[str, Any] = enriched_params["config"]["configurable"]
    run_input = enriched_params.get("input")
    turn_id_raw = configurable.get("transcript_turn_id")
    turn_id = (
        uuid.UUID(turn_id_raw)
        if isinstance(turn_id_raw, str) and metadata.get("transcript") == TRANSCRIPT_VERSION
        else None
    )

    try:
        run = await create_durable_run(
            thread_id,
            _ASSISTANT_ID,
            input=run_input if isinstance(run_input, dict) else {},
            config={"configurable": configurable},
            # Only its sender may withdraw it (``proxy_dashboard_thread_run_cancel``).
            metadata={**enriched_params["metadata"], QUEUED_BY_KEY: login},
            source=DASHBOARD_SOURCE,
            client=langgraph_client(),
            multitask_strategy="enqueue",
        )
        run_id = run.get("run_id") if isinstance(run, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise HTTPException(502, "LangGraph did not return a run id for the queued follow-up")
    except Exception:
        # No run will ever start the requested turn; left open, it would read
        # as queued (and the thread as busy) forever.
        if turn_id is not None:
            try:
                await append(
                    thread_id,
                    [
                        Command(
                            command_id=f"turn:{turn_id}:failed",
                            event=TurnFailed(
                                turn_id=turn_id, error="the follow-up could not be queued"
                            ),
                            actor_kind="system",
                            turn_id=turn_id,
                        )
                    ],
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not close the turn of a follow-up that failed to queue",
                    exc_info=True,
                    extra={"queue": {"thread_id": thread_id, "turn_id": str(turn_id)}},
                )
        raise

    if turn_id is not None:
        # The run exists and will start on its own, so failing the request here
        # would only invite a retry that queues the message twice. Until this
        # lands, the row shows as pending and its run's ``turn.started`` fills
        # the run id in.
        try:
            await append(
                thread_id,
                [
                    Command(
                        command_id=f"turn:{turn_id}:queued",
                        event=TurnQueued(turn_id=turn_id, run_id=run_id),
                        actor_kind="user",
                        run_id=run_id,
                        turn_id=turn_id,
                    )
                ],
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not record the queued run of a follow-up",
                exc_info=True,
                extra={"queue": {"thread_id": thread_id, "run_id": run_id}},
            )
    return {
        "id": command.get("id"),
        "type": "success",
        "result": {"thread_id": thread_id, "run_id": run_id, "queued": True},
    }


async def dispatch_pending_follow_ups(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    client: Any,
    multitask_strategy: str = "interrupt",
) -> str | None:
    """Start a run for follow-ups a finished run never got to.

    A message steered into a run after its last model call is still waiting in
    the store; the new run's first model call picks it up. Returns the run id,
    or ``None`` when nothing was waiting.
    """
    queued = await client.store.get_item(("queue", thread_id), "pending_messages")
    value = queued.get("value") if isinstance(queued, Mapping) else None
    messages = value.get("messages") if isinstance(value, Mapping) else None
    if not isinstance(messages, list) or not messages:
        return None
    configurable = await _build_dashboard_configurable(thread_id, login, metadata)
    run = await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source=DASHBOARD_SOURCE,
        input={"messages": []},
        metadata={"kind": FOLLOW_UP_PICKUP_KIND},
        client=client,
        multitask_strategy=multitask_strategy,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    return run_id if isinstance(run_id, str) else None


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


async def _create_system_thread_record(
    thread_id: str,
    principal: Principal,
    *,
    prompt: str,
    title: str | None,
    repo_config: dict[str, str],
) -> dict[str, Any]:
    """Stamp a thread that belongs to a workspace rather than to a person.

    Deliberately not built from :func:`_create_dashboard_thread_record`: there is
    no profile to read defaults from, no owner to record, and no participant to
    merge, and inheriting those would give the thread a person it does not have.
    """
    now_ms = _now_ms()
    metadata: dict[str, Any] = {
        "source": API_SOURCE,
        "origin": API_SOURCE,
        "thread_category": "automation",
        "trigger_kind": "api",
        "automation_scope": "workspace",
        "owner_type": "system",
        "visibility": "public",
        "workspace": principal.workspace,
        "environment": principal.workspace,
        STARTED_BY_ID: principal.started_by_id,
        STARTED_BY_NAME: principal.started_by_name,
        "created_by": principal.created_by,
        "title": title or prompt[:80] or principal.started_by_name,
        "base_branch": "main",
        "model": "Default",
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    if repo_config:
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    client = langgraph_client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="raise")
    return as_thread_dict(await client.threads.get(thread_id))


async def _system_repo_config(
    configurable: Mapping[str, Any], principal: Principal
) -> dict[str, str]:
    """The repository a machine's thread works in, checked against its workspace.

    A federated workflow that names none gets its own repository, which is the
    only one it could have been talking about.
    """
    requested = configurable.get("repo")
    if requested is None and principal.default_repo:
        requested = principal.default_repo
    if requested is None:
        return {}
    repo_config = _parse_repo(requested)
    if not repo_config:
        raise HTTPException(422, "repo must be owner/name")
    owner = await workspace_for_repo(repo_config["owner"], repo_config["name"])
    if owner != principal.workspace:
        raise HTTPException(403, "repository is not in this workspace")
    await require_repo_access_for_workspace(f"{repo_config['owner']}/{repo_config['name']}")
    return repo_config


async def _enrich_system_run_start_command(
    thread_id: str,
    principal: Principal,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    creating: bool = False,
) -> dict[str, Any]:
    """The machine-principal half of ``run.start``.

    The person's path resolves a profile, a model, participants and a GitHub
    token for whoever sent the message. None of that exists here, so this stamps
    the workspace's own thread and run config and leaves the rest of the command
    pipeline — forwarding, streaming, run bookkeeping — shared.
    """
    if command.get("method") != "run.start":
        raise HTTPException(403, "a machine principal may only start runs")

    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
        command["params"] = params
    client_config = params.get("config")
    client_config = client_config if isinstance(client_config, dict) else {}
    client_configurable = client_config.get("configurable")
    client_configurable = client_configurable if isinstance(client_configurable, dict) else {}

    requested = requested_thread_type(client_configurable)
    if requested is None:
        raise HTTPException(422, "thread_type is required")
    principal.authorize(requested)

    content = _command_message_content(params)
    prompt = _command_prompt_text(content)
    if not prompt.strip():
        raise HTTPException(422, "a run needs a prompt")
    if _dashboard_images_from_content(content):
        raise HTTPException(422, "machine principals cannot attach images")

    sandbox_bridge = await _resolve_sandbox_bridge(
        client_configurable.get("sandbox_bridge_id"),
        owner_id=principal.sender_id,
        creating=creating,
    )
    repo_config = await _system_repo_config(client_configurable, principal)
    if creating:
        title = client_configurable.get("title")
        metadata = thread_metadata(
            await _create_system_thread_record(
                thread_id,
                principal,
                prompt=prompt,
                title=title if isinstance(title, str) else None,
                repo_config=repo_config,
            )
        )
        if sandbox_bridge is not None:
            metadata = await _bind_thread_to_bridge(thread_id, sandbox_bridge, metadata)
    else:
        principal.assert_can_post(metadata)

    invocation_id = new_invocation_id()
    structured = build_input_messages(
        content if content is not None else prompt,
        {"sender_id": principal.sender_id, "surface": "automation", "kind": "system"},
        systems=[
            {
                "id": principal.sender_id,
                "display_name": principal.started_by_name,
                "platform": "open-swe",
            }
        ],
    )
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = structured
    else:
        params["input"] = {"messages": structured}

    configurable: dict[str, Any] = with_invocation_id(
        {
            "thread_id": thread_id,
            "source": API_SOURCE,
            "workspace": principal.workspace,
            "environment": principal.workspace,
            STARTED_BY_ID: principal.started_by_id,
        },
        invocation_id,
    )
    configurable["invocation_started_at"] = datetime.now(UTC).isoformat()
    stored_repo = repo_config_from_metadata(metadata)
    if stored_repo:
        configurable["repo"] = stored_repo
    elif repo_config:
        configurable["repo"] = repo_config

    params["assistant_id"] = _ASSISTANT_ID
    params.setdefault("stream_mode", list(DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": configurable}
    params["metadata"] = with_invocation_id({**agent_version_metadata()}, invocation_id)
    command["params"] = params
    return command
