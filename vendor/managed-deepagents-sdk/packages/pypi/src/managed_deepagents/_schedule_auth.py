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
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from langgraph_sdk import Auth
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.schedule_delivery import read_schedule_deliver_to
from managed_deepagents._channels.source_providers import is_trusted_source_provider
from managed_deepagents._identity_runtime import _auth_user_fields, _field, is_studio_auth_user
from managed_deepagents._loopback import is_loopback_url, resolve_loopback_api_url
from managed_deepagents._thread_record import (
    THREAD_CONVERSATION_METADATA,
    THREAD_PARTICIPANTS_METADATA,
    THREAD_RECORD_METADATA,
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

    ``mda_schedule_access`` is present for cron-route handoffs and absent for the
    persistent-thread read. ``ExecutionUser`` keeps ``permissions`` required as a
    ``list``, which ``langgraph_sdk.auth.types.MinimalUserDict`` cannot express.
    """

    mda_schedule_access: ScheduleAccess


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


class _CronRow(TypedDict, total=False):
    cron_id: str
    end_time: str | None


_EXECUTION = TypeAdapter(ExecutionUser)
_ACCESS = TypeAdapter(ScheduleAccess)
_CHANNEL = TypeAdapter(ChannelContext)
_CRON_ROWS = TypeAdapter(list[_CronRow])
_HANDOFF = TypeAdapter(ThreadHandoff)
_HANDOFF_GRANT = TypeAdapter(ThreadHandoffGrant)
_EXECUTION_KEYS = ExecutionUser.__required_keys__ | ExecutionUser.__optional_keys__
_HANDOFF_TIMEOUT_SECONDS = 15


def schedule_request_config(access: ScheduleAccess) -> tuple[str, dict[str, str]]:
    """Use the deployed API URL and server key, or the local dev API."""
    return _handoff_request_config(SCHEDULE_HANDOFF_HEADER, access)


def _handoff_request_config(header: str, value: object) -> tuple[str, dict[str, str]]:
    hosted = os.environ.get("LANGSMITH_HOST_API_URL", "").strip().rstrip("/")
    base = hosted or resolve_loopback_api_url()
    secret = os.environ.get("MDA_HANDOFF_SECRET")
    api_key = os.environ.get("LANGSMITH_API_KEY", "").strip()
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
    """Move a scheduled run's thread to the conversation its post started.

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

    It may only update the scheduled run's own thread, once, or read it to check.
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
        or execution.get("mda_scheduled_run") is not True
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


def thread_handoff_grant(user: object) -> ThreadHandoffGrant | None:
    """The thread and scheduled run a handoff caller may move, if it is one."""
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
    UUID(access["assistant"])
    if "thread" in access:
        UUID(access["thread"])
        if method != "GET" or path != f"/threads/{access['thread']}":
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
        or not execution.get("mda_agent_auth_principal_id")
    ):
        raise ValueError("Schedule access is not permitted")
    user: ScheduleHandoffUser = {**execution}
    if "thread" not in access:
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
    metadata = data.get("metadata")
    payload = data.get("payload")
    if access is None:
        if not (is_studio_auth_user(user) or _is_local_dev_operator(user)):
            raise ValueError("Schedule access is not permitted")
        await _authorize_deploy_schedule(action, data)
        return None
    filters = {"mda_schedule_owner": access["owner"], "mda_schedule_assistant": access["assistant"]}
    if action in {"update", "delete"}:
        filters["mda_managed_by"] = "runtime"
    if action == "create":
        if (
            not isinstance(payload, dict)
            or payload.get("assistant_id") != access["assistant"]
            or not isinstance(metadata, dict)
            or metadata.get("mda_managed_by") != "runtime"
        ):
            raise ValueError("Schedule access is not permitted")
        metadata.update(filters)
        metadata["mda_managed"] = True
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


async def _authorize_deploy_schedule(action: str, data: dict[str, object]) -> None:
    # Studio identity is reachable by any workspace API key: allow only a deploy-managed
    # create (stamped below), never an update, and leave read/search/delete unscoped so
    # `mda deploy` (and `mda dev` locally) can still list and tear down managed crons.
    if action == "update":
        raise ValueError("Schedule access is not permitted")
    if action != "create":
        return
    metadata = data.get("metadata")
    payload = data.get("payload")
    if (
        not isinstance(metadata, dict)
        or metadata.get("mda_managed") is not True
        or metadata.get("mda_managed_by") != "deploy"
        or not isinstance(payload, dict)
        or not isinstance(payload.get("assistant_id"), str)
    ):
        raise ValueError("Schedule access is not permitted")
    assistant = payload["assistant_id"]
    try:
        UUID(assistant)
    except ValueError:
        assistant = str(uuid5(NAMESPACE_URL, assistant))
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
        await _check_persistent_thread(
            {
                "owner": "agent",
                "assistant": assistant,
                "execution": execution,
                "thread": str(data["thread_id"]),
            }
        )
    _check_context(payload, execution, "deploy")
    _stamp_execution(payload, execution)


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


async def _check_persistent_thread(access: ScheduleAccess) -> None:
    base, headers = schedule_request_config(access)
    async with (
        httpx.AsyncClient(timeout=15, follow_redirects=False) as client,
        client.stream(
            "GET",
            f"{base}/threads/{access['thread']}",
            headers=headers,
        ) as response,
    ):
        if not response.is_success:
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
                    "assistant_id": access["assistant"],
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
