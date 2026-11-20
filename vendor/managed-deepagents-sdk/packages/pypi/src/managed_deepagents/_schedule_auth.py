from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, NotRequired, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from langgraph_sdk import Auth
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.http.studio import _GRAPH_NAMESPACE
from managed_deepagents._channels.schedule_delivery import read_schedule_deliver_to
from managed_deepagents._channels.source_providers import is_trusted_source_provider
from managed_deepagents._identity_runtime import _auth_user_fields, _field, is_studio_auth_user
from managed_deepagents._langsmith_env import resolve_server_api_key
from managed_deepagents._loopback import (
    is_loopback_url,
    resolve_loopback_api_url,
)
from managed_deepagents._thread_record import (
    THREAD_CONVERSATION_METADATA,
    THREAD_PARTICIPANTS_METADATA,
    THREAD_RECORD_METADATA,
    is_person_principal,
    parse_thread_record,
    strip_thread_record_keys,
)
from managed_deepagents.run_context import ChannelContext, is_managed_key

if TYPE_CHECKING:
    from langgraph_sdk.auth.types import (
        BaseUser,
        CronsCreate,
        CronsDelete,
        CronsRead,
        CronsSearch,
        CronsUpdate,
        FilterType,
    )

_MAX_HANDOFF_SIZE = 8192

SCHEDULE_HANDOFF_HEADER = "x-mda-schedule-handoff"
THREAD_HANDOFF_HEADER = "x-mda-thread-handoff"
SANDBOX_BINDING_HEADER = "x-mda-sandbox-binding"
_SANDBOX_BINDING_USER = "mda:sandbox-binding"
_SEARCH_PAGE = 100
SCHEDULE_LIMIT_PREFIX = "Schedule limit reached"


@with_config(ConfigDict(extra="forbid"))
class ExecutionUser(TypedDict):
    identity: str
    permissions: list[str]
    mda_user_id: str
    mda_user_kind: NotRequired[Literal["user", "service"]]
    mda_agent_auth_principal_id: NotRequired[str]
    mda_identity_provider: NotRequired[str]
    mda_identity_issuer: NotRequired[str]
    mda_identity_subject: NotRequired[str]
    mda_source_provider: NotRequired[str]
    mda_source_thread_id: NotRequired[str]
    # Marks cron runs; channel provider overrides leave it in place.
    mda_scheduled_run: NotRequired[Literal[True]]


@with_config(ConfigDict(extra="forbid"))
class ScheduleRunAs(TypedDict):
    """The thread participant a schedule runs as."""

    id: str
    role: Literal["owner", "member"]


@with_config(ConfigDict(extra="forbid"))
class ScheduleAccess(TypedDict):
    owner: str
    assistant: str
    execution: ExecutionUser
    thread: NotRequired[str]
    #: Set when a user schedule runs as another participant of the owner's thread.
    run_as: NotRequired[ScheduleRunAs]


class ScheduleHandoffUser(ExecutionUser, total=False):
    """Execution user returned by the schedule handoff; never validated as input.

    ``mda_schedule_access`` is present for cron-route handoffs.
    ``mda_schedule_thread`` is present instead for the persistent-thread create
    and read, and names the only thread they may touch. ``ExecutionUser`` keeps
    ``permissions`` required as a ``list``, which
    ``langgraph_sdk.auth.types.MinimalUserDict`` cannot express.
    """

    mda_schedule_access: ScheduleAccess
    mda_schedule_thread: str


@with_config(ConfigDict(extra="forbid"))
class ThreadHandoffConversation(TypedDict):
    """The conversation a scheduled run's post started."""

    provider: str
    source_thread_key: str


@with_config(ConfigDict(extra="forbid"))
class ThreadHandoffGrant(TypedDict):
    """The thread and scheduled run a handoff caller may move."""

    thread: str
    execution: ExecutionUser


@with_config(ConfigDict(extra="forbid"))
class ThreadHandoff(ThreadHandoffGrant):
    """A scheduled run handing its thread to the conversation its post started."""

    conversation: ThreadHandoffConversation


