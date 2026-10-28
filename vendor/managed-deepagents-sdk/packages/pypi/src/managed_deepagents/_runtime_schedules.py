from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, Literal, NotRequired
from uuid import UUID, uuid4

import httpx
from langgraph_sdk.schema import CronSortBy, OnCompletionBehavior, SortOrder
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    with_config,
)
from typing_extensions import TypedDict

from managed_deepagents._channels.schedule_delivery import (
    get_deployment_id,
    read_schedule_deliver_to,
)
from managed_deepagents._connections import (
    CredentialStoreError,
    resolve_agent_auth_context,
    resolve_owner_id,
)
from managed_deepagents._identity_runtime import (
    _field,
    build_runtime_identity,
    resolve_auth_user,
    resolve_runtime_configurable,
)
from managed_deepagents._schedule_auth import (
    SCHEDULE_LIMIT_PREFIX,
    ExecutionUser,
    ScheduleAccess,
    ScheduleRunAs,
    schedule_request_config,
)
from managed_deepagents._schedule_channel import schedule_thread
from managed_deepagents._schedule_time import schedule_timing
from managed_deepagents._thread_record import (
    THREAD_RECORD_METADATA_PREFIX,
    ThreadRecord,
    is_person_principal,
    origin_from_binding,
    parse_thread_record,
    target_from_origin,
    thread_record_metadata,
)
from managed_deepagents.run_context import ChannelContext, is_managed_key
from managed_deepagents.schedules import (
    ScheduleDeliveryTarget,
    ScheduleThreadConfig,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._managed_thread import RunThread, ThreadParticipant
    from managed_deepagents.identity import RuntimeIdentity

_MAX_INPUT_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 4_194_304
_MAX_PAGE_SIZE = 100
_CHANNEL_ADAPTER = TypeAdapter(ChannelContext)
_METADATA_ADAPTER = TypeAdapter(dict[str, JsonValue])
_MANAGED_BY_ADAPTER = TypeAdapter(Literal["runtime", "deploy"])
_DELIVERY_ADAPTER = TypeAdapter(ScheduleDeliveryTarget)


@with_config(ConfigDict(extra="forbid"))
class AgentScheduleOwner(TypedDict):
    type: Literal["agent"]


@with_config(ConfigDict(extra="forbid"))
class UserScheduleOwner(TypedDict):
    type: Literal["user"]
    id: NotRequired[Annotated[str, Field(min_length=1)]]


ScheduleOwner = AgentScheduleOwner | UserScheduleOwner
ScheduleInput = dict[str, JsonValue] | list[dict[str, JsonValue]]


class SchedulePatch(TypedDict, total=False):
    cron: str
    at: str | datetime
    timezone: str
    prompt: str
    input: ScheduleInput
    paused: bool
    metadata: dict[str, JsonValue]
    context: dict[str, JsonValue]


class ResolvedScheduleOwner(TypedDict):
    type: Literal["agent", "user"]
    id: str


class ScheduleRecord(TypedDict):
    id: str
    owner: ResolvedScheduleOwner
    managed_by: Literal["deploy", "runtime"]
    cron: NotRequired[str]
    at: NotRequired[str]
    timezone: str
    prompt: NotRequired[str]
    input: NotRequired[ScheduleInput]
    thread: ScheduleThreadConfig
    new_thread: bool
    #: The channel a new-thread schedule posts to; ``None`` for a same-thread
    #: schedule, which posts to its thread's origin when it fires.
    channel: str | None
    target: JsonValue
    #: Who the schedule runs as, when not its owner.
    run_as: ScheduleRunAs | None
    deliver_to: NotRequired[ScheduleDeliveryTarget]
    context: NotRequired[dict[str, JsonValue]]
    paused: bool
    metadata: dict[str, JsonValue]
    expired: bool


class SchedulePage(TypedDict):
    items: list[ScheduleRecord]
    next_offset: int | None


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cron: str | None = None
    at: str | datetime | None = None
    timezone: str | None = None
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    input: ScheduleInput | None = Field(default=None, exclude=True)
    deliver_to: ScheduleDeliveryTarget | None = None
    paused: bool | None = None
    new_thread: bool | None = None
    run_as: ScheduleRunAs | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict, exclude=True)
    context: dict[str, JsonValue] | None = Field(default=None, exclude=True)

    @field_validator("context")
    @classmethod
    def validate_context(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        if value and any(is_managed_key(key) for key in value):
            raise ValueError("Context keys mda_* are reserved")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if any(key.startswith("mda_") for key in value):
            raise ValueError("Metadata keys starting with mda_ are reserved")
        return value


class _Payload(BaseModel):
    input: ScheduleInput | None = None
    config: dict[str, object] | None = None
    context: dict[str, object] | None = None


class _Wire(BaseModel):
    cron_id: str
    assistant_id: str
    thread_id: str | None = None
    schedule: str
    timezone: str | None = None
    enabled: bool
    next_run_date: str
    end_time: str | None = None
    metadata: dict[str, object]
    payload: _Payload


@dataclass
class _Context:
    access: ScheduleAccess
    deployment: str


class _ResponseTooLargeError(ValueError):
    pass


class ScheduleNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("Schedule not found")


def _run_configurable(runtime: object | None) -> Mapping[str, object]:
    """Read the current run's configurable; schedules never work outside a run."""
    configurable = resolve_runtime_configurable(runtime)
    if not configurable or not configurable.get("assistant_id"):
        raise ValueError(
            "Schedules require an active agent run; use runtime.schedules in a tool or middleware"
        )
    return configurable


async def _context(owner: ScheduleOwner, configurable: Mapping[str, object]) -> _Context:
    selected = TypeAdapter(ScheduleOwner).validate_python(owner, strict=True)
    identity = build_runtime_identity(configurable)
    assistant = str(configurable.get("assistant_id", ""))
    UUID(assistant)
    deployment = get_deployment_id()
    if not deployment:
        raise ValueError("Schedules require a deployment ID")
    if identity is None:
        raise ValueError("Schedules require an authenticated runtime")
    owner_id = resolve_owner_id(
        {"type": "agent"}
        if selected["type"] == "agent"
        else {"type": "user", **({"id": selected["id"]} if "id" in selected else {})},
        identity,
        deployment,
    )
    execution: ExecutionUser
    if selected["type"] == "agent":
        execution = {
            "identity": f"mda:schedule:{assistant}",
            "permissions": [],
            "mda_user_id": f"mda:schedule:{assistant}",
            "mda_user_kind": "service",
            "mda_source_provider": "schedule",
            "mda_scheduled_run": True,
        }
        return _Context(
            {"owner": "agent", "assistant": assistant, "execution": execution}, deployment
        )
    if identity["user"]["kind"] != "person":
        raise ValueError("User schedules require an authenticated person")
    principal = (await asyncio.to_thread(resolve_agent_auth_context, identity)).principal_id
    execution = {
        "identity": owner_id,
        "permissions": [],
        "mda_user_id": owner_id,
        "mda_agent_auth_principal_id": principal,
        "mda_source_provider": "schedule",
        "mda_scheduled_run": True,
    }
    if identity.get("identity_provider"):
        execution["mda_identity_provider"] = identity["identity_provider"]
    if identity.get("identity_issuer"):
        execution["mda_identity_issuer"] = identity["identity_issuer"]
    if identity.get("identity_subject"):
        execution["mda_identity_subject"] = identity["identity_subject"]
    return _Context(
        {"owner": f"user:{owner_id}", "assistant": assistant, "execution": execution}, deployment
    )


async def _request(ctx: _Context, method: str, path: str, body: object = None) -> object:
    base, headers = schedule_request_config(ctx.access)
    encoded = json.dumps(body, allow_nan=False).encode() if body is not None else None
    if encoded is not None and len(encoded) > _MAX_INPUT_BYTES:
        raise ValueError("Schedule input exceeds 1 MiB")
    async with (
        httpx.AsyncClient(timeout=15, follow_redirects=False) as client,
        client.stream(
            method,
            f"{base}{path}",
            content=encoded,
            headers=headers,
        ) as response,
    ):
        if response.status_code == HTTPStatus.NOT_FOUND:
            raise ScheduleNotFoundError
        if not response.is_success:
            try:
                detail = json.loads(await response.aread()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, str) and detail.startswith(SCHEDULE_LIMIT_PREFIX):
                raise ValueError(detail)
            raise RuntimeError(f"Schedule request failed ({response.status_code})")
        if response.status_code == HTTPStatus.NO_CONTENT:
            return None
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise _ResponseTooLargeError("Schedule response exceeds 4 MiB")
        return json.loads(data)


async def _search_page(ctx: _Context, limit: int, offset: int) -> tuple[list[object], int]:
    sort_by: CronSortBy = "created_at"
    sort_order: SortOrder = "asc"
    while True:
        try:
            raw = await _request(
                ctx,
                "POST",
                "/runs/crons/search",
                {
                    "assistant_id": ctx.access["assistant"],
                    "limit": limit,
                    "offset": offset,
                    "sort_by": sort_by,
                    "sort_order": sort_order,
                },
            )
        except _ResponseTooLargeError:
            if limit == 1:
                raise
            limit = max(1, limit // 2)
            continue
        if not isinstance(raw, list):
            raise ValueError("Invalid schedule page")
        return raw, limit


def _scoped(raw: object, ctx: _Context) -> _Wire:
    try:
        wire = _Wire.model_validate(raw)
    except ValidationError:
        raise ValueError("Invalid schedule response") from None
    if (
        wire.assistant_id != ctx.access["assistant"]
        or wire.metadata.get("mda_managed") is not True
        or wire.metadata.get("mda_schedule_owner") != ctx.access["owner"]
        or wire.metadata.get("mda_schedule_assistant") != ctx.access["assistant"]
    ):
        raise ScheduleNotFoundError
    return wire


def _stored_spec(value: object) -> _Spec:
    try:
        return _Spec.model_validate(value)
    except ValidationError:
        raise ValueError("Invalid schedule response") from None


def _record(wire: _Wire, ctx: _Context, *, now: datetime | None = None) -> ScheduleRecord:
    spec = _stored_spec(wire.metadata.get("mda_schedule_spec") or {})
    source = wire.metadata.get("mda_managed_by")
    source = _MANAGED_BY_ADAPTER.validate_python(source, strict=True)
    result: ScheduleRecord = {
        "id": wire.cron_id,
        "owner": {
            "type": "agent" if ctx.access["owner"] == "agent" else "user",
            "id": ctx.deployment
            if ctx.access["owner"] == "agent"
            else ctx.access["owner"].removeprefix("user:"),
        },
        "managed_by": source,
        "metadata": _METADATA_ADAPTER.validate_python(
            {
                key: value
                for key, value in wire.metadata.items()
                if not key.startswith(("mda_", THREAD_RECORD_METADATA_PREFIX))
            },
            strict=True,
        ),
        "timezone": spec.timezone or wire.timezone or "UTC",
        "thread": {"mode": "persistent", "id": wire.thread_id}
        if wire.thread_id
        else {"mode": "ephemeral"},
        "new_thread": spec.new_thread if spec.new_thread is not None else not bool(wire.thread_id),
        **_destination(wire),
        "run_as": spec.run_as,
        "paused": not wire.enabled,
        "expired": wire.end_time is not None
        and datetime.fromisoformat(wire.end_time) < (now or datetime.now(UTC)),
    }
    if spec.at:
        if not isinstance(spec.at, str):
            raise ValueError("Invalid schedule response")
        result["at"] = spec.at
    else:
        result["cron"] = wire.schedule
    if spec.prompt:
        result["prompt"] = spec.prompt
    elif wire.payload.input is not None:
        result["input"] = wire.payload.input
    # Crons created before context support keep their routing in configurable.
    routing = wire.payload.context
    if routing is None:
        routing = (wire.payload.config or {}).get("configurable")
    delivery = spec.deliver_to or read_schedule_deliver_to(
        _field(_field(routing, "mda_schedule"), "deliver_to")
    )
    if wire.payload.context is not None:
        result["context"] = _METADATA_ADAPTER.validate_python(
            {key: value for key, value in wire.payload.context.items() if not is_managed_key(key)},
            strict=True,
        )
    if delivery:
        result["deliver_to"] = _DELIVERY_ADAPTER.validate_python(delivery, strict=True)
    return result


class _Destination(TypedDict):
    channel: str | None
    target: JsonValue


def _destination(wire: _Wire) -> _Destination:
    """Where a schedule posts: a new-thread schedule's stored origin, or the
    channel a cron made before the thread record captured.

    A same-thread schedule has none: it posts to its thread's origin when it fires.
    """
    record = parse_thread_record(wire.metadata)
    if record is not None and record.origin is not None:
        return {
            "channel": record.origin.channel.name,
            "target": target_from_origin(record.origin.channel),
        }
    routing = wire.payload.context
    # Crons created before context support keep their routing in configurable.
    if routing is None:
        routing = (wire.payload.config or {}).get("configurable")
    channel = _field(routing, "mda_channel")
    if channel is None and wire.payload.context is None:
        channel = _field(routing, "channel")
    if channel is None:
        return {"channel": None, "target": None}
    parsed = _CHANNEL_ADAPTER.validate_python(channel, strict=True)
    return {"channel": parsed["name"], "target": parsed["target"]}


def _run_input(spec: _Spec) -> ScheduleInput:
    if (spec.prompt is not None) == (spec.input is not None):
        raise ValueError("Specify exactly one of prompt or input")
    if spec.prompt is not None:
        if not spec.prompt.strip():
            raise ValueError("prompt must not be empty")
        return {"messages": [{"role": "user", "content": spec.prompt}]}
    if spec.input is None:
        raise ValueError("Missing input")
    return spec.input


async def _verify_target(
    wire: _Wire, target: str | None, ctx: _Context, *, created: bool = True
) -> None:
    if target and datetime.fromisoformat(wire.next_run_date) != datetime.fromisoformat(target):
        try:
            await _request(
                ctx,
                "DELETE" if created else "PATCH",
                f"/runs/crons/{wire.cron_id}",
                None if created else {"enabled": False},
            )
        except Exception as exc:
            raise RuntimeError("One-time target was missed; schedule cleanup failed") from exc
        raise ValueError(
            f"One-time target was missed; schedule {'removed' if created else 'paused'}"
        )


def _timing_patch(next_spec: _Spec, patch: SchedulePatch) -> dict[str, object]:
    if "cron" in patch:
        next_spec.at = None
    if "at" in patch:
        next_spec.cron = None
        next_spec.timezone = patch.get("timezone")
    timing = schedule_timing(cron=next_spec.cron, at=next_spec.at, timezone=next_spec.timezone)
    return_fields: dict[str, object] = {
        "schedule": timing["schedule"],
        "timezone": timing["timezone"],
        "end_time": timing["end_time"],
    }
    if "at" in timing:
        next_spec.at = timing["at"]
        next_spec.timezone = "UTC"
    return return_fields


def _input_patch(next_spec: _Spec, value: _Spec, patch: SchedulePatch) -> dict[str, object]:
    body: dict[str, object] = {}
    if "prompt" in patch or "input" in patch:
        body["input"] = _run_input(value)
        if "prompt" in patch:
            next_spec.input = None
        else:
            next_spec.prompt = None
    return body


def _new_thread_record(
    origin: ChannelBinding, thread: RunThread, execution: ExecutionUser
) -> dict[str, JsonValue]:
    """The record of the thread a new-thread schedule creates each time it fires.

    The Agent Server copies a cron's metadata into the thread it creates. The
    execution, when it is a person, owns that thread.
    """
    sender = thread.thread.origin.sender if thread.thread.origin is not None else None
    person = execution["mda_user_id"]
    owned = execution.get("mda_user_kind") != "service" and is_person_principal(person)
    record = ThreadRecord(
        origin=origin_from_binding(origin, sender.id if sender is not None else None),
        participants={person: "owner"} if owned else {},
    )
    return thread_record_metadata(record)


async def _run_as(
    ctx: _Context, owner: ScheduleOwner, thread: RunThread, participant: ThreadParticipant
) -> ScheduleRunAs:
    """Run the schedule as a participant of the current thread, with their Agent Auth principal."""
    if owner["type"] != "user":
        raise ValueError("run_as requires owner={'type': 'user'}")
    if participant not in thread.thread.participants:
        raise ValueError("run_as must be a participant of the current thread")
    origin = thread.origin
    if origin is None:
        raise ValueError("run_as requires a thread with a channel origin")
    ctx.access["execution"] = {
        "identity": participant.id,
        "permissions": [],
        "mda_user_id": participant.id,
        "mda_agent_auth_principal_id": await _participant_principal(participant, origin),
        "mda_source_provider": "schedule",
        "mda_scheduled_run": True,
    }
    run_as: ScheduleRunAs = {"id": participant.id, "role": participant.role}
    # The handoff accepts an execution that is neither the owner nor this participant.
    ctx.access["run_as"] = run_as
    return run_as


async def _participant_principal(participant: ThreadParticipant, origin: ChannelBinding) -> str:
    """A participant's Agent Auth principal: a Trigger sender's id already is one."""
    if origin["transport"] == "trigger_server":
        return participant.id
    identity: RuntimeIdentity = {
        "user": {"kind": "person", "id": participant.id},
        "source": {"provider": origin["provider"]},
    }
    try:
        context = await asyncio.to_thread(resolve_agent_auth_context, identity)
    except CredentialStoreError as error:
        raise ValueError(
            f"run_as participant has no Agent Auth principal ({type(error).__name__})"
        ) from None
    return context.principal_id


def _context_patch(
    existing: _Wire, value: _Spec, patch: SchedulePatch, ctx: _Context
) -> dict[str, object]:
    if "context" not in patch:
        return {}
    saved = existing.payload.context
    if saved is None:
        raise ValueError("Schedules created before context support cannot take one")
    # Crons created before the thread record replay the channel they captured.
    channel = saved.get("mda_channel")
    if channel is not None:
        binding = _CHANNEL_ADAPTER.validate_python(channel, strict=True)
        ctx.access["execution"]["mda_source_provider"] = binding["provider"]
        ctx.access["execution"]["mda_source_thread_id"] = binding["source_thread_key"]
    return {
        "context": {
            "mda_schedule": saved.get("mda_schedule"),
            **({"mda_channel": channel} if channel is not None else {}),
            **(value.context or {}),
        }
    }


class RuntimeSchedules:
    """Schedule operations bound to the current agent run.

    MDA attaches an instance as ``runtime.schedules`` on tool and middleware
    runtimes; each call reads the run's trusted identity.
    """

    __slots__ = ("_runtime",)

    def __init__(self, runtime: object | None = None) -> None:
        self._runtime = runtime

    async def create(
        self,
        *,
        owner: ScheduleOwner,
        cron: str | None = None,
        at: str | datetime | None = None,
        timezone: str | None = None,
        prompt: str | None = None,
        input: ScheduleInput | None = None,
        new_thread: bool | None = None,
        paused: bool = False,
        metadata: dict[str, JsonValue] | None = None,
        context: dict[str, JsonValue] | None = None,
        run_as: ThreadParticipant | None = None,
    ) -> ScheduleRecord:
        """Create a schedule that posts to the current thread's origin.

        ``run_as`` runs a user-owned schedule as another participant of the
        thread; its owner (who lists, changes and deletes it) stays the caller.
        """
        configurable = _run_configurable(self._runtime)
        spec = _Spec(
            cron=cron,
            at=at,
            timezone=timezone,
            prompt=prompt,
            input=input,
            new_thread=new_thread if new_thread is not None else cron is not None,
            paused=paused,
            metadata=metadata if metadata is not None else {},
            context=context,
        )
        run_input = _run_input(spec)
        timing = schedule_timing(cron=cron, at=at, timezone=timezone)
        resolved_new_thread = bool(spec.new_thread)
        thread = schedule_thread(configurable)
        channel = thread.origin
        thread_id = configurable.get("thread_id")
        if not resolved_new_thread and not thread_id:
            raise ValueError("Same-thread schedules require a current thread; use new_thread=True")
        ctx = await _context(owner, configurable)
        if run_as is not None:
            spec.run_as = await _run_as(ctx, owner, thread, run_as)
        if channel is not None:
            ctx.access["execution"]["mda_source_provider"] = channel["provider"]
            ctx.access["execution"]["mda_source_thread_id"] = channel["source_thread_key"]
        elif not resolved_new_thread and owner["type"] == "agent":
            raise ValueError("Agent-owned schedules outside a channel require new_thread=True")
        elif not resolved_new_thread:
            user = resolve_auth_user(configurable) or {}
            provider = user.get("mda_source_provider")
            source_thread_id = user.get("mda_source_thread_id")
            # Native thread authorization must use the original conversation owner.
            if (
                isinstance(provider, str)
                and provider.strip()
                and isinstance(source_thread_id, str)
                and source_thread_id.strip()
            ):
                ctx.access["execution"]["mda_source_provider"] = provider
                ctx.access["execution"]["mda_source_thread_id"] = source_thread_id
        name = str(uuid4())
        if "at" in timing:
            spec.at = timing["at"]
            spec.timezone = "UTC"
        path = (
            "/runs/crons" if resolved_new_thread else f"/threads/{UUID(str(thread_id))}/runs/crons"
        )
        on_run_completed: OnCompletionBehavior = (
            "keep" if not resolved_new_thread or channel else "delete"
        )
        wire = _scoped(
            await _request(
                ctx,
                "POST",
                path,
                {
                    "assistant_id": ctx.access["assistant"],
                    "schedule": timing["schedule"],
                    "timezone": timing["timezone"],
                    **({"end_time": timing["end_time"]} if timing["end_time"] else {}),
                    "input": run_input,
                    "enabled": not paused,
                    "on_run_completed": on_run_completed,
                    "context": {"mda_schedule": {"name": name}, **(spec.context or {})},
                    "metadata": {
                        **spec.metadata,
                        # A same-thread schedule posts to its thread's origin when it fires.
                        **(
                            _new_thread_record(channel, thread, ctx.access["execution"])
                            if channel is not None and resolved_new_thread
                            else {}
                        ),
                        "mda_managed": True,
                        "mda_managed_by": "runtime",
                        "mda_schedule": name,
                        "mda_schedule_spec": spec.model_dump(exclude_none=True),
                    },
                },
            ),
            ctx,
        )
        await _verify_target(wire, timing.get("at"), ctx)
        return _record(wire, ctx)

    async def list(
        self,
        *,
        owner: ScheduleOwner,
        limit: int = 20,
        offset: int = 0,
        include_expired: bool = False,
    ) -> SchedulePage:
        if (
            type(limit) is not int
            or not 1 <= limit <= _MAX_PAGE_SIZE
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("Invalid schedule pagination")
        if type(include_expired) is not bool:
            raise ValueError("include_expired must be a boolean")
        ctx = await _context(owner, _run_configurable(self._runtime))
        now = datetime.now(UTC)
        # Native cron search cannot filter expiration. Offsets count visible rows;
        # scan until one extra visible row proves there is another page.
        batch_size = min(limit + 1, _MAX_PAGE_SIZE)
        server_offset = offset if include_expired else 0
        skipped = server_offset
        items: list[ScheduleRecord] = []
        while True:
            raw, batch_size = await _search_page(ctx, batch_size, server_offset)
            for item in raw:
                record = _record(_scoped(item, ctx), ctx, now=now)
                if record["expired"] and not include_expired:
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                if len(items) == limit:
                    return {"items": items, "next_offset": offset + limit}
                items.append(record)
            if len(raw) < batch_size:
                return {"items": items, "next_offset": None}
            server_offset += len(raw)

    async def get(self, id: str, *, owner: ScheduleOwner) -> ScheduleRecord:
        UUID(id)
        ctx = await _context(owner, _run_configurable(self._runtime))
        return _record(_scoped(await _request(ctx, "GET", f"/runs/crons/{id}"), ctx), ctx)

    async def update(
        self, id: str, patch: SchedulePatch, *, owner: ScheduleOwner
    ) -> ScheduleRecord:
        UUID(id)
        if any(value is None for value in patch.values()):
            raise ValueError("Schedule fields cannot be cleared")
        if set(patch) - {
            "cron",
            "at",
            "timezone",
            "prompt",
            "input",
            "paused",
            "metadata",
            "context",
        }:
            raise ValueError(
                "Unsupported schedule update field; channel and thread choice are fixed at creation"
            )
        value = _Spec.model_validate(patch)
        if not patch:
            raise ValueError("Schedule patch cannot be empty")
        ctx = await _context(owner, _run_configurable(self._runtime))
        existing = _scoped(await _request(ctx, "GET", f"/runs/crons/{id}"), ctx)
        if existing.metadata.get("mda_managed_by") != "runtime":
            raise ValueError("Change code-defined schedules in code and deploy")
        old = _stored_spec(existing.metadata.get("mda_schedule_spec"))
        if old.at and "timezone" in patch and "at" not in patch and "cron" not in patch:
            raise ValueError("Changing a one-time timezone requires a new at timestamp")
        next_spec = old.model_copy(update={key: getattr(value, key) for key in patch})
        body: dict[str, object] = {}
        body.update(_input_patch(next_spec, value, patch))
        if {"cron", "at", "timezone"} & patch.keys():
            body.update(_timing_patch(next_spec, patch))
        if "paused" in patch:
            body["enabled"] = not value.paused
        body.update(_context_patch(existing, value, patch, ctx))
        body["metadata"] = {
            **value.metadata,
            "mda_schedule_spec": next_spec.model_dump(exclude_none=True),
        }
        wire = _scoped(await _request(ctx, "PATCH", f"/runs/crons/{id}", body), ctx)
        if "at" in patch:
            if not isinstance(next_spec.at, str):
                raise ValueError("Invalid at timestamp")
            await _verify_target(wire, next_spec.at, ctx, created=False)
        return _record(wire, ctx)

    async def delete(self, id: str, *, owner: ScheduleOwner) -> None:
        UUID(id)
        ctx = await _context(owner, _run_configurable(self._runtime))
        existing = _scoped(await _request(ctx, "GET", f"/runs/crons/{id}"), ctx)
        if existing.metadata.get("mda_managed_by") != "runtime":
            raise ValueError("Change code-defined schedules in code and deploy")
        await _request(ctx, "DELETE", f"/runs/crons/{id}")


schedules = RuntimeSchedules()
"""Schedule operations for the current run, read from the ambient LangGraph config.

Equivalent to ``runtime.schedules`` inside a tool or middleware.
"""
