"""Turning a dashboard request into a LangGraph run: bodies, models, images, commands."""

import asyncio
import base64
import binascii
import copy
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast

from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator
from runtime_api.provenance import Provenance

from openswe.agent_api.connections import SENDER_USER_KEY
from openswe.bridge.local_access_policy import LocalAccess
from openswe.config import ENV
from openswe.dashboard.options import (
    DEPRECATED_MODEL_IDS,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_images,
    normalize_model_choice,
)
from openswe.dashboard.profiles import get_profile
from openswe.dashboard.repo_access import (
    require_repo_access_for_system,
    require_repo_access_for_user,
)
from openswe.dashboard.settings import (
    get_settings,
)
from openswe.dashboard.user_preferences import get_user_preferences
from openswe.dispatch import (
    FOLLOW_UP_PICKUP_KIND,
    SERVER_STREAM,
    TURN_ATTRIBUTION_FIELDS,
    RunFormat,
    _run_user_metadata,
    create_durable_run,
    dispatch_agent_run,
    dispatch_input,
)
from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.input_messages import (
    PersonIdentity,
    RunMessage,
    SystemIdentity,
    build_input_messages,
    dynamic_context_hashes_from_messages,
    injected_dynamic_context_hashes_from_metadata,
)
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.message_queue import QueuedMessage
from openswe.page_commands.offers import PageSession
from openswe.plugins.records import PluginChat
from openswe.projects.store import PROJECT_ID_KEY, PROJECTS, ProjectError
from openswe.prompts import prompt
from openswe.run_config import PluginChatConfig
from openswe.slack.client import (
    SlackNotice,
    fetch_latest_slack_thread_message,
    post_slack_thread_reply,
    update_slack_message,
)
from openswe.slack.staged_files import DASHBOARD_FILE_DIR, SlackFileEntry, queue_slack_files
from openswe.slack.user_options import SlackUserOption
from openswe.source_context import SourceContext
from openswe.threads.access import (
    _ensure_dashboard_github_token,
    agent_version_metadata,
    resolve_run_email,
)
from openswe.threads.creation import create_thread
from openswe.threads.identity import ThreadIdentity
from openswe.threads.principals import STARTED_BY_ID, STARTED_BY_NAME, Principal, ThreadType
from openswe.threads.start_request import SentRun
from openswe.threads.store import MAC_THREAD, THREADS
from openswe.threads.summary import (
    DASHBOARD_SOURCE,
    TRANSCRIPT_VERSION,
    _is_thread_resolved,
    _now_ms,
    _parse_repo,
    assert_thread_readable,
    chats_beside_plugin,
    repo_config_from_metadata,
    thread_is_promptable,
    thread_source,
)
from openswe.trace_metadata import RunOrigin
from openswe.transcript.attachments import PendingAttachment
from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    MessageAttachment,
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    TurnFailed,
    TurnQueued,
    TurnRequested,
)
from openswe.transcript.turns import OpenTurn, recorded_turn_id
from openswe.users import User
from openswe.users.repository_defaults import RepositoryDefaults
from openswe.utils.dashboard_handoff import (
    DASHBOARD_HANDOFF_BODY,
    RELAYED_FROM_KEY,
    REPLIES_KEY,
    PassageReply,
    dashboard_input_context,
    passage_replies,
)
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.json_types import as_thread_dict, thread_metadata
from openswe.utils.thread_ops import langgraph_client, queue_message_for_thread
from openswe.utils.thread_participants import participant_metadata
from openswe.utils.thread_settings import thread_model_choice

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
API_SOURCE = "api"
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_OPAQUE_FILE_MIME_TYPE = "application/octet-stream"
_SUPPORTED_FILE_MIME_TYPES = frozenset(
    {
        _OPAQUE_FILE_MIME_TYPE,
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_MAX_DASHBOARD_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_DASHBOARD_IMAGES_BASE64 = 5 * ((_MAX_DASHBOARD_IMAGE_BYTES + 2) // 3 * 4)
_MAX_DASHBOARD_FILES_BASE64 = 10 * ((_MAX_DASHBOARD_IMAGE_BYTES + 2) // 3 * 4)
_DASHBOARD_HANDOFF_SYSTEM: SystemIdentity = {
    "id": "system:dashboard-handoff",
    "display_name": "Dashboard handoff",
    "platform": "open-swe",
}
_PULL_REQUEST_THREAD_SYSTEM: SystemIdentity = {
    "id": "system:pull-request-thread",
    "display_name": "Pull request thread",
    "platform": "open-swe",
}
# Keys asking to run a thread on its owner's Mac, or to move it to or from one: refused.
_MAC_THREAD_KEYS: Final = ("sandbox_bridge_id", "sandbox_target")


class _LinkedPullRequest(BaseModel):
    pr_url: str | None = None


class DashboardImageBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str | None = None
    base64: str = Field(min_length=1)
    mime_type: str = Field(alias="mimeType", min_length=1)
    file_name: str | None = Field(default=None, alias="fileName")
    attachment_id: uuid.UUID = Field(default_factory=uuid.uuid7)

    @classmethod
    def files_in(cls, content: object) -> list[DashboardImageBody]:
        """The non-image file blocks of a message's content."""
        if not isinstance(content, list):
            return []
        files: list[DashboardImageBody] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "file":
                continue
            data, mime, name = block.get("base64"), block.get("mime_type"), block.get("file_name")
            if not isinstance(data, str) or not isinstance(mime, str):
                raise HTTPException(422, "invalid file data")
            files.append(
                cls(
                    kind="file",
                    base64=data,
                    mimeType=mime if mime in _SUPPORTED_FILE_MIME_TYPES else _OPAQUE_FILE_MIME_TYPE,
                    fileName=name if isinstance(name, str) else None,
                )
            )
        if sum(len(file.base64) for file in files) > _MAX_DASHBOARD_FILES_BASE64:
            raise HTTPException(413, "files exceed 100MB total limit")
        return files

    @staticmethod
    async def stage_files(
        thread_id: str,
        files: Sequence[DashboardImageBody],
        *,
        message_id: str | None = None,
    ) -> list[str]:
        """Queue attachments for the sandbox and return their paths."""
        if not files:
            return []
        for position, file in enumerate(files):
            _decode_dashboard_image(file)
            if message_id:
                # A retried run.start reuses the message id; matching ids keep its queue on the stored bytes.
                file.attachment_id = uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{thread_id}/{message_id}/{position}"
                )
            if not file.file_name and file.mime_type in _SUPPORTED_IMAGE_MIME_TYPES:
                file.file_name = f"{file.attachment_id}.{file.mime_type.split('/')[-1]}"
        staged = await queue_slack_files(
            thread_id,
            [SlackFileEntry(name=f.file_name or "", attachment_id=f.attachment_id) for f in files],
            directory=DASHBOARD_FILE_DIR,
        )
        return [file.sandbox_path for file in staged]


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
    settings = await get_settings()
    resolved_model, resolved_effort = settings.default_model("agent")
    if model_id not in DEPRECATED_MODEL_IDS:
        chosen_model, chosen_effort = normalize_model_choice(model_id, effort)
        if chosen_model and chosen_effort:
            resolved_model, resolved_effort = chosen_model, chosen_effort
    resolved_model, resolved_effort = gate_fable_model(
        resolved_model,
        resolved_effort,
        fable_enabled=settings.fable_enabled,
    )
    if not isinstance(resolved_effort, str):
        raise ValueError("global default model must include a reasoning effort")
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
    if image.kind == "file":
        if image.mime_type not in _SUPPORTED_FILE_MIME_TYPES:
            raise HTTPException(422, f"unsupported file type: {image.mime_type}")
    elif image.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
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
    if sum(len(image.base64) for image in images) > _MAX_DASHBOARD_IMAGES_BASE64:
        raise HTTPException(413, "images exceed 50MB total limit")
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


async def create_dashboard_thread_record(
    thread_id: str,
    *,
    login: str,
    email: str | None = None,
    repo_config: dict[str, str],
    repo_explicitly_none: bool = False,
    prompt: str | None,
    images: list[DashboardImageBody] | None = None,
    title: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    model_selection: str = "auto",
    visibility: Literal["public", "private"] = "public",
    extra_metadata: Mapping[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    if_exists: Literal["raise", "do_nothing"] = "raise",
) -> dict[str, Any]:
    """Create a dashboard thread with immutable ownership and visibility.

    ``prompt`` is its first message, checked here; ``None`` opens it with none
    yet, named by ``title``. ``profile`` is the person's profile when the caller
    already read it. With ``do_nothing`` a thread that already exists keeps its
    metadata and gets the creation record an earlier attempt may not have written.
    """
    if prompt is None and not title:
        raise ValueError("a thread opened without a message needs a title")
    profile = profile if profile is not None else await get_profile(login) or {}
    now_ms = _now_ms()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, model_id, effort)
    resolved_model, resolved_effort = _with_vision_fallback(
        resolved_model,
        resolved_effort,
        has_images=bool(images),
    )
    if prompt is not None:
        prompt = prompt.strip()
        _user_message_content(prompt, images or [], model_id=resolved_model)
    else:
        prompt = ""
    chosen_model, chosen_effort = normalize_model_choice(model_id, effort)
    metadata_model = chosen_model or "Default"
    metadata_effort = chosen_effort
    if images and not model_supports_images(str(metadata_model)):
        metadata_model = resolved_model
        metadata_effort = resolved_effort
    has_repo = bool(repo_config.get("owner") and repo_config.get("name"))
    initial_title = title or (await SlackUserOption.readable(prompt))[:80] or "New agent"
    metadata: dict[str, Any] = {
        "source": DASHBOARD_SOURCE,
        "origin": DASHBOARD_SOURCE,
        "owner_type": "user",
        "owner_login": login.strip(),
        **(
            {"owner_user_id": str(ThreadIdentity(login).user_id)}
            if ThreadIdentity(login).user_id
            else {}
        ),
        "visibility": visibility,
        "thread_category": "interactive",
        "trigger_kind": "user",
        **await participant_metadata({}, login=login, email=email),
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
    if visibility == "private" and ThreadIdentity(login).is_admin(email):
        metadata["admin_thread"] = True
    if not title:
        metadata["title_seed"] = initial_title
    if has_repo:
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    elif repo_explicitly_none:
        metadata["repo_explicitly_none"] = True
    if extra_metadata:
        metadata.update(extra_metadata)

    metadata["transcript"] = TRANSCRIPT_VERSION

    client = langgraph_client()
    await create_thread(
        client,
        thread_id,
        title=initial_title,
        metadata=metadata,
        if_exists=if_exists,
    )
    thread = await client.threads.get(thread_id)
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
    plugin_tools: bool = False,
) -> dict[str, Any]:
    """The run config of a dashboard run on ``thread_id``.

    ``plugin_tools`` gives a plugin chat's run the plugin's tools: only runs
    sent from its dock, where the plugin is open to answer them, get them.
    """
    profile = profile if profile is not None else await get_profile(login) or {}
    source = thread_source(metadata)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": source,
        **(
            {"github_login": login}
            if ThreadIdentity(login).github_login
            else {
                "user_id": str(ThreadIdentity(login).user_id),
                "github_linked": False,
                "auth_provider": "langsmith",
            }
        ),
        "user_email": await resolve_run_email(login, profile),
        "background_task_completion": False,
    }
    if ThreadIdentity(login).github_login:
        from openswe.dashboard.profiles import get_valid_access_token

        try:
            github_token = await get_valid_access_token(login)
        except Exception:
            logger.warning(
                "GitHub token unavailable; continuing without repository access", exc_info=True
            )
            github_token = None
        if not github_token:
            user = await ThreadIdentity(login).user()
            if user is None:
                raise HTTPException(401, "Sign in with LangSmith to use Robin")
            configurable.pop("github_login", None)
            configurable.update(user_id=str(user.id), github_linked=False)
    schedule_id = metadata.get("schedule_id")
    if source == "schedule" and isinstance(schedule_id, str) and schedule_id:
        configurable["schedule_id"] = schedule_id
    repo_config = repo_config_from_metadata(metadata)
    if configurable.get("github_linked") is False:
        configurable["repo_explicitly_none"] = True
    elif repo_config:
        configurable["repo"] = repo_config
    elif metadata.get("repo_explicitly_none") is True:
        configurable["repo_explicitly_none"] = True
    for key, value in SourceContext.from_metadata(metadata).dump().items():
        configurable.setdefault(key, value)
    model_selection = metadata.get("model_selection")
    if model_selection in {"auto", "explicit"}:
        configurable["model_selection"] = model_selection
    # The agent re-checks the requesting user against CONFIGURED_ADMINS before it
    # hands out the admin tools, so this only marks intent.
    if metadata.get("admin_thread") is True:
        configurable["admin_thread"] = True
    continued_from = metadata.get("continued_from_thread_id")
    if isinstance(continued_from, str) and continued_from:
        configurable["continued_from_thread_id"] = continued_from
    if plugin_tools and chats_beside_plugin(metadata):
        user = await User.for_person(ThreadIdentity(login).person)
        chat = await PluginChat.run_config(thread_id, user.id) if user else None
        if chat is not None:
            configurable["plugin_chat"] = chat.model_dump(mode="json")
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                configurable[key] = value
    if source == "schedule" and metadata.get("owner_type") == "system":
        for key in ("github_login", "user_id", "user_email", "github_linked", "auth_provider"):
            configurable.pop(key, None)
    return configurable


class SessionCreateBody(BaseModel):
    prompt: str = Field(min_length=1)
    repo: str | None = Field(default=None, pattern=r"^[^/\s]+/[^/\s]+$")
    visibility: Literal["public", "private"] | None = None
    start: bool = True


class ThreadCreateBody(BaseModel):
    """The SDK's ``threads.create``, which never creates a thread here.

    A dashboard thread starts with its first message, whose run config carries what
    the composer chose for it: its repository, visibility, project and
    machine. A create carries none of that, so this answers only for a thread that
    exists, as the Agent Server does for ``if_exists``.
    """

    thread_id: uuid.UUID
    if_exists: Literal["raise", "do_nothing"] = "raise"

    @model_validator(mode="before")
    @classmethod
    def _without_prompt(cls, body: object) -> object:
        # A body with a prompt starts a session, valid or not.
        if isinstance(body, dict) and "prompt" in body:
            raise ValueError("a body with a prompt starts a session")
        return body

    async def existing(self, login: str, *, email: str | None = None) -> dict[str, JsonValue]:
        """The thread as the Agent Server answers a create of one that exists.

        A missing thread and one the caller can't read are the same 404, so the route
        tells no one which thread ids exist.
        """
        thread = await THREADS.get(str(self.thread_id))
        if thread is None:
            raise HTTPException(404, "thread not found")
        assert_thread_readable(thread.metadata, login, email)
        if self.if_exists != "do_nothing":
            raise HTTPException(409, f"Thread {self.thread_id} already exists")
        return thread.as_langgraph()


async def create_dashboard_session(
    body: SessionCreateBody, login: str, *, email: str | None = None
) -> str:
    """Create a fresh owned thread using the person's dashboard defaults."""
    profile = RepositoryDefaults.model_validate(await get_profile(login) or {})
    identity = ThreadIdentity(login)
    if identity.user_id is not None and body.repo:
        raise HTTPException(403, "This account has no GitHub repository access")
    repo = (
        None
        if identity.user_id is not None
        else (body.repo if body.repo is not None else profile.repository())
    )
    if body.repo is None and identity.github_login:
        from openswe.webhooks.common import account_link_reason

        if await account_link_reason(identity.github_login) is not None:
            repo = None
    repo_config = _parse_repo(repo) or {}
    if repo:
        if not repo_config:
            raise HTTPException(422, "repo must be owner/name")
        await require_repo_access_for_user(login, f"{repo_config['owner']}/{repo_config['name']}")
    await _ensure_dashboard_github_token(login)
    thread = await create_dashboard_thread_record(
        str(uuid.uuid4()),
        login=login,
        email=email,
        repo_config=repo_config,
        prompt=body.prompt,
        visibility="private"
        if identity.user_id is not None
        else await _requested_visibility({"visibility": body.visibility}, login=login),
        repo_explicitly_none=identity.user_id is not None,
    )
    return str(thread["thread_id"])


async def start_dashboard_thread(
    login: str,
    email: str | None,
    *,
    title: str,
    prompt: str,
    repos: Sequence[str],
    visibility: Literal["public", "private"],
    unlisted: bool = False,
) -> str:
    """Start a person's dashboard thread; the first repo is the one its sandbox opens in."""
    identity = ThreadIdentity(login)
    if identity.user_id is not None and repos:
        raise HTTPException(403, "This account has no GitHub repository access")
    repo_configs: list[dict[str, str]] = []
    for repo in repos:
        repo_config = _parse_repo(repo)
        if not repo_config:
            raise HTTPException(422, "repos must be owner/name")
        await require_repo_access_for_user(login, f"{repo_config['owner']}/{repo_config['name']}")
        repo_configs.append(repo_config)
    await _ensure_dashboard_github_token(login)
    primary = repo_configs[0] if repo_configs else {}
    thread = await create_dashboard_thread_record(
        str(uuid.uuid4()),
        login=login,
        email=email,
        repo_config=primary,
        prompt=prompt,
        title=title,
        visibility="private" if identity.user_id is not None else visibility,
        repo_explicitly_none=identity.user_id is not None,
        extra_metadata={"unlisted": True} if unlisted else None,
    )
    thread_id = str(thread["thread_id"])
    client = langgraph_client()
    await dispatch_agent_run(
        thread_id,
        prompt,
        await _build_dashboard_configurable(thread_id, login, thread_metadata(thread)),
        source=DASHBOARD_SOURCE,
        thread_title=None,
        client=client,
    )
    return thread_id


async def start_run_with_tool_results(
    thread_id: str,
    login: str,
    *,
    prompt: str,
    tool_results: Sequence[Mapping[str, str]],
    overrides: dict[str, Any],
    plugin_tools: bool = False,
    as_sender: bool = False,
) -> str:
    """Start a run with the results of tools its client ran: a sandbox program, or a plugin's frame.

    ``as_sender`` when ``login`` ran them themselves, as in a plugin's frame.
    """
    await _ensure_dashboard_github_token(login)
    client = langgraph_client()
    metadata = thread_metadata(await client.threads.get(thread_id))
    configurable = await _build_dashboard_configurable(
        thread_id, login, metadata, overrides=overrides, plugin_tools=plugin_tools
    )
    if tool_results:
        user = (
            (await dispatch_input(prompt, DASHBOARD_SOURCE, configurable))["messages"]
            if prompt
            else []
        )
        run = await create_durable_run(
            thread_id,
            _ASSISTANT_ID,
            input={"messages": [*tool_results, *user]},
            config={"configurable": configurable},
            source=DASHBOARD_SOURCE,
            thread_title=None,
            client=client,
            multitask_strategy="enqueue",
            as_sender=as_sender,
        )
    else:
        run = await dispatch_agent_run(
            thread_id,
            prompt,
            configurable,
            source=DASHBOARD_SOURCE,
            thread_title=None,
            client=client,
            multitask_strategy="enqueue",
            as_sender=as_sender,
        )
    return str(run["run_id"])


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


def _command_message_replies(params: dict[str, Any]) -> list[PassageReply]:
    """The passages a ``run.start`` command's newest user message replies to."""
    run_input = params.get("input")
    messages = run_input.get("messages") if isinstance(run_input, dict) else None
    last = messages[-1] if isinstance(messages, list) and messages else None
    kwargs = last.get("additional_kwargs") if isinstance(last, dict) else None
    raw = kwargs.get(REPLIES_KEY) if isinstance(kwargs, dict) else None
    try:
        return passage_replies(raw)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid replies: {exc.errors()[0]['msg']}") from exc


def _relayed_from(params: dict[str, Any]) -> str | None:
    """Take the relaying thread's id off a ``run.start`` command's params."""
    relayed_from = params.pop(RELAYED_FROM_KEY, None)
    return relayed_from if isinstance(relayed_from, str) and relayed_from else None


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
        attachment_id = image.attachment_id
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


def requested_thread_type(configurable: Mapping[str, Any]) -> ThreadType | None:
    """The kind of thread a creating command asks for, if it names one."""
    requested = configurable.get("thread_type")
    if requested is None:
        return None
    if requested not in ("system", "workspace", "private"):
        raise HTTPException(422, "thread_type must be system, workspace, or private")
    return cast(ThreadType, requested)


async def _requested_visibility(
    configurable: Mapping[str, Any], *, login: str
) -> Literal["public", "private"]:
    """How visible a person's new thread is, from the kind they asked for.

    ``workspace`` and ``private`` are the two a person may create, and they are
    what "public" and "private" have always meant here. A client that names
    neither falls back to their saved default.
    """
    requested = requested_thread_type(configurable)
    if requested == "system":
        # The principal check upstream allows this only for an admin, and an admin's
        # system thread is not created through the dashboard record at all.
        raise HTTPException(500, "system threads are not created as a person's thread")
    if requested is not None:
        return "private" if requested == "private" else "public"
    visibility = (
        configurable.get("visibility") or (await get_user_preferences(login))["default_visibility"]
    )
    if visibility not in ("public", "private"):
        raise HTTPException(422, "visibility must be public or private")
    return cast(Literal["public", "private"], visibility)


@dataclass(frozen=True)
class _ThreadHistory:
    """What the graph already holds of a thread's conversation, read from its state.

    Only the state carries the dynamic context a run injected, so this is a full
    state read; callers start it early so it overlaps their other lookups.
    """

    read: bool = False
    context_hashes: frozenset[str] | None = None
    message_ids: frozenset[str] = frozenset()

    @classmethod
    async def of(cls, thread_id: str) -> _ThreadHistory:
        """The thread's history, empty when its state cannot be read."""
        try:
            state = await THREADS.state(thread_id)
        except Exception:
            logger.debug("Could not read dashboard thread history for %s", thread_id, exc_info=True)
            return cls()
        if state is None:
            return cls()
        values = state.get("values")
        messages = values.get("messages") if isinstance(values, dict) else None
        if not isinstance(messages, list):
            return cls(read=True)
        return cls(
            read=True,
            context_hashes=frozenset(dynamic_context_hashes_from_messages(messages)),
            message_ids=frozenset(
                message_id
                for message in messages
                if isinstance(message, Mapping) and isinstance(message_id := message.get("id"), str)
            ),
        )


async def _attributed_run_messages(
    login: str,
    *,
    metadata: Mapping[str, Any],
    history: _ThreadHistory,
    content: Any,
    email: str | None,
    review_chat_pr_url: str | None = None,
    relayed_from: str | None = None,
    replies: Sequence[PassageReply] = (),
    attached_paths: Sequence[str] = (),
) -> tuple[list[RunMessage], set[str]]:
    """The human message a dashboard command carries, attributed to its sender.

    Returns the structured messages and the dynamic-context hashes already in the
    conversation.
    """
    if content is None:
        content = ""
    sender_id = ThreadIdentity(login).sender_id
    injected = (
        set(history.context_hashes)
        if history.context_hashes is not None
        else injected_dynamic_context_hashes_from_metadata(metadata)
    )
    person: PersonIdentity = ThreadIdentity(login).person
    if email:
        person["email"] = email
    sender_id = (await User.canonical_person(person))["id"]
    notices: list[tuple[SystemIdentity, str]] = []
    if metadata.get("source") == "slack" and relayed_from is None:
        notices.append((_DASHBOARD_HANDOFF_SYSTEM, DASHBOARD_HANDOFF_BODY))
    pr_url = _LinkedPullRequest.model_validate(metadata).pr_url
    if pr_url and history.read and not history.message_ids:
        notices.append(
            (_PULL_REQUEST_THREAD_SYSTEM, prompt("runs/pull-request-thread", url=pr_url))
        )
    if isinstance(review_chat_pr_url, str):
        notices.append(
            (
                _PULL_REQUEST_THREAD_SYSTEM,
                prompt("runs/pull-request-review-chat", url=review_chat_pr_url),
            )
        )
    structured = build_input_messages(
        content,
        dashboard_input_context(sender_id, relayed_from, replies, attached_paths),
        systems=[system for system, _ in notices] or None,
        injected_dynamic_context_hashes=injected,
    )
    for system, body in notices:
        structured.insert(
            -1,
            build_input_messages(
                body,
                {"sender_id": system["id"], "surface": "automation", "kind": "system"},
                injected_dynamic_context_hashes={system["id"]},
            )[0],
        )
    return structured, injected


MAX_PLUGIN_CONTEXT = 8_000
PLUGIN_CONTEXT_MIME = "application/vnd.open-swe.plugin-context"
"""A message's attachment that marks the plugin context sent with it; its name is the label shown."""


def _plugin_context(
    metadata: Mapping[str, object],
    client_configurable: Mapping[str, object],
    chat: PluginChatConfig,
) -> tuple[list[RunMessage], list[MessageAttachment]]:
    """What the plugin beside a chat says when the person writes from its dock: how it
    describes its tools, and what it has open.

    Its author wrote both, so the agent reads them in a message ahead of the person's,
    never in its system prompt. The person's message carries an attachment naming the
    context, which the thread shows in place of it.
    """
    if not chats_beside_plugin(metadata) or client_configurable.get("plugin_dock") is not True:
        return [], []
    context = client_configurable.get("plugin_context")
    context = context[:MAX_PLUGIN_CONTEXT] if isinstance(context, str) and context.strip() else ""
    instructions = chat.instructions.strip()
    if not context and not instructions:
        return [], []
    label = client_configurable.get("plugin_context_label")
    label = label.strip()[:120] if isinstance(label, str) and label.strip() else "Plugin context"
    return (
        [
            {
                "role": "user",
                "id": f"plugin-context:{uuid.uuid7()}",
                "content": prompt("plugins/context", instructions=instructions, context=context),
                "additional_kwargs": Provenance.harness("plugin_context").stamp(),
            }
        ],
        [MessageAttachment(mime_type=PLUGIN_CONTEXT_MIME, file_name=label)] if context else [],
    )


async def _enrich_run_start_command(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    creating: bool = False,
    email: str | None = None,
    relayed_origin: RunOrigin | None = None,
) -> dict[str, Any]:
    if command.get("method") != "run.start":
        return command
    if not creating and ThreadIdentity(login).user_id is not None:
        if not thread_is_promptable(metadata, login):
            raise HTTPException(403, "This account cannot prompt this task")

    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
        command["params"] = params

    # The state read is the slowest lookup before dispatch, and nothing below
    # changes the conversation or the profile, so both run alongside the checks
    # and any sandbox move.
    history = None if creating else asyncio.ensure_future(_ThreadHistory.of(thread_id))
    profile_read = asyncio.ensure_future(get_profile(login))
    try:
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
        if any(client_configurable.get(key) is not None for key in _MAC_THREAD_KEYS):
            raise HTTPException(410, MAC_THREAD)
        content = _command_message_content(params)
        replies = _command_message_replies(params)
        relayed_from = _relayed_from(params)
        if offloading and creating:
            raise HTTPException(400, "offloading requires an existing conversation")
        # The person wrote, so the plugin's results may resume the chat again; any
        # that waited for them reach the agent with their message.
        plugin_user = (
            await User.for_person(ThreadIdentity(login).person)
            if chats_beside_plugin(metadata)
            else None
        )
        plugin_chat = (
            await PluginChat.run_config(thread_id, plugin_user.id) if plugin_user else None
        )
        held_results = (
            await PluginChat.person_wrote(thread_id, plugin_user.id)
            if plugin_user and plugin_chat and not offloading
            else []
        )
        context_messages, context_attachments = (
            _plugin_context(metadata, client_configurable, plugin_chat) if plugin_chat else ([], [])
        )
        command_images = _dashboard_images_from_content(content)
        command_files = DashboardImageBody.files_in(content)
        command_attachments = [*command_images, *command_files]
        if command_files:
            if not creating and metadata.get("transcript") != TRANSCRIPT_VERSION:
                raise HTTPException(422, "this thread is too old to accept files")
            for file in command_files:
                _decode_dashboard_image(file)
        invocation_id = new_invocation_id()
        invocation_started_at = datetime.now(UTC).isoformat()
        overrides = with_invocation_id(None, invocation_id)
        overrides["invocation_started_at"] = invocation_started_at
        overrides[RELAYED_FROM_KEY] = relayed_from
        run_model: str | None = None
        run_effort: str | None = None

        if creating:
            # First ``run.start`` for a client-minted thread id: stamp the full
            # dashboard thread record (owner, title, repo, model) and validate any
            # attached images against the resolved model before the run is
            # forwarded to LangGraph. The repo hint rides in the client
            # configurable; it never reaches the run config (which is rebuilt from
            # the stamped metadata below).
            visibility = await _requested_visibility(client_configurable, login=login)
            identity = ThreadIdentity(login)
            if identity.user_id is not None:
                visibility = "private"
                if client_configurable.get("repo"):
                    raise HTTPException(403, "This account has no GitHub repository access")
            repo_config = _parse_repo(client_configurable.get("repo")) or {}
            project_id = client_configurable.get("project_id")
            try:
                project = (
                    await PROJECTS.for_run(project_id, await User.for_login("github", login))
                    if isinstance(project_id, str) and project_id
                    else None
                )
            except ProjectError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
            thread = await create_dashboard_thread_record(
                thread_id,
                login=login,
                email=email,
                repo_config=repo_config,
                repo_explicitly_none=identity.user_id is not None
                or client_configurable.get("repo_explicitly_none") is True,
                visibility=visibility,
                prompt=_command_prompt_text(content)
                or ", ".join(f.file_name or "file" for f in command_files),
                images=command_images,
                model_id=client_configurable.get("agent_model_id"),
                effort=client_configurable.get("agent_effort"),
                model_selection=model_selection or "auto",
                extra_metadata={PROJECT_ID_KEY: str(project.id)} if project is not None else None,
                profile=await profile_read or {},
            )
            metadata = thread_metadata(thread)
            run_model, run_effort = normalize_model_choice(
                metadata.get("resolved_model"), metadata.get("resolved_effort")
            )
            if command_images and run_model and run_effort:
                overrides["agent_model_id"] = run_model
                overrides["agent_effort"] = run_effort
            elif chosen_model and chosen_effort:
                overrides["agent_model_id"] = chosen_model
                overrides["agent_effort"] = chosen_effort
        else:
            run_model, run_effort = (
                (chosen_model, chosen_effort) if chosen_model else thread_model_choice(metadata)
            )
            if command_images and run_model and run_effort:
                run_model, run_effort = _with_vision_fallback(
                    run_model, run_effort, has_images=True
                )
            _image_blocks(command_images, model_id=run_model)

        overrides["local_access_bridge_id"] = await LocalAccess.offer_for_run(
            client_configurable.get("local_access_bridge_id"),
            metadata=metadata,
            login=login,
            owner_id=Principal.of_login(login, email).sender_id,
        )

        prior = await history if history is not None else _ThreadHistory()
        profile = await profile_read or {}
        attached_paths = await DashboardImageBody.stage_files(
            thread_id, command_attachments, message_id=_command_message_id(params)
        )
        if isinstance(content, list):
            content = [
                block
                for block in content
                if not (isinstance(block, dict) and block.get("type") == "file")
            ]
    finally:
        reads = (profile_read,) if history is None else (history, profile_read)
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)

    structured, injected = await _attributed_run_messages(
        login,
        metadata=metadata,
        history=prior,
        content=content,
        email=email,
        review_chat_pr_url=client_configurable.get("review_chat_pr_url"),
        relayed_from=relayed_from,
        replies=replies,
        attached_paths=attached_paths,
    )
    # The transcript keys a human message by the id the graph will carry, so the
    # id is minted here when the client did not send a usable one.
    transcribed = creating or metadata.get("transcript") == TRANSCRIPT_VERSION
    client_message_id = _command_message_id(params)
    message_id: str | None = None
    if client_message_id and client_message_id not in prior.message_ids:
        message_id = client_message_id
    elif transcribed:
        message_id = str(uuid.uuid7())
    if message_id:
        structured[-1]["id"] = message_id
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = [*held_results, *context_messages, *structured]
    metadata_update: dict[str, Any] = {
        # A relay keeps the thread answering where its person is.
        **({} if relayed_from else {"source": DASHBOARD_SOURCE}),
        # Continuing on the web promotes a `/oswe` question thread for good.
        "unlisted": False,
        "model_selection": model_selection,
        **await participant_metadata(metadata, login=login, email=email),
        "injected_dynamic_context_hashes": sorted(injected),
    }
    if command_images and run_model and run_effort:
        overrides["agent_model_id"] = run_model
        overrides["agent_effort"] = run_effort
        overrides["model_override_reason"] = "image_input"
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
    if _is_thread_resolved(metadata):
        metadata_update["resolved"] = False
        metadata_update["resolved_at_ms"] = None
    metadata = {**metadata, **metadata_update}
    await THREADS.update(thread_id, metadata_update)

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
            attachments, pending = _transcript_attachments(command_attachments, message_id)
            attachments.extend(context_attachments)
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
    review_page_pr_url = client_configurable.get("review_chat_pr_url")
    if isinstance(review_page_pr_url, str):
        overrides["review_page_pr_url"] = review_page_pr_url
    if (page_tools := client_configurable.get("page_tools")) is not None:
        page_owner = await User.for_person(ThreadIdentity(login).person)
        if page_owner is not None:
            overrides["page"] = PageSession.offered(
                client_configurable.get("page_session"), page_owner.id, page_tools
            ).model_dump(mode="json")
    overrides["model_selection_changed"] = (
        client_configurable.get("model_selection_changed") is True
    )
    merged_configurable = await _build_dashboard_configurable(
        thread_id,
        login,
        metadata,
        profile=profile,
        overrides=overrides,
        plugin_tools=client_configurable.get("plugin_dock") is True,
    )

    run_metadata = params.get("metadata")
    if not isinstance(run_metadata, dict):
        run_metadata = {}
    run_metadata = with_invocation_id(
        {
            **{
                key: value
                for key, value in run_metadata.items()
                if key not in TURN_ATTRIBUTION_FIELDS
                and key
                not in {
                    "visibility",
                    "owner_type",
                    "owner_login",
                    "owner_user_id",
                    "system_authorization",
                    "automation_creator_user_id",
                    "automation_system",
                    "automation_scope",
                    "schedule_id",
                    "thread_category",
                    "source",
                    "origin",
                    "created_by",
                    "admin_thread",
                    "workspace",
                    "environment",
                }
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

    origin = RunOrigin.resolve(relayed_origin) if relayed_from else RunOrigin.DASHBOARD
    merged_configurable["origin"] = origin.value
    run_metadata["origin"] = origin.value
    if offloading:
        merged_configurable["offload_conversation"] = True
        params["input"] = {}

    params["assistant_id"] = _ASSISTANT_ID
    run_metadata.update(
        await _run_user_metadata({"configurable": merged_configurable}, source=DASHBOARD_SOURCE)
    )
    # The person sent this message, so its model calls bill them (as
    # ``dispatch.prepare_durable_run`` stamps a run they start); a relayed one is no one's.
    sender = run_metadata.get("user_id")
    if relayed_from is None and isinstance(sender, str):
        run_metadata[SENDER_USER_KEY] = sender
    config_metadata = client_config.get("metadata")
    if not isinstance(config_metadata, dict):
        config_metadata = {}
    params["config"] = {
        **client_config,
        "configurable": merged_configurable,
        "metadata": {
            **{k: v for k, v in config_metadata.items() if k not in TURN_ATTRIBUTION_FIELDS},
            **run_metadata,
        },
    }
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
    turn: OpenTurn | None,
    email: str | None = None,
    relayed_origin: RunOrigin | None = None,
    run_format: RunFormat = SERVER_STREAM,
) -> SentRun:
    """Deliver a ``run.start`` sent while a run is live into that run.

    The message joins ``turn`` instead of opening a new one: it is recorded on
    the transcript right away and left for the run to pick up before its next
    model call. A run started for the message because the live one ended meanwhile
    streams in ``run_format``.
    """
    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
    content = _command_message_content(params)
    replies = _command_message_replies(params)
    relayed_from = _relayed_from(params)
    command_images = _dashboard_images_from_content(content)
    if DashboardImageBody.files_in(content):
        raise HTTPException(409, "files can be attached once the agent has finished its run")
    if not _command_prompt_text(content) and not command_images and not replies:
        raise HTTPException(422, "a follow-up needs a message")

    client = langgraph_client()
    latest_run_id = metadata.get("latest_run_id")
    # A turn learns its run id only once the run starts or is queued.
    live_run_id = turn.run_id if turn is not None else None
    if live_run_id is None and isinstance(latest_run_id, str) and latest_run_id:
        live_run_id = latest_run_id
    # The run keeps the model it started with, so images are held to it. Thread
    # metadata may already name the model of a follow-up queued behind it.
    live_run_metadata, history = await asyncio.gather(
        _run_metadata(client, thread_id, live_run_id), _ThreadHistory.of(thread_id)
    )
    run_model = live_run_metadata.get(RUN_MODEL_KEY)
    image_blocks = _image_blocks(
        command_images,
        model_id=run_model if isinstance(run_model, str) else thread_model_choice(metadata)[0],
    )

    structured, _ = await _attributed_run_messages(
        login,
        metadata=metadata,
        history=history,
        content=content,
        email=email,
        relayed_from=relayed_from,
        replies=replies,
    )
    client_message_id = _command_message_id(params)
    message_id = (
        client_message_id
        if client_message_id and client_message_id not in history.message_ids
        else str(uuid.uuid7())
    )
    structured[-1]["id"] = message_id

    if turn is not None:
        turn_id = turn.turn_id
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
            **ThreadIdentity(login).person,
            "platform": "langsmith" if ThreadIdentity(login).user_id else "github",
            **({"email": email} if email else {}),
        },
        "queue_id": message_id,
        REPLIES_KEY: [reply.model_dump() for reply in replies],
        "surface": "automation" if relayed_from else "web",
        "created_at_ms": _now_ms(),
    }
    if relayed_from:
        payload[RELAYED_FROM_KEY] = relayed_from
        payload["origin"] = RunOrigin.resolve(relayed_origin).value
    elif metadata.get("source") == "slack":
        payload["source"] = DASHBOARD_SOURCE
    if not await queue_message_for_thread(thread_id, payload):
        raise HTTPException(502, "failed to deliver the follow-up to the running agent")
    # The run may have ended between the busy check and the store write, past
    # the completion hook's own look at the store. ``reject`` keeps the two
    # from racing each other into a second run.
    if not await _run_is_live(client, thread_id, live_run_id):
        try:
            dispatched = await dispatch_pending_follow_ups(
                thread_id,
                login,
                metadata,
                client=client,
                multitask_strategy="reject",
                run_format=run_format,
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
            **await participant_metadata(metadata, login=login, email=email),
        },
    )
    if not relayed_from:
        await _SlackWebHandoff(thread_id, metadata).notify()
    return SentRun(thread_id=thread_id, run_id=live_run_id, message_id=message_id, steered=True)


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
    relayed_origin: RunOrigin | None = None,
    run_format: RunFormat = SERVER_STREAM,
) -> SentRun:
    """Hold a ``run.start`` sent while a run is live until that run ends.

    The message gets its own run, created with LangGraph's ``enqueue`` strategy
    so the platform starts it when the thread goes idle, from any client and
    with this browser long gone. The transcript records the turn as requested
    right away and learns the run id so it can be cancelled before it starts.
    The run streams in ``run_format``.
    """
    params = command.get("params")
    # An agent's relayed message is not its sender's own: it acts as no one.
    relayed = isinstance(params, dict) and bool(params.get(RELAYED_FROM_KEY))
    handoff = _SlackWebHandoff(thread_id, metadata)
    enriched = await _enrich_run_start_command(
        thread_id,
        login,
        command,
        metadata=metadata,
        email=email,
        relayed_origin=relayed_origin,
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
            origin=configurable["origin"],
            thread_title=None,
            client=langgraph_client(),
            multitask_strategy="enqueue",
            # On a channel's thread it starts as its sender, as their message would.
            as_sender=not relayed,
            run_format=run_format,
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

    if chats_beside_plugin(metadata):
        user = await User.for_person(ThreadIdentity(login).person)
        if user:
            await PluginChat.deliver_input(thread_id, user.id, run_input)

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
    if not relayed:
        await handoff.notify()
    return SentRun(thread_id=thread_id, run_id=run_id, queued=True)


async def dispatch_pending_follow_ups(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    client: Any,
    multitask_strategy: str = "interrupt",
    run_format: RunFormat = SERVER_STREAM,
) -> str | None:
    """Start a run for follow-ups a finished run never got to.

    A message steered into a run after its last model call is still waiting in
    the store; the new run's first model call picks it up. Returns the run id,
    or ``None`` when nothing was waiting. The run answers, and acts as, the person
    who sent the newest of them from the web; otherwise it is ``login``'s and acts
    as no one.
    """
    queued = await QueuedMessage.for_thread(thread_id)
    if not queued:
        return None
    sender = queued[-1].web_sender
    configurable = await _build_dashboard_configurable(thread_id, sender or login, metadata)
    run = await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source=DASHBOARD_SOURCE,
        origin=queued[-1].origin.value,
        thread_title=None,
        input={"messages": []},
        metadata={"kind": FOLLOW_UP_PICKUP_KIND},
        client=client,
        multitask_strategy=multitask_strategy,
        as_sender=sender is not None,
        run_format=run_format,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else None
    return run_id if isinstance(run_id, str) else None


class _SlackWebHandoff:
    def __init__(self, thread_id: str, metadata: Mapping[str, object]) -> None:
        self.thread_id = thread_id
        slack_thread = SourceContext.from_metadata(metadata).slack_thread
        self.location = (
            slack_thread.location
            if metadata.get("source") == "slack" and slack_thread and not slack_thread.is_dm
            else None
        )

    async def notify(self) -> None:
        if self.location is None:
            return
        url = dashboard_thread_url(self.thread_id)
        if not url:
            return
        notice = SlackNotice(kind="web_handoff", key=self.thread_id)
        text = f"<{url}|Continued on Web>"
        try:
            latest = await fetch_latest_slack_thread_message(*self.location)
            if latest is not None and (
                notice.repeats(latest) or await self.update_footer(latest, url)
            ):
                return
            await post_slack_thread_reply(
                *self.location,
                text,
                blocks=[
                    {
                        "type": "context",
                        "block_id": notice.block_id,
                        "elements": [{"type": "mrkdwn", "text": text}],
                    }
                ],
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception:
            logger.exception(
                "Failed to post Slack web handoff notice",
                extra={"thread_id": self.thread_id},
            )

    async def update_footer(self, message: Mapping[str, object], url: str) -> bool:
        bot_user_id = ENV.SLACK_BOT_USER_ID.get()
        message_ts = message.get("ts")
        if not bot_user_id or message.get("user") != bot_user_id or not isinstance(message_ts, str):
            return False
        blocks = message.get("blocks")
        if not isinstance(blocks, list):
            return False
        old_links = (f"<{url}|Open in Robin>", f"<{url}|Open in Web>")
        new_link = f"<{url}|Continued in Web>"
        updated_blocks = copy.deepcopy(blocks)
        changed = False
        for block in updated_blocks:
            if not isinstance(block, dict) or block.get("type") != "context":
                continue
            elements = block.get("elements")
            for element in elements if isinstance(elements, list) else []:
                if not isinstance(element, dict) or element.get("type") != "mrkdwn":
                    continue
                text = element.get("text")
                if isinstance(text, str) and new_link in text:
                    return True
                for old_link in old_links:
                    if isinstance(text, str) and old_link in text:
                        element["text"] = text.replace(old_link, new_link)
                        changed = True
        if not changed or self.location is None:
            return False
        text = message.get("text")
        updated_text = text if isinstance(text, str) else ""
        for old_link in old_links:
            updated_text = updated_text.replace(old_link, new_link)
        await update_slack_message(
            self.location[0],
            message_ts,
            updated_text,
            blocks=updated_blocks,
            unfurl_links=False,
            unfurl_media=False,
        )
        return True


async def _create_system_thread_record(
    thread_id: str,
    principal: Principal,
    *,
    prompt: str,
    title: str | None,
    repo_config: dict[str, str],
) -> dict[str, Any]:
    """Stamp a system-owned thread rather than a person-owned one.

    Deliberately not built from :func:`create_dashboard_thread_record`: there is
    no profile to read defaults from, no owner to record, and no participant to
    merge, and inheriting those would give the thread a person it does not have.
    """
    now_ms = _now_ms()
    metadata: dict[str, Any] = {
        "source": API_SOURCE,
        "origin": API_SOURCE,
        "thread_category": "automation",
        "trigger_kind": "api",
        "automation_scope": "system",
        "owner_type": "system",
        "visibility": "public",
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
    if principal.token_repositories is not None:
        metadata[GITHUB_TOKEN_REPOSITORIES_KEY] = list(principal.token_repositories)
    client = langgraph_client()
    await create_thread(
        client, thread_id, title=metadata["title"], metadata=metadata, if_exists="raise"
    )
    return as_thread_dict(await client.threads.get(thread_id))


async def _system_repo_config(
    configurable: Mapping[str, Any], principal: Principal
) -> dict[str, str]:
    """The repository a machine's thread works in.

    Any repository the GitHub App can reach will do, not only ones its
    global defaults prefer. A federated workflow that names none gets its own repository, which is the
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
    await require_repo_access_for_system(f"{repo_config['owner']}/{repo_config['name']}")
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
    the system-owned thread and run config and leaves the rest of the command
    pipeline — forwarding, streaming, run bookkeeping — shared. The one choice a
    caller makes is the model: a supported model and effort it names runs instead
    of the global default.
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

    requested_model = client_configurable.get("agent_model_id")
    requested_effort = client_configurable.get("agent_effort")
    model_id, effort = normalize_model_choice(requested_model, requested_effort)
    if (requested_model is not None or requested_effort is not None) and model_id is None:
        raise HTTPException(
            422,
            "agent_model_id and agent_effort must name a supported model and one of its efforts",
        )

    content = _command_message_content(params)
    prompt = _command_prompt_text(content)
    if not prompt.strip():
        raise HTTPException(422, "a run needs a prompt")
    if _dashboard_images_from_content(content) or DashboardImageBody.files_in(content):
        raise HTTPException(422, "machine principals cannot attach images or files")

    if any(client_configurable.get(key) is not None for key in _MAC_THREAD_KEYS):
        raise HTTPException(410, MAC_THREAD)
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
    else:
        await principal.assert_can_post(metadata)

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
    # A client finds its own message in the thread's state by the id it minted,
    # unless that id is taken: the graph would overwrite the earlier message.
    client_message_id = _command_message_id(params)
    if client_message_id and (
        creating or client_message_id not in (await _ThreadHistory.of(thread_id)).message_ids
    ):
        structured[-1]["id"] = client_message_id
    run_input = params.get("input")
    if isinstance(run_input, dict):
        run_input["messages"] = structured
    else:
        params["input"] = {"messages": structured}

    configurable: dict[str, Any] = with_invocation_id(
        {
            "thread_id": thread_id,
            "source": API_SOURCE,
            "origin": API_SOURCE,
            STARTED_BY_ID: principal.started_by_id,
            "background_task_completion": False,
        },
        invocation_id,
    )
    configurable["invocation_started_at"] = datetime.now(UTC).isoformat()
    stored_repo = repo_config_from_metadata(metadata)
    if stored_repo:
        configurable["repo"] = stored_repo
    elif repo_config:
        configurable["repo"] = repo_config
    if model_id is not None:
        configurable["agent_model_id"] = model_id
        configurable["agent_effort"] = effort
        configurable["model_selection"] = "explicit"

    params["assistant_id"] = _ASSISTANT_ID
    params["config"] = {**client_config, "configurable": configurable}
    params["metadata"] = with_invocation_id(
        {**agent_version_metadata(), "origin": API_SOURCE}, invocation_id
    )
    command["params"] = params
    return command