class ThreadHandoffUser(ExecutionUser, total=False):
    """The conversation a thread handoff names, as a channel caller; never validated as input."""

    mda_thread_handoff: ThreadHandoffGrant


@with_config(ConfigDict(extra="forbid"))
class SandboxBindingGrant(TypedDict):
    """The thread whose sandbox binding the runtime reads or writes."""

    thread: str


class SandboxBindingUser(ExecutionUser):
    """The runtime, reading or writing one thread's sandbox binding; never validated as input."""

    mda_sandbox_binding: str


class _CronRow(TypedDict, total=False):
    cron_id: str
    end_time: str | None


_EXECUTION = TypeAdapter(ExecutionUser)
_ACCESS = TypeAdapter(ScheduleAccess)
_CHANNEL = TypeAdapter(ChannelContext)
_CRON_ROWS = TypeAdapter(list[_CronRow])
_HANDOFF = TypeAdapter(ThreadHandoff)
_HANDOFF_GRANT = TypeAdapter(ThreadHandoffGrant)
_SANDBOX_BINDING = TypeAdapter(SandboxBindingGrant)
_EXECUTION_KEYS = ExecutionUser.__required_keys__ | ExecutionUser.__optional_keys__
_HANDOFF_TIMEOUT_SECONDS = 15


def schedule_request_config(access: ScheduleAccess) -> tuple[str, dict[str, str]]:
    """Use the deployed API URL and server key, or the local dev API."""
    return _handoff_request_config(SCHEDULE_HANDOFF_HEADER, access)


def _handoff_request_config(header: str, value: object) -> tuple[str, dict[str, str]]:
    hosted = os.environ.get("LANGSMITH_HOST_API_URL", "").strip().rstrip("/")
    base = hosted or resolve_loopback_api_url()
    secret = os.environ.get("MDA_HANDOFF_SECRET")
    api_key = resolve_server_api_key()
    if not secret or (
        not is_loopback_url(base) and not (hosted.startswith("https://") and api_key)
    ):
        raise ValueError("Schedules require an authenticated Agent Server")
    headers = {
        "content-type": "application/json",
        "x-mda-handoff-secret": secret,
        header: json.dumps(value),
    }
    if hosted and api_key:
        headers["x-api-key"] = api_key
    return base, headers


def validate_execution(value: object) -> ExecutionUser:
    try:
        result = _EXECUTION.validate_python(value, strict=True)
    except ValidationError:
        raise ValueError("Invalid schedule execution identity") from None
    if not result["identity"] or not result["mda_user_id"] or result["permissions"]:
        raise ValueError("Invalid schedule execution identity")
    return result


def channel_run_execution(configurable: Mapping[str, object] | None) -> ExecutionUser | None:
    """The principal a channel conversation's run runs as, from the run's configurable.

    It is the caller ingress stamped with the conversation's provider and source
    thread key, which own the run's thread.
    """
    user = _auth_user_fields(_field(configurable, "langgraph_auth_user"))
    if user is None:
        return None
    try:
        execution = validate_execution(
            {key: value for key, value in user.items() if key in _EXECUTION_KEYS}
        )
    except ValueError:
        return None
    return execution if _is_channel_run_execution(execution) else None


def _is_channel_run_execution(execution: ExecutionUser) -> bool:
    return is_trusted_source_provider(execution.get("mda_source_provider", "")) and bool(
        execution.get("mda_source_thread_id", "").strip()
    )


def schedule_execution(configurable: Mapping[str, object] | None) -> ExecutionUser | None:
    """The principal a managed cron fire runs as, from the run's configurable."""
    user = _auth_user_fields(_field(configurable, "langgraph_auth_user"))
    if user is None:
        return None
    try:
        execution = validate_execution(
            {key: value for key, value in user.items() if key in _EXECUTION_KEYS}
        )
    except ValueError:
        return None
    return execution if execution.get("mda_scheduled_run") is True else None


def _verified_handoff(headers: Mapping[str, object], raw: object) -> str:
    """A handoff header's value, unless the request lacks the handoff secret."""
    expected = os.environ.get("MDA_HANDOFF_SECRET")
    supplied = headers.get("x-mda-handoff-secret")
    if (
        not expected
        or not isinstance(supplied, str)
        or not hmac.compare_digest(expected.encode(), supplied.encode())
        or not isinstance(raw, str)
        or len(raw) > _MAX_HANDOFF_SIZE
    ):
        raise ValueError("Schedule access is not permitted")
    return raw


#: The waits, in seconds, before each retry of a handoff a retry can fix.
_HANDOFF_RETRY_DELAYS_SECONDS = (0.5, 2.0)


async def hand_off_scheduled_thread(handoff: ThreadHandoff, metadata: Mapping[str, object]) -> None:
    """Move a scheduled or channel run's thread to the conversation its post started.

    ``metadata`` holds that conversation's record and key. The handoff can only
    follow the post, which names the conversation, so a failed request or an
    Agent Server error is retried. A retried run finds the thread already moved.
    """
    base, headers = _handoff_request_config(THREAD_HANDOFF_HEADER, handoff)
    url = f"{base}/threads/{handoff['thread']}"
    async with httpx.AsyncClient(
        timeout=_HANDOFF_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for delay in (*_HANDOFF_RETRY_DELAYS_SECONDS, None):
            try:
                status = await _move_thread(client, url, headers, metadata, handoff)
            except httpx.HTTPError:
                status = None
            if status == "moved":
                return
            if delay is None or (status is not None and status < HTTPStatus.INTERNAL_SERVER_ERROR):
                suffix = "" if status is None else f" with status {status}"
                raise RuntimeError(f"Thread handoff failed{suffix}")
            await asyncio.sleep(delay)


async def _move_thread(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    metadata: Mapping[str, object],
    handoff: ThreadHandoff,
) -> Literal["moved"] | int:
    """One handoff attempt: ``"moved"``, or the status that refused it."""
    moved = await client.patch(url, headers=headers, json={"metadata": metadata})
    if moved.is_success:
        return "moved"
    if moved.status_code != HTTPStatus.NOT_FOUND:
        return moved.status_code
    current = await client.get(url, headers=headers)
    key = handoff["conversation"]["source_thread_key"]
    if current.is_success and _held_conversation(current.json()) == key:
        return "moved"
    return moved.status_code


def _held_conversation(thread: object) -> object:
    """The conversation key a thread's metadata names, if any."""
    return _field(_field(thread, "metadata"), THREAD_CONVERSATION_METADATA)


def thread_handoff_user(
    headers: Mapping[str, object], path: str | None, method: str | None
) -> ThreadHandoffUser | None:
    """The conversation a thread handoff names, as a channel caller.

    It may only update the scheduled or channel run's own thread, once, or read it
    to check.
    """
    raw = headers.get(THREAD_HANDOFF_HEADER)
    if raw is None:
        return None
    handoff = _HANDOFF.validate_python(json.loads(_verified_handoff(headers, raw)), strict=True)
    thread = handoff["thread"]
    UUID(thread)
    execution = validate_execution(handoff["execution"])
    conversation = handoff["conversation"]
    if (
        method not in {"PATCH", "GET"}
        or path != f"/threads/{thread}"
        or (
            execution.get("mda_scheduled_run") is not True
            and not _is_channel_run_execution(execution)
        )
        or not conversation["source_thread_key"]
        or not is_trusted_source_provider(conversation["provider"])
    ):
        raise ValueError("Thread handoff is not permitted")
    return {
        **execution,
        "mda_source_provider": conversation["provider"],
        "mda_source_thread_id": conversation["source_thread_key"],
        "mda_thread_handoff": {"thread": thread, "execution": execution},
    }


def sandbox_binding_user(
    headers: Mapping[str, object], path: str | None, method: str | None
) -> SandboxBindingUser | None:
    """The runtime, reading a thread or writing its sandbox binding, whoever owns the thread."""
    raw = headers.get(SANDBOX_BINDING_HEADER)
    if raw is None:
        return None
    grant = _SANDBOX_BINDING.validate_python(
        json.loads(_verified_handoff(headers, raw)), strict=True
    )
    thread = grant["thread"]
    UUID(thread)
    if method not in {"PATCH", "GET"} or path != f"/threads/{thread}":
        raise ValueError("Sandbox binding access is not permitted")
    return {
        "identity": _SANDBOX_BINDING_USER,
        "permissions": [],
        "mda_user_id": _SANDBOX_BINDING_USER,
        "mda_user_kind": "service",
        "mda_sandbox_binding": thread,
    }


def sandbox_binding_grant(user: object) -> str | None:
    """The thread whose sandbox binding a caller may read and write, if it is the runtime."""
    value = _field(user, "mda_sandbox_binding")
    return value if isinstance(value, str) else None


def schedule_thread_grant(user: object) -> str | None:
    """The persistent thread a deploy schedule's handoff may create and read, if it is one."""
    value = _field(user, "mda_schedule_thread")
    return value if isinstance(value, str) else None


def thread_handoff_grant(user: object) -> ThreadHandoffGrant | None:
    """The thread and the run's principal a handoff caller may move, if it is one."""
    value = _field(user, "mda_thread_handoff")
    if value is None:
        return None
    try:
        return _HANDOFF_GRANT.validate_python(value, strict=True)
    except ValidationError:
        return None


def schedule_handoff_user(
    headers: Mapping[str, object], path: str | None, method: str | None
) -> ScheduleHandoffUser | None:
    raw = headers.get(SCHEDULE_HANDOFF_HEADER)
    if raw is None:
        return None
    access = _ACCESS.validate_python(json.loads(_verified_handoff(headers, raw)), strict=True)
    # A person's API cron update names no assistant, so its limit search spans all of them.
    if access["assistant"] or (method, path) != ("POST", "/runs/crons/search"):
        UUID(access["assistant"])
    if "thread" in access:
        UUID(access["thread"])
        reads = method == "GET" and path == f"/threads/{access['thread']}"
        # Only deploy creates a persistent thread, for an agent-owned schedule.
        creates = method == "POST" and path == "/threads" and access["owner"] == "agent"
        if not (reads or creates):
            raise ValueError("Schedule access is not permitted")
    elif not re.fullmatch(r"/(?:threads/[^/]+/)?runs/crons(?:/[^/]+)?", path or ""):
        raise ValueError("Schedule access is not permitted")
    execution = validate_execution(access["execution"])
    if access["owner"] == "agent":
        if execution.get("mda_user_kind") != "service" or execution.get(
            "mda_agent_auth_principal_id"
        ):
            raise ValueError("Schedule access is not permitted")
    # A user schedule runs as a person: its owner, or (``run_as``) another
    # participant of the owner's thread.
    elif (
        execution["mda_user_id"]
        not in (access["owner"].removeprefix("user:"), access.get("run_as", {}).get("id"))
        or not access["owner"].startswith("user:")
        or execution.get("mda_user_kind") == "service"
    ):
        raise ValueError("Schedule access is not permitted")
    user: ScheduleHandoffUser = {**execution}
    if "thread" in access:
        user["mda_schedule_thread"] = access["thread"]
    else:
        user["mda_schedule_access"] = access
    return user


def schedule_access(user: object) -> ScheduleAccess | None:
    value = _field(user, "mda_schedule_access")
    if value is None:
        return None
    try:
        return _ACCESS.validate_python(value, strict=True)
    except ValidationError:
        return None


async def authorize_schedule(  # noqa: C901 - one branch per cron event check
    user: BaseUser,
    action: str,
    value: CronsCreate | CronsRead | CronsUpdate | CronsDelete | CronsSearch,
) -> FilterType | None:
    access = schedule_access(user)
    if not isinstance(value, dict):
        raise ValueError("Schedule access is not permitted")
    # Cron payloads carry ``metadata`` on the wire although the upstream
    # ``CronsCreate`` / ``CronsUpdate`` TypedDicts do not declare it.
    data = cast("dict[str, object]", value)
    _guard_cron_record(action, data, access)
    if access is None:
        from managed_deepagents._identity_auth import is_local_dev_unscoped_user  # noqa: PLC0415

        operator = is_studio_auth_user(user) or _is_local_dev_operator(user)
        if operator or is_local_dev_unscoped_user(user):
            if action == "delete" and not operator:
                return {"mda_managed_by": "api"}
            return await _authorize_studio_schedule(action, data, deploy=operator)
        access = _person_access(user, action, data)
    metadata = data.get("metadata")
    payload = data.get("payload")
    filters = {"mda_schedule_owner": access["owner"]}
    if access["assistant"]:
        filters["mda_schedule_assistant"] = access["assistant"]
    if action in {"update", "delete"}:
        filters["mda_managed_by"] = "runtime"
    if action == "create":
        if (
            not isinstance(payload, dict)
            or _assistant_id(payload.get("assistant_id"), _GRAPH_NAMESPACE) != access["assistant"]
            or not isinstance(metadata, dict)
            or metadata.get("mda_managed_by") != "runtime"
        ):
            raise ValueError("Schedule access is not permitted")
        metadata.update(filters)
        metadata["mda_managed"] = True
        _stamp_owner(data, access["execution"])
        await _check_schedule_limit(access)
    _check_context(payload, access["execution"], "runtime")
    if action == "create" or (
        action == "update" and isinstance(payload, dict) and "config" in payload
    ):
        if not isinstance(payload, dict):
            raise ValueError("Schedule access is not permitted")
        _stamp_execution(payload, access["execution"])
    if (
        action == "update"
        and isinstance(metadata, dict)
        and any(key.startswith("mda_") and key != "mda_schedule_spec" for key in metadata)
    ):
        raise ValueError("Schedule access is not permitted")
    if action == "update" and data.get("schedule") is not None:
        await _check_schedule_limit(access, str(data.get("cron_id")))
    return filters


def _person_access(user: BaseUser, action: str, data: dict[str, object]) -> ScheduleAccess:
    """A person's own API crons, which run as them like their runtime user schedules."""
    user_id = _field(user, "mda_user_id")
    if (
        not is_person_principal(user_id)
        or _field(user, "mda_user_kind") == "service"
        or _field(user, "mda_source_provider") is not None
    ):
        raise ValueError("Schedule access is not permitted")
    # Agent Auth resolves the principal from these identity fields when the cron fires.
    execution = validate_execution(
        {
            **{key: _field(user, key) for key in _PERSON_IDENTITY_KEYS if _field(user, key)},
            "identity": user_id,
            "permissions": [],
            "mda_user_id": user_id,
            "mda_source_provider": "schedule",
            "mda_scheduled_run": True,
        }
    )
    payload = data.get("payload")
    if action == "create":
        _prepare_person_cron(data)
    # Context and config carry a runtime schedule's routing and principal; recreate to change them.
    elif (isinstance(payload, dict) and {"assistant_id", "context", "config"} & payload.keys()) or (
        isinstance(data.get("metadata"), dict)
        and "owner" in cast("dict[str, object]", data["metadata"])
    ):
        raise ValueError("Schedule access is not permitted")
    return {
        "owner": f"user:{user_id}",
        "assistant": _assistant_id(_field(payload, "assistant_id"), _GRAPH_NAMESPACE) or "",
        "execution": execution,
    }


_PERSON_IDENTITY_KEYS = (
    "mda_agent_auth_principal_id",
    "mda_identity_provider",
    "mda_identity_issuer",
    "mda_identity_subject",
)


def _prepare_person_cron(data: dict[str, object]) -> None:
    """Tag a person's new API cron as their runtime user schedule."""
    if data.get("thread_id"):
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="Schedule access is not permitted: API crons must create a new thread per run",
        )
    metadata = data.get("metadata")
    if metadata is None:
        metadata = data["metadata"] = {}
    payload = data.get("payload")
    if (
        not isinstance(metadata, dict)
        or any(key.startswith("mda_") or key == "owner" for key in metadata)
        or not isinstance(payload, dict)
    ):
        raise ValueError("Schedule access is not permitted")
    metadata["mda_managed_by"] = "runtime"
    _wrap_person_context(payload)


def _wrap_person_context(payload: dict[str, object]) -> None:
    """Add the schedule name runtime crons carry to the caller's authored context."""
    _check_api_context(payload)
    context = payload.get("context") or {}
    payload["context"] = {
        "mda_schedule": {"name": str(uuid4())},
        **cast("dict[str, object]", context),
    }


def _assistant_id(value: object, namespace: UUID = NAMESPACE_URL) -> str | None:
    """An assistant UUID, or a UUID derived from a graph name."""
    if not isinstance(value, str):
        return None
    try:
        UUID(value)
    except ValueError:
        return str(uuid5(namespace, value))
    return value


def _guard_cron_record(action: str, data: dict[str, object], access: ScheduleAccess | None) -> None:
    """Keep a thread record only on a runtime new-thread schedule, for its execution.

    The Agent Server copies a cron's metadata into the thread it creates, so a
    new-thread schedule's record becomes that thread's. A thread's runs merge
    cron metadata over thread metadata, so a same-thread schedule never carries
    one; deploy schedules and updates never add one either.
    """
    metadata = data.get("metadata")
    payload = data.get("payload")
    keep = (
        action == "create"
        and access is not None
        and not data.get("thread_id")
        and isinstance(metadata, dict)
        and is_execution_record(metadata, access["execution"])
    )
    if keep and isinstance(metadata, dict):
        record = {key: metadata[key] for key in _RECORD_KEYS}
        strip_thread_record_keys(metadata)
        metadata.update(record)
    else:
        strip_thread_record_keys(metadata)
    run_metadata = _field(payload, "metadata")
    if run_metadata is not metadata:
        strip_thread_record_keys(run_metadata)
    strip_thread_record_keys(_field(_field(payload, "config"), "metadata"))


_RECORD_KEYS = (THREAD_RECORD_METADATA, THREAD_PARTICIPANTS_METADATA)


def is_execution_record(metadata: object, execution: object) -> bool:
    """A record of the execution's conversation, owned by the execution or by no one."""
    record = parse_thread_record(metadata, exact=True)
    if record is None or record.origin is None:
        return False
    channel = record.origin.channel
    return (
        channel.provider == _field(execution, "mda_source_provider")
        and channel.source_thread_key == _field(execution, "mda_source_thread_id")
        and dict(record.participants) in ({}, {_field(execution, "mda_user_id"): "owner"})
    )


_LOCAL_DEV_ENV = "MDA_LOCAL_DEV"
_LOCAL_DEV_USER = "mda:local-dev"


def _is_local_dev_operator(user: object) -> bool:
    """The ``mda dev`` operator, which provisions ``schedules/`` locally.

    Local builds turn the Studio auth scheme off, so ``mda dev`` reconciles
    deploy-managed crons as ``mda:local-dev`` instead of as Studio, over the
    handoff secret it staged. Only that verified caller counts: a request
    without a valid secret falls back to a synthetic local *service* principal
    whose id the client chose, so service principals are refused. Deploy never
    sets ``MDA_LOCAL_DEV``.
    """
    return (
        os.environ.get(_LOCAL_DEV_ENV) == "1"
        and _field(user, "mda_user_id") == _LOCAL_DEV_USER
        and _field(user, "mda_user_kind") != "service"
    )


async def _authorize_studio_schedule(
    action: str, data: dict[str, object], *, deploy: bool
) -> FilterType | None:
    # Studio identity is reachable by any workspace API key. It may create a deploy-managed
    # cron or an untagged one, which is stamped "api" and is the only kind it may update.
    # Read/search stay unscoped, and delete too for the operator, so `mda deploy` (and
    # `mda dev` locally) can still list and tear down managed crons.
    if action == "update":
        return _authorize_api_schedule_update(data)
    if action != "create":
        return None
    metadata = data.get("metadata")
    payload = data.get("payload")
    if metadata is None:
        metadata = data["metadata"] = {}
    if (
        not isinstance(metadata, dict)
        or not isinstance(payload, dict)
        or not isinstance(payload.get("assistant_id"), str)
    ):
        raise ValueError("Schedule access is not permitted")
    api = not any(map(is_managed_key, metadata))
    # Plain `mda dev` callers get API crons only; `schedules/` stays the operator's.
    if not api and (
        not deploy
        or metadata.get("mda_managed") is not True
        or metadata.get("mda_managed_by") != "deploy"
    ):
        raise ValueError("Schedule access is not permitted")
    assistant = str(_assistant_id(payload["assistant_id"]))
    if api:
        # Not `mda_managed`: deploy reconciliation and runtime schedule tools leave it alone.
        metadata["mda_managed_by"] = "api"
    else:
        metadata.update(mda_schedule_owner="agent", mda_schedule_assistant=assistant)
    execution: ExecutionUser = {
        "identity": f"mda:schedule:{assistant}",
        "permissions": [],
        "mda_user_id": f"mda:schedule:{assistant}",
        "mda_user_kind": "service",
        "mda_source_provider": "schedule",
        "mda_scheduled_run": True,
    }
    if data.get("thread_id"):
        await _ensure_persistent_thread(
            {
                "owner": "agent",
                "assistant": assistant,
                "execution": execution,
                "thread": str(data["thread_id"]),
            }
        )
    if api:
        _check_api_context(payload)
    else:
        _check_context(payload, execution, "deploy")
    _stamp_execution(payload, execution)
    _stamp_owner(data, execution)
    return None


def _stamp_owner(data: dict[str, object], execution: ExecutionUser) -> None:
    """Own a new cron's threads by its execution, never its assistant's owner.

    A cron's threads take its metadata over its assistant's, and person-owned
    assistants carry their creator under ``owner``.
    """
    from managed_deepagents._identity_auth import _thread_owner_key  # noqa: PLC0415

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata["owner"] = _thread_owner_key(execution)


def _authorize_api_schedule_update(data: dict[str, object]) -> FilterType:
    """An API cron keeps its stamped assistant and principal; only its other fields change."""
    metadata = data.get("metadata")
    payload = data.get("payload")
    if (isinstance(metadata, dict) and any(map(is_managed_key, metadata))) or (
        isinstance(payload, dict) and {"assistant_id", "config"} & payload.keys()
    ):
        raise ValueError("Schedule access is not permitted")
    _check_api_context(payload)
    return {"mda_managed_by": "api"}


def _check_api_context(payload: object) -> None:
    """An API cron's context is authored only: no managed schedule or channel routing."""
    context = _field(payload, "context")
    if context is not None and (not isinstance(context, dict) or any(map(is_managed_key, context))):
        raise ValueError("Invalid schedule config")


def _stamp_execution(payload: dict[str, object], execution: ExecutionUser) -> None:
    """Cron routing lives in the run context; the principal is the only configurable entry."""
    config = payload.setdefault("config", {})
    if not isinstance(config, dict):
        raise ValueError("Invalid schedule config")
    configurable = config.setdefault("configurable", {})
    if not isinstance(configurable, dict) or set(configurable) - {"cron_id"}:
        raise ValueError("Invalid schedule config")
    configurable["langgraph_auth_user"] = dict(execution)


def _check_context(
    payload: object, execution: ExecutionUser, source: Literal["deploy", "runtime"]
) -> None:
    if not isinstance(payload, dict) or "context" not in payload:
        return
    context = payload["context"]
    schedule = _field(context, "mda_schedule")
    name = _field(schedule, "name")
    if (
        not isinstance(context, dict)
        or not isinstance(schedule, dict)
        or not isinstance(name, str)
        or not name.strip()
        or any(is_managed_key(key) for key in context if key not in {"mda_schedule", "mda_channel"})
    ):
        raise ValueError("Invalid schedule config")
    deliver_to = schedule.get("deliver_to")
    if deliver_to is not None and read_schedule_deliver_to(deliver_to) is None:
        raise ValueError("Invalid schedule config")
    channel = context.get("mda_channel")
    if channel is None:
        return
    if source == "deploy":
        raise ValueError("Invalid schedule config")
    try:
        binding = _CHANNEL.validate_python(channel, strict=True)
    except ValidationError:
        raise ValueError("Invalid schedule config") from None
    if binding["provider"] != execution.get("mda_source_provider") or binding[
        "source_thread_key"
    ] != execution.get("mda_source_thread_id"):
        raise ValueError("Invalid schedule config")


async def _ensure_persistent_thread(access: ScheduleAccess) -> None:
    """Check the schedule can read its thread, and create the thread when it is missing.

    The schedule identity creates it, so the thread is its own. The create
    raises on an existing thread: ``if_exists: "do_nothing"`` would merge the
    stamped owner into a thread another caller owns and take it over. That
    conflict refuses the schedule.
    """
    base, headers = schedule_request_config(access)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        async with client.stream(
            "GET", f"{base}/threads/{access['thread']}", headers=headers
        ) as response:
            if response.is_success:
                return
            if response.status_code != HTTPStatus.NOT_FOUND:
                raise ValueError("Schedule access is not permitted")
        created = await client.post(
            f"{base}/threads", headers=headers, json={"thread_id": access["thread"]}
        )
        if created.status_code == HTTPStatus.CONFLICT:
            raise ValueError(
                f"Schedule access is not permitted: thread {access['thread']} "
                "belongs to another caller"
            )
        if not created.is_success:
            raise ValueError("Schedule access is not permitted")


def _schedule_limit(owner: str) -> int:
    """Read ``MDA_MAX_AGENT_SCHEDULES`` or ``MDA_MAX_USER_SCHEDULES``; default 20."""
    name = "MDA_MAX_AGENT_SCHEDULES" if owner == "agent" else "MDA_MAX_USER_SCHEDULES"
    raw = os.environ.get(name, "").strip()
    return int(raw) if re.fullmatch(r"[1-9][0-9]*", raw) else 20


async def _check_schedule_limit(access: ScheduleAccess, cron_id: str | None = None) -> None:
    """Reject once the owner has the maximum active runtime schedules, not counting ``cron_id``.

    A timing update can make an expired schedule active again.
    """
    base, headers = schedule_request_config(access)
    limit = _schedule_limit(access["owner"])
    now = datetime.now(UTC)
    active = offset = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        while True:
            response = await client.post(
                f"{base}/runs/crons/search",
                headers=headers,
                json={
                    **({"assistant_id": access["assistant"]} if access["assistant"] else {}),
                    "metadata": {"mda_managed_by": "runtime"},
                    "select": ["cron_id", "end_time"],
                    "limit": _SEARCH_PAGE,
                    "offset": offset,
                },
            )
            if not response.is_success:
                raise ValueError("Schedule access is not permitted")
            rows = _CRON_ROWS.validate_python(response.json())
            active += sum(
                1
                for row in rows
                if row.get("cron_id") != cron_id
                and not ((end := row.get("end_time")) and datetime.fromisoformat(end) < now)
            )
            if active >= limit:
                raise Auth.exceptions.HTTPException(
                    status_code=409,
                    detail=f"{SCHEDULE_LIMIT_PREFIX}: this owner can have at most {limit} "
                    "schedules that have not expired, paused ones included; "
                    "delete one before creating another",
                )
            if len(rows) < _SEARCH_PAGE:
                return
            offset += _SEARCH_PAGE
