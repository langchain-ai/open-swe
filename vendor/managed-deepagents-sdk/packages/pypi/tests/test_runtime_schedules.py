from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import pytest
from langchain.tools import ToolRuntime
from langgraph_sdk import Auth
from pydantic import JsonValue

from managed_deepagents import _runtime_schedules as runtime
from managed_deepagents import _schedule_auth
from managed_deepagents import _schedule_channel as channel
from managed_deepagents._channels import binding
from managed_deepagents._identity_auth import _thread_owner_key
from managed_deepagents._managed_thread import ThreadParticipant, build_run_thread
from managed_deepagents._managed_tools import adapt_managed_runtime, managed_runtime_overrides
from managed_deepagents._schedule_auth import authorize_schedule, schedule_handoff_user
from managed_deepagents._thread_record import (
    next_thread_record,
    parse_thread_record,
    thread_record_metadata,
)

ASSISTANT = "00000000-0000-4000-8000-000000000001"
# Unbound: reads the run config the fixture installs.
schedules = runtime.RuntimeSchedules()
CHANNEL: dict[str, Any] = {
    "name": "team",
    "provider": "slack",
    "transport": "trigger_server",
    "source_thread_key": "slack:original",
    "delivery_id": "original",
    "target": {
        "channel_id": "channel-install",
        "source_thread_key": "slack:original",
        "address": {"conversation_id": "C123", "thread_id": "123.456"},
    },
    "event": {"type": "message", "messages": []},
    "raw_event": {"channel_type": "channel"},
}


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:  # noqa: C901 - HTTP fixture
    state = SimpleNamespace(
        caller="alice",
        stored={},
        patches=[],
        http_request=runtime._request,
        source={},
        stamp={},
        thread_owner="alice",
        accesses=[],
    )
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment")
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "local-test-secret")
    monkeypatch.setattr(
        runtime,
        "resolve_runtime_configurable",
        lambda _: {
            "assistant_id": ASSISTANT,
            "thread_id": ASSISTANT,
            **state.stamp,
            "langgraph_auth_user": {
                "identity": state.caller,
                "mda_user_id": state.caller,
                "permissions": [],
                **state.source,
            },
        },
    )
    monkeypatch.setattr(
        runtime,
        "resolve_agent_auth_context",
        lambda _: SimpleNamespace(principal_id="canonical-person"),
    )

    async def request(ctx: runtime._Context, method: str, path: str, body: Any = None) -> object:  # noqa: C901 - HTTP fixture
        if (
            path.startswith("/threads/")
            and _thread_owner_key(ctx.access["execution"]) != state.thread_owner
        ):
            raise runtime.ScheduleNotFoundError
        body = copy.deepcopy(body or {})
        id = path.rsplit("/", maxsplit=1)[-1]
        action = {"GET": "read", "DELETE": "delete", "PATCH": "update"}.get(
            method, "search" if id == "search" else "create"
        )
        value = (
            {"metadata": body.get("metadata", {}), "payload": body}
            if action in {"create", "update"}
            else {}
        )
        if action == "update":
            value.update(cron_id=id, schedule=body.get("schedule"))
        filters = await authorize_schedule(
            {**ctx.access["execution"], "mda_schedule_access": ctx.access}, action, value
        )
        if "config" in body:
            assert list(body["config"]["configurable"]) == ["langgraph_auth_user"]

        def matches(wire: dict[str, Any]) -> bool:
            return all(
                wire["metadata"].get(key) == expected for key, expected in (filters or {}).items()
            )

        if action == "create":
            id = str(uuid4())
            wire = {
                "cron_id": id,
                "thread_id": path.split("/")[2] if path.startswith("/threads/") else None,
                "assistant_id": body["assistant_id"],
                "schedule": body["schedule"],
                "timezone": body["timezone"],
                "enabled": body["enabled"],
                "end_time": body.get("end_time"),
                "next_run_date": body["metadata"]["mda_schedule_spec"].get(
                    "at", "2026-12-01T00:00:00Z"
                ),
                "metadata": body["metadata"],
                "payload": {
                    "input": body["input"],
                    "config": body["config"],
                    "context": body.get("context"),
                },
            }
            state.stored[id] = wire
            return copy.deepcopy(wire)
        if action == "search":
            return copy.deepcopy(
                [
                    wire
                    for wire in state.stored.values()
                    if matches(wire)
                    and all(
                        wire["metadata"].get(key) == expected
                        for key, expected in body.get("metadata", {}).items()
                    )
                ][body["offset"] : body["offset"] + body["limit"]]
            )
        wire = state.stored.get(id)
        if wire is None or not matches(wire):
            raise runtime.ScheduleNotFoundError
        if action == "delete":
            del state.stored[id]
            return None
        if action == "update":
            state.patches.append(copy.deepcopy(body))
            for key in ("schedule", "timezone", "end_time", "enabled"):
                if key in body:
                    wire[key] = body[key]
            for key in ("config", "input", "context"):
                if key in body:
                    wire["payload"][key] = body[key]
            wire["metadata"].update(body.get("metadata", {}))
        return copy.deepcopy(wire)

    async def handle(req: httpx.Request) -> httpx.Response:
        access = json.loads(req.headers[_schedule_auth.SCHEDULE_HANDOFF_HEADER])
        state.accesses.append(access)
        ctx = runtime._Context(access, "deployment")
        body = json.loads(req.content) if req.content else None
        try:
            result = await request(ctx, req.method, req.url.path, body)
        except Auth.exceptions.HTTPException as error:
            return httpx.Response(error.status_code, json={"detail": error.detail})
        return httpx.Response(200, json=result)

    monkeypatch.setattr(runtime, "_request", request)
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handle))
    )
    return state


def test_owner_isolation_and_pagination(native: SimpleNamespace) -> None:
    async def check() -> None:
        first = await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="first")
        await schedules.create(owner={"type": "user"}, cron="0 9 * * *", input={"task": "second"})
        page = await schedules.list(owner={"type": "user"}, limit=1)
        assert len(page["items"]) == 1
        assert page["next_offset"] == 1
        native.caller = "bob"
        assert (await schedules.list(owner={"type": "user"}))["items"] == []
        for operation in (
            schedules.get(first["id"], owner={"type": "user"}),
            schedules.update(first["id"], {"paused": True}, owner={"type": "user"}),
            schedules.delete(first["id"], owner={"type": "user"}),
        ):
            with pytest.raises(runtime.ScheduleNotFoundError):
                await operation
        with pytest.raises(Exception, match=r"match"):
            await schedules.create(
                owner={"type": "user", "id": "alice"}, cron="0 9 * * *", prompt="bad"
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    ("stamp", "source"),
    [
        pytest.param({"mda_schedule": {"name": "parent"}}, {}, id="runtime-cron-stamp"),
        pytest.param(
            {"mda_schedule": {"name": "daily", "deliver_to": None}}, {}, id="deploy-cron-stamp"
        ),
        pytest.param({}, {"mda_source_provider": "schedule"}, id="schedule-identity"),
        pytest.param(
            {},
            {"mda_source_provider": "slack", "mda_scheduled_run": True},
            id="channel-schedule-identity",
        ),
    ],
)
def test_scheduled_run_creates_schedules(
    native: SimpleNamespace, stamp: dict[str, object], source: dict[str, object]
) -> None:
    async def check() -> None:
        await schedules.create(owner={"type": "agent"}, cron="0 9 * * *", prompt="parent")
        native.stamp = stamp
        native.source = source
        owners: list[runtime.ScheduleOwner] = [{"type": "agent"}, {"type": "user"}]
        for owner in owners:
            await schedules.create(owner=owner, cron="* * * * *", prompt="child")
        assert len(native.stored) == 3

    asyncio.run(check())


def test_owner_schedule_limit(native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_request", native.http_request)

    async def check() -> None:
        agent: runtime.ScheduleOwner = {"type": "agent"}
        first = await schedules.create(owner=agent, cron="0 9 * * *", prompt="test")
        for _ in range(20):
            deploy = copy.deepcopy(native.stored[first["id"]])
            deploy["metadata"]["mda_managed_by"] = "deploy"
            native.stored[str(uuid4())] = deploy
        for _ in range(19):
            await schedules.create(owner=agent, cron="0 9 * * *", prompt="test")
        with pytest.raises(ValueError, match="at most 20 schedules"):
            await schedules.create(owner=agent, cron="0 9 * * *", prompt="test")
        await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test")
        native.stored[first["id"]]["end_time"] = "2000-01-01T00:00:00Z"
        last = await schedules.create(owner=agent, cron="0 9 * * *", prompt="test")
        with pytest.raises(ValueError, match="at most 20 schedules"):
            await schedules.update(first["id"], {"cron": "0 10 * * *"}, owner=agent)
        await schedules.update(last["id"], {"cron": "0 10 * * *"}, owner=agent)

    asyncio.run(check())


def test_owner_schedule_limit_from_env(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "_request", native.http_request)
    monkeypatch.setenv("MDA_MAX_AGENT_SCHEDULES", "3")
    monkeypatch.setenv("MDA_MAX_USER_SCHEDULES", "2")

    async def check() -> None:
        owners: list[tuple[runtime.ScheduleOwner, int]] = [
            ({"type": "agent"}, 3),
            ({"type": "user"}, 2),
        ]
        for owner, limit in owners:
            for _ in range(limit):
                await schedules.create(owner=owner, cron="0 9 * * *", prompt="test")
            with pytest.raises(ValueError, match=f"at most {limit} schedules"):
                await schedules.create(owner=owner, cron="0 9 * * *", prompt="test")
        monkeypatch.setenv("MDA_MAX_USER_SCHEDULES", "0")
        await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test")

    asyncio.run(check())


def test_agent_identity_survives_input_edit_and_pause(native: SimpleNamespace) -> None:
    async def check() -> None:
        created = await schedules.create(owner={"type": "agent"}, cron="0 9 * * *", prompt="agent")
        saved = copy.deepcopy(
            native.stored[created["id"]]["payload"]["config"]["configurable"]["langgraph_auth_user"]
        )
        assert saved["mda_user_kind"] == "service"
        assert "mda_agent_auth_principal_id" not in saved
        native.caller = "bob"
        await schedules.update(
            created["id"],
            {"prompt": "updated"},
            owner={"type": "agent"},
        )
        assert (
            native.stored[created["id"]]["payload"]["config"]["configurable"]["langgraph_auth_user"]
            == saved
        )
        await schedules.update(created["id"], {"paused": True}, owner={"type": "agent"})
        assert not {"config", "schedule", "timezone"} & native.patches[-1].keys()
        native.stored[created["id"]]["metadata"]["mda_managed_by"] = "deploy"
        assert (await schedules.get(created["id"], owner={"type": "agent"}))[
            "managed_by"
        ] == "deploy"
        with pytest.raises(ValueError, match=r"code-defined"):
            await schedules.delete(created["id"], owner={"type": "agent"})

    asyncio.run(check())


def test_one_time_to_recurring_clears_expiration(native: SimpleNamespace) -> None:
    async def check() -> None:
        at = (datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=3)).isoformat()
        created = await schedules.create(owner={"type": "user"}, at=at, prompt="once")
        with pytest.raises(ValueError, match=r"new at"):
            await schedules.update(
                created["id"], {"timezone": "America/New_York"}, owner={"type": "user"}
            )
        updated = await schedules.update(
            created["id"], {"cron": "0 9 * * *"}, owner={"type": "user"}
        )
        assert updated["id"] == created["id"]
        assert "at" not in updated
        assert native.patches[-1]["end_time"] is None

    asyncio.run(check())


def test_raw_crons_and_forged_input_are_denied(native: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match=r"Schedule access|exactly one"):
        asyncio.run(
            authorize_schedule(
                {"identity": "alice", "mda_user_id": "alice", "permissions": []}, "search", {}
            )
        )
    with pytest.raises(ValueError, match=r"Schedule access|exactly one"):
        schedule_handoff_user({"x-mda-schedule-handoff": "{}"}, "/runs/crons", "POST")
    with pytest.raises(ValueError, match=r"Schedule access|exactly one"):
        asyncio.run(
            schedules.create(owner={"type": "agent"}, cron="0 9 * * *", prompt="bad", input={})
        )


def test_explicit_delivery_is_rejected(native: SimpleNamespace) -> None:
    with pytest.raises(TypeError, match="deliver_to"):
        asyncio.run(
            schedules.create(
                owner={"type": "user"},
                cron="0 9 * * *",
                prompt="test",
                deliver_to={
                    "channel": "slack",
                    "to": {"type": "provider_conversation", "conversation_id": "C1"},
                },
            )
        )
    assert not native.stored


def test_input_edit_preserves_saved_identity(
    native: SimpleNamespace,
) -> None:
    async def check() -> None:
        created = await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test")
        configurable = native.stored[created["id"]]["payload"]["config"]["configurable"]
        saved = copy.deepcopy(configurable["langgraph_auth_user"])
        saved["mda_source_provider"] = "slack"
        saved["mda_source_thread_id"] = "original-channel-thread"
        configurable["langgraph_auth_user"] = saved
        await schedules.update(created["id"], {"prompt": "updated"}, owner={"type": "user"})
        configurable = native.stored[created["id"]]["payload"]["config"]["configurable"]
        assert configurable["langgraph_auth_user"] == saved

    asyncio.run(check())


@pytest.mark.parametrize(
    "user",
    [
        None,
        {
            "identity": "service",
            "mda_user_id": "service",
            "mda_user_kind": "service",
            "permissions": [],
        },
    ],
)
def test_user_owner_requires_a_person(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, user: Any
) -> None:
    monkeypatch.setattr(
        runtime,
        "resolve_runtime_configurable",
        lambda _: {"assistant_id": ASSISTANT, "langgraph_auth_user": user},
    )
    with pytest.raises(ValueError, match="authenticated"):
        asyncio.run(schedules.list(owner={"type": "user"}))


def test_static_schedule_service_identity_and_persistent_access(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        value = {
            "metadata": {"mda_managed": True, "mda_managed_by": "deploy"},
            "payload": {
                "assistant_id": ASSISTANT,
                "context": {
                    "mda_schedule": {
                        "name": "daily",
                        "deliver_to": {
                            "channel": "slack",
                            "to": {"type": "provider_conversation", "conversation_id": "C1"},
                        },
                    },
                    "team": "eng",
                    "channel": "authored",
                },
            },
        }
        context = copy.deepcopy(value["payload"]["context"])
        await authorize_schedule(
            {"identity": "langgraph-studio-user", "permissions": []}, "create", value
        )
        assert value["metadata"]["mda_schedule_owner"] == "agent"
        assert value["payload"]["context"] == context
        assert list(value["payload"]["config"]["configurable"]) == ["langgraph_auth_user"]
        deploy_execution = value["payload"]["config"]["configurable"]["langgraph_auth_user"]
        assert deploy_execution["mda_user_kind"] == "service"
        assert deploy_execution["mda_scheduled_run"] is True
        for payload in (
            {"context": {"mda_schedule": {"name": "daily", "deliver_to": {"channel": "slack"}}}},
            {"context": {"mda_schedule": {"name": "daily"}, "mda_channel": CHANNEL}},
            {"context": {"mda_schedule": {"name": "daily"}, "mda_other": 1}},
            {"context": {"team": "eng"}},
            {"config": {"configurable": {"mda_schedule": {"name": "daily"}}}},
        ):
            with pytest.raises(ValueError, match="Invalid schedule config"):
                await authorize_schedule(
                    {"identity": "langgraph-studio-user", "permissions": []},
                    "create",
                    {**value, "payload": {"assistant_id": ASSISTANT, **payload}},
                )

        async def denied(access: object) -> None:
            raise ValueError("Schedule access is not permitted")

        monkeypatch.setattr(_schedule_auth, "_check_persistent_thread", denied)
        value["thread_id"] = ASSISTANT
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(
                {"identity": "langgraph-studio-user", "permissions": []}, "create", value
            )

        forged = {
            "payload": {
                "assistant_id": ASSISTANT,
                "config": {"configurable": {"langgraph_auth_user": {"identity": "forged"}}},
            },
        }
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(
                {"identity": "langgraph-studio-user", "permissions": []}, "create", forged
            )
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(
                {"identity": "langgraph-studio-user", "permissions": []}, "update", value
            )
        assert (
            await authorize_schedule(
                {"identity": "langgraph-studio-user", "permissions": []},
                "delete",
                {"cron_id": "any"},
            )
            is None
        )
        assert (
            await authorize_schedule(
                {"identity": "langgraph-studio-user", "permissions": []}, "search", {}
            )
            is None
        )

    asyncio.run(check())


def test_only_the_local_dev_operator_under_mda_dev_provisions_static_schedules(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deploy_schedule() -> dict[str, Any]:
        return {
            "metadata": {"mda_managed": True, "mda_managed_by": "deploy"},
            "payload": {
                "assistant_id": ASSISTANT,
                "context": {"mda_schedule": {"name": "daily"}},
            },
        }

    operator = {"identity": "mda:local-dev", "mda_user_id": "mda:local-dev", "permissions": []}
    someone = {"identity": "bob", "mda_user_id": "bob", "permissions": []}
    # What a request without a valid secret falls back to under `mda dev`.
    claimed = {**operator, "mda_user_kind": "service"}

    async def check() -> None:
        # Outside `mda dev` the local-dev principal is nobody special.
        monkeypatch.delenv("MDA_LOCAL_DEV", raising=False)
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(operator, "create", deploy_schedule())
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(operator, "search", {})

        monkeypatch.setenv("MDA_LOCAL_DEV", "1")
        value = deploy_schedule()
        await authorize_schedule(operator, "create", value)
        assert value["metadata"]["mda_schedule_owner"] == "agent"
        execution = value["payload"]["config"]["configurable"]["langgraph_auth_user"]
        assert execution["mda_user_kind"] == "service"
        assert execution["mda_scheduled_run"] is True
        assert await authorize_schedule(operator, "search", {}) is None
        assert await authorize_schedule(operator, "delete", {"cron_id": "any"}) is None
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(operator, "update", deploy_schedule())
        runtime_owned = deploy_schedule()
        runtime_owned["metadata"]["mda_managed_by"] = "runtime"
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(operator, "create", runtime_owned)
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(someone, "create", deploy_schedule())
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(claimed, "create", deploy_schedule())
        with pytest.raises(ValueError, match="Schedule access"):
            await authorize_schedule(claimed, "search", {})

    asyncio.run(check())


def test_empty_owner_id_is_not_omitted(native: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="validation"):
        asyncio.run(schedules.list(owner={"type": "user", "id": ""}))


def test_expired_schedules_are_filtered_before_pagination(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        first = await schedules.create(owner=owner, cron="0 9 * * *", prompt="first")
        # More expired rows than a server page, between two visible schedules.
        for index in range(101):
            item = await schedules.create(owner=owner, cron="0 9 * * *", prompt=str(index))
            wire = native.stored[item["id"]]
            wire["end_time"] = "2000-01-01T00:05:00Z"
            wire["metadata"]["mda_schedule_spec"] = {
                "at": "2000-01-01T00:00:00Z",
                "prompt": str(index),
            }
        last = await schedules.create(owner=owner, cron="0 10 * * *", prompt="last", paused=True)
        page = await schedules.list(owner=owner, limit=1)
        assert [item["id"] for item in page["items"]] == [first["id"]]
        assert page["next_offset"] == 1
        page = await schedules.list(owner=owner, limit=1, offset=1)
        assert [item["id"] for item in page["items"]] == [last["id"]]
        assert page["items"][0]["paused"] is True
        assert page["items"][0]["expired"] is False
        assert page["next_offset"] is None
        assert (await schedules.list(owner=owner, offset=2))["items"] == []
        history = await schedules.list(owner=owner, limit=2, offset=1, include_expired=True)
        assert len(history["items"]) == 2
        assert all(item["expired"] for item in history["items"])
        assert history["next_offset"] == 3
        assert (await schedules.get(history["items"][0]["id"], owner=owner))["expired"] is True

    asyncio.run(check())


def test_unexpired_one_time_and_all_expired_page(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        target = (datetime.now(UTC) + timedelta(days=1)).replace(second=0, microsecond=0)
        item = await schedules.create(owner=owner, at=target.isoformat(), prompt="future")
        assert item["expired"] is False
        assert len((await schedules.list(owner=owner))["items"]) == 1
        native.stored[item["id"]]["end_time"] = "2000-01-01T00:00:00Z"
        assert await schedules.list(owner=owner) == {"items": [], "next_offset": None}
        assert (await schedules.list(owner=owner, include_expired=True))["items"][0][
            "expired"
        ] is True
        with pytest.raises(ValueError, match="include_expired"):
            await schedules.list(owner=owner, include_expired=cast("bool", "true"))

    asyncio.run(check())


def test_metadata_uses_native_cron_fields_and_merges(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        metadata: dict[str, JsonValue] = {
            "label": "briefing",
            "priority": 1,
            "tags": ["daily"],
            "details": {"team": "eng"},
        }
        created = await schedules.create(
            owner=owner, cron="0 9 * * *", prompt="briefing", metadata=metadata
        )
        assert created["metadata"] == metadata
        wire = native.stored[created["id"]]
        assert all(wire["metadata"][key] == value for key, value in metadata.items())
        assert "metadata" not in wire["metadata"]["mda_schedule_spec"]
        protected = {
            key: value
            for key, value in wire["metadata"].items()
            if key.startswith("mda_") and key != "mda_schedule_spec"
        }
        updated = await schedules.update(
            created["id"], {"metadata": {"priority": 2, "enabled": True}}, owner=owner
        )
        expected = {**metadata, "priority": 2, "enabled": True}
        assert updated["metadata"] == expected
        assert native.patches[-1]["metadata"]["priority"] == 2
        assert "label" not in native.patches[-1]["metadata"]
        for patch in ({"metadata": {}}, {"paused": True}):
            result = await schedules.update(
                created["id"], cast("runtime.SchedulePatch", patch), owner=owner
            )
            assert result["metadata"] == expected
        assert (await schedules.get(created["id"], owner=owner))["metadata"] == expected
        assert (await schedules.list(owner=owner))["items"][0]["metadata"] == expected
        assert all(wire["metadata"][key] == value for key, value in protected.items())
        native.caller = "bob"
        with pytest.raises(runtime.ScheduleNotFoundError):
            await schedules.update(created["id"], {"metadata": {}}, owner=owner)

    asyncio.run(check())


@pytest.mark.parametrize(
    "key",
    [
        "mda_managed",
        "mda_managed_by",
        "mda_schedule_owner",
        "mda_schedule_assistant",
        "mda_schedule",
        "mda_schedule_spec",
        "mda_future",
    ],
)
def test_metadata_cannot_change_reserved_fields(native: SimpleNamespace, key: str) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        with pytest.raises(ValueError, match="reserved"):
            await schedules.create(
                owner=owner, cron="0 9 * * *", prompt="test", metadata={key: "forged"}
            )
        assert not native.stored
        created = await schedules.create(owner=owner, cron="0 9 * * *", prompt="test")
        assert created["metadata"] == {}
        with pytest.raises(ValueError, match="reserved"):
            await schedules.update(created["id"], {"metadata": {key: "forged"}}, owner=owner)
        assert not native.patches
        if key != "mda_schedule_spec":
            ctx = await runtime._context(owner, runtime._run_configurable(None))
            with pytest.raises(ValueError, match="Schedule access"):
                await authorize_schedule(
                    {"mda_schedule_access": ctx.access}, "update", {"metadata": {key: "forged"}}
                )

    asyncio.run(check())


@pytest.mark.parametrize("metadata", [[], "invalid", {"value": object()}])
def test_metadata_must_be_a_json_dict(native: SimpleNamespace, metadata: Any) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        with pytest.raises(ValueError, match="validation"):
            await schedules.create(owner=owner, cron="0 9 * * *", prompt="test", metadata=metadata)
        created = await schedules.create(owner=owner, cron="0 9 * * *", prompt="test")
        with pytest.raises(ValueError, match="validation"):
            await schedules.update(created["id"], {"metadata": metadata}, owner=owner)
        with pytest.raises(ValueError, match="cannot be cleared"):
            await schedules.update(
                created["id"], cast("runtime.SchedulePatch", {"metadata": None}), owner=owner
            )
        assert not native.patches

    asyncio.run(check())


@pytest.mark.parametrize("owner", ["agent", "user"])
@pytest.mark.parametrize(
    ("recurring", "override", "expected"),
    [(True, None, True), (False, None, False), (True, False, False), (False, True, True)],
)
def test_channel_capture_and_thread_defaults(
    native: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    owner: Literal["agent", "user"],
    recurring: bool,
    override: bool | None,
    expected: bool,
) -> None:
    context = {"mda_channel": copy.deepcopy(CHANNEL)}
    monkeypatch.setattr(channel, "ambient_context", lambda: context)
    monkeypatch.setattr(binding, "channel_provider_for_name", lambda _: "slack")
    original = runtime.resolve_runtime_configurable(None)
    original["langgraph_auth_user"].update(
        mda_source_provider="slack", mda_source_thread_id="slack:original"
    )
    native.thread_owner = "mda:source-thread:slack:slack:original"
    monkeypatch.setattr(runtime, "resolve_runtime_configurable", lambda _: original)
    timing = (
        {"cron": "0 9 * * *"}
        if recurring
        else {
            "at": (datetime.now(UTC) + timedelta(days=1))
            .replace(second=0, microsecond=0)
            .isoformat()
        }
    )

    async def check() -> None:
        created = await schedules.create(
            owner={"type": owner}, prompt="hello", new_thread=override, **timing
        )
        assert created["new_thread"] is expected
        # A same-thread schedule posts to its thread's origin when it fires.
        assert created["channel"] == ("team" if expected else None)
        assert created["target"] == (context["mda_channel"]["target"] if expected else None)
        wire = native.stored[created["id"]]
        assert wire["thread_id"] == (None if expected else ASSISTANT)
        assert wire["payload"]["context"] == {
            "mda_schedule": {"name": wire["metadata"]["mda_schedule"]}
        }
        # The new thread each run starts gets the record; the agent owns none of it.
        record = parse_thread_record(wire["metadata"])
        assert (record is not None) is expected
        if record is not None:
            assert dict(record.participants) == ({"alice": "owner"} if owner == "user" else {})
        execution = wire["payload"]["config"]["configurable"]["langgraph_auth_user"]
        assert execution["mda_source_provider"] == "slack"
        assert execution["mda_source_thread_id"] == "slack:original"
        assert execution["mda_scheduled_run"] is True
        assert (execution.get("mda_user_kind") == "service") is (owner == "agent")
        await schedules.update(created["id"], {"prompt": "changed"}, owner={"type": owner})
        assert wire["payload"]["config"]["configurable"]["langgraph_auth_user"] == execution
        assert (await schedules.get(created["id"], owner={"type": owner}))["new_thread"] is expected

    asyncio.run(check())
    assert context["mda_channel"]["event"]["type"] == "message"


def test_capture_uses_native_langgraph_runtime_context(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langgraph.graph import END, START, StateGraph  # noqa: PLC0415

    context = {
        "channel": "application-value",
        "mda_channel": {
            "name": "team",
            "provider": "slack",
            "transport": "trigger_server",
            "source_thread_key": "source",
            "delivery_id": "original",
            "target": {
                "channel_id": "install",
                "source_thread_key": "source",
                "address": {"conversation_id": "D123", "thread_id": "123.456"},
            },
            "event": {"type": "message", "messages": []},
            "raw_event": {"channel_type": "im"},
        },
    }
    configurable = runtime.resolve_runtime_configurable(None)
    configurable["langgraph_auth_user"].update(
        mda_source_provider="slack", mda_source_thread_id="source"
    )
    monkeypatch.setattr(runtime, "resolve_runtime_configurable", lambda _: configurable)
    monkeypatch.setattr(binding, "channel_provider_for_name", lambda _: "slack")

    async def create(_state: dict[str, object]) -> dict[str, object]:
        item = await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="hello")
        return {"schedule": item}

    graph = StateGraph(dict)
    graph.add_node("create", create)
    graph.add_edge(START, "create")
    graph.add_edge("create", END)
    result = asyncio.run(graph.compile().ainvoke({}, context=context))
    assert result["schedule"]["channel"] == "team"
    assert result["schedule"]["target"] == context["mda_channel"]["target"]


def test_background_records_and_immutable_channel_options(native: SimpleNamespace) -> None:
    async def check() -> None:
        item = await schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="background")
        assert item["channel"] is None
        assert item["target"] is None
        assert item["new_thread"] is True
        for patch in ({"new_thread": False}, {"deliver_to": {}}, {"thread": {}}):
            with pytest.raises(ValueError, match="fixed at creation"):
                await schedules.update(
                    item["id"], cast("runtime.SchedulePatch", patch), owner={"type": "user"}
                )

    asyncio.run(check())


def test_invalid_channel_context_cannot_become_a_background_schedule(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "ambient_context", lambda: {"mda_channel": {"name": "forged"}})
    with pytest.raises(ValueError, match="invalid channel"):
        asyncio.run(schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="bad"))
    assert not native.stored


def test_input_is_stored_only_in_native_payload(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        input = {"text": "x" * 600_000}
        item = await schedules.create(owner=owner, cron="0 9 * * *", input=input)
        wire = native.stored[item["id"]]
        assert "input" not in wire["metadata"]["mda_schedule_spec"]
        wire["metadata"]["mda_schedule_spec"] = {"cron": "0 9 * * *", "input": {"text": "old"}}
        assert (await schedules.get(item["id"], owner=owner))["input"] == input
        await schedules.update(item["id"], {"paused": True}, owner=owner)
        assert "input" not in wire["metadata"]["mda_schedule_spec"]

    asyncio.run(check())


@pytest.mark.parametrize("include_expired", [True, False])
def test_large_legacy_pages(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, include_expired: bool
) -> None:
    request = runtime._request

    async def handle(req: httpx.Request) -> httpx.Response:
        ctx = await runtime._context({"type": "user"}, runtime._run_configurable(None))
        result = await request(ctx, req.method, req.url.path, json.loads(req.content))
        return httpx.Response(200, json=result)

    monkeypatch.setattr(runtime, "_request", native.http_request)
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handle))
    )

    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        ids = []
        for _ in range(5):
            input = {"text": "x" * 450_000}
            item = await schedules.create(owner=owner, cron="0 9 * * *", input=input)
            native.stored[item["id"]]["metadata"]["mda_schedule_spec"] = {
                "cron": "0 9 * * *",
                "input": input,
            }
            ids.append(item["id"])
        native.stored[ids[0]]["end_time"] = "2000-01-01T00:00:00Z"
        expected = ids if include_expired else ids[1:]
        page = await schedules.list(owner=owner, limit=1, include_expired=include_expired)
        assert [item["id"] for item in page["items"]] == expected[:1]
        assert page["next_offset"] == 1
        page = await schedules.list(owner=owner, limit=100, include_expired=include_expired)
        assert [item["id"] for item in page["items"]] == expected
        assert page["next_offset"] is None

    asyncio.run(check())


@pytest.mark.parametrize(
    "channel", [{"name": 1, "target": {}}, {"name": "team", "target": object()}]
)
def test_invalid_returned_channel(native: SimpleNamespace, channel: object) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        item = await schedules.create(owner=owner, cron="0 9 * * *", prompt="test")
        native.stored[item["id"]]["payload"]["context"]["mda_channel"] = channel
        with pytest.raises(ValueError, match="validation"):
            await schedules.get(item["id"], owner=owner)

    asyncio.run(check())


def test_legacy_cron_is_read_and_refuses_a_context(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        created = await schedules.create(owner=owner, cron="0 9 * * *", prompt="legacy")
        wire = native.stored[created["id"]]
        del wire["payload"]["context"]
        wire["payload"]["config"]["configurable"].update(
            mda_schedule={"name": "legacy"}, channel=CHANNEL, langgraph_auth_permissions=[]
        )
        record = await schedules.get(created["id"], owner=owner)
        assert (record["channel"], record["target"]) == ("team", CHANNEL["target"])
        await schedules.update(created["id"], {"prompt": "updated"}, owner=owner)
        assert "context" not in native.patches[-1]
        with pytest.raises(ValueError, match="context"):
            await schedules.update(created["id"], {"context": {"team": "eng"}}, owner=owner)

    asyncio.run(check())


def test_context_is_stored_on_the_native_cron_and_replaced_on_update(
    native: SimpleNamespace,
) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        context: dict[str, JsonValue] = {"team": "eng", "limit": 3, "channel": "authored"}
        created = await schedules.create(
            owner=owner, cron="0 9 * * *", prompt="context", context=context
        )
        assert created["context"] == context
        wire = native.stored[created["id"]]
        name = wire["metadata"]["mda_schedule"]
        assert wire["payload"]["context"] == {"mda_schedule": {"name": name}, **context}
        assert "context" not in wire["metadata"]["mda_schedule_spec"]
        await schedules.update(created["id"], {"paused": True}, owner=owner)
        assert "context" not in native.patches[-1]
        updated = await schedules.update(created["id"], {"context": {"team": "ops"}}, owner=owner)
        assert updated["context"] == {"team": "ops"}
        assert wire["payload"]["context"] == {"mda_schedule": {"name": name}, "team": "ops"}
        for bad in ([], {"mda_channel": "x"}, {"mda_schedule": {}}):
            with pytest.raises(ValueError, match=r"validation|reserved"):
                await schedules.create(
                    owner=owner, cron="0 9 * * *", prompt="bad", context=cast("Any", bad)
                )

    asyncio.run(check())


def test_datetime_create_and_update(native: SimpleNamespace) -> None:
    async def check() -> None:
        owner: runtime.ScheduleOwner = {"type": "user"}
        at = datetime.now(UTC).replace(second=1, microsecond=0) + timedelta(days=1)
        expected = (
            (at.replace(second=0) + timedelta(minutes=1))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        item = await schedules.create(owner=owner, at=at, prompt="once")
        assert item["at"] == expected
        assert (await schedules.update(item["id"], {"at": at}, owner=owner))["at"] == expected

    asyncio.run(check())


@pytest.mark.parametrize("new_thread", [False, True])
def test_channel_thread_ownership(native: SimpleNamespace, new_thread: bool) -> None:
    native.source = {
        "mda_source_provider": "slack",
        "mda_source_thread_id": "slack:t:c",
    }
    native.thread_owner = "mda:source-thread:slack:slack:t:c"

    async def check() -> None:
        created = await schedules.create(
            owner={"type": "user"}, cron="0 9 * * *", prompt="test", new_thread=new_thread
        )
        wire = native.stored[created["id"]]
        saved = copy.deepcopy(wire["payload"]["config"]["configurable"]["langgraph_auth_user"])
        assert wire["thread_id"] == (None if new_thread else ASSISTANT)
        assert saved["mda_user_id"] == "alice"
        assert saved["mda_agent_auth_principal_id"] == "canonical-person"
        assert saved["mda_source_provider"] == ("schedule" if new_thread else "slack")
        assert saved.get("mda_source_thread_id") == (None if new_thread else "slack:t:c")
        assert _thread_owner_key(saved) == ("alice" if new_thread else native.thread_owner)
        native.source = {}
        await schedules.update(created["id"], {"prompt": "updated"}, owner={"type": "user"})
        assert wire["payload"]["config"]["configurable"]["langgraph_auth_user"] == saved

    asyncio.run(check())


def test_same_thread_ignores_client_source_fields(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime,
        "resolve_runtime_configurable",
        lambda _: {
            "assistant_id": ASSISTANT,
            "thread_id": ASSISTANT,
            "mda_source_provider": "slack",
            "mda_source_thread_id": "forged",
            "langgraph_auth_user": {
                "identity": "alice",
                "mda_user_id": "alice",
                "permissions": [],
            },
        },
    )
    created = asyncio.run(
        schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test", new_thread=False)
    )
    saved = native.stored[created["id"]]["payload"]["config"]["configurable"]["langgraph_auth_user"]
    assert _thread_owner_key(saved) == "alice"
    assert saved["mda_source_provider"] == "schedule"
    assert "mda_source_thread_id" not in saved


def test_rejects_calls_outside_an_agent_run(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "resolve_runtime_configurable", lambda _: None)
    with pytest.raises(ValueError, match=r"active agent run.*runtime\.schedules"):
        asyncio.run(schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test"))
    with pytest.raises(ValueError, match="active agent run"):
        asyncio.run(schedules.list(owner={"type": "user"}))
    assert native.stored == {}


def test_binds_schedules_to_the_trusted_caller_of_the_runtime(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound_runtime = object()
    ambient = runtime.resolve_runtime_configurable
    monkeypatch.setattr(
        runtime,
        "resolve_runtime_configurable",
        lambda value: (
            {
                "assistant_id": ASSISTANT,
                "thread_id": ASSISTANT,
                "langgraph_auth_user": {
                    "identity": "dave",
                    "mda_user_id": "dave",
                    "permissions": [],
                },
            }
            if value is bound_runtime
            else ambient(value)
        ),
    )
    created = asyncio.run(
        runtime.RuntimeSchedules(bound_runtime).create(
            owner={"type": "user"}, cron="0 9 * * *", prompt="test"
        )
    )
    assert created["owner"] == {"type": "user", "id": "dave"}
    assert asyncio.run(schedules.list(owner={"type": "user"}))["items"] == []


def test_managed_tool_runtime_exposes_bound_schedules(native: SimpleNamespace) -> None:
    native_runtime = ToolRuntime(
        state={},
        context=None,
        config={"configurable": {"assistant_id": ASSISTANT}},
        stream_writer=lambda _: None,
        tool_call_id=None,
        store=None,
    )
    managed = adapt_managed_runtime(native_runtime, managed_runtime_overrides(native_runtime))
    assert isinstance(managed.schedules, runtime.RuntimeSchedules)
    created = asyncio.run(
        managed.schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="test")
    )
    assert created["owner"] == {"type": "user", "id": "alice"}


def test_schedules_import_uses_the_ambient_run(native: SimpleNamespace) -> None:
    from managed_deepagents import schedules as imported  # noqa: PLC0415

    async def check() -> None:
        created = await imported.create(owner={"type": "user"}, cron="0 9 * * *", prompt="imported")
        assert created["owner"] == {"type": "user", "id": "alice"}
        page = await imported.list(owner={"type": "user"})
        assert [item["id"] for item in page["items"]] == [created["id"]]
        await imported.delete(created["id"], owner={"type": "user"})

    asyncio.run(check())
    assert native.stored == {}


def test_direct_application_channel_is_not_schedule_routing(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel, "ambient_context", lambda: {"channel": "application-value"})
    item = asyncio.run(schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="hello"))
    assert item["channel"] is None
    assert item["target"] is None


def _on_thread(native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in a Slack thread whose record names alice, then bob, with no live delivery."""
    monkeypatch.setattr(binding, "channel_provider_for_name", lambda _: "slack")
    record = next_thread_record(next_thread_record(None, CHANNEL, "alice"), CHANNEL, "bob")
    thread = build_run_thread(
        thread_id=ASSISTANT, live=None, metadata=thread_record_metadata(record), configurable=None
    )
    monkeypatch.setattr(runtime, "schedule_thread", lambda _configurable: thread)
    native.source = {"mda_source_provider": "slack", "mda_source_thread_id": "slack:original"}
    native.thread_owner = "mda:source-thread:slack:slack:original"


@pytest.mark.parametrize("new_thread", [False, True])
def test_a_schedule_posts_to_the_threads_origin(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, new_thread: bool
) -> None:
    _on_thread(native, monkeypatch)
    created = asyncio.run(
        schedules.create(
            owner={"type": "user"}, cron="0 9 * * *", prompt="hi", new_thread=new_thread
        )
    )
    wire = native.stored[created["id"]]
    assert "mda_channel" not in wire["payload"]["context"]
    execution = wire["payload"]["config"]["configurable"]["langgraph_auth_user"]
    assert execution["mda_source_thread_id"] == "slack:original"
    record = parse_thread_record(wire["metadata"])
    if not new_thread:
        # It posts to its thread's origin when it fires.
        assert record is None
        assert created["channel"] is None
        return
    # The thread each run starts gets this record, with the origin's sender kept.
    assert record is not None
    assert record.origin is not None
    assert record.origin.sender == "bob"
    assert dict(record.participants) == {"alice": "owner"}
    assert created["channel"] == "team"


def test_run_as_runs_as_another_participant(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_thread(native, monkeypatch)
    bob = ThreadParticipant(id="bob", role="member")
    created = asyncio.run(
        schedules.create(owner={"type": "user"}, cron="0 9 * * *", prompt="hi", run_as=bob)
    )
    assert created["run_as"] == {"id": "bob", "role": "member"}
    # Alice still owns it: she lists, changes and deletes it.
    assert created["owner"] == {"type": "user", "id": "alice"}
    assert native.accesses[-1]["run_as"] == created["run_as"]
    execution = native.stored[created["id"]]["payload"]["config"]["configurable"][
        "langgraph_auth_user"
    ]
    # A Trigger sender's id is their Agent Auth principal.
    assert execution["mda_user_id"] == execution["mda_agent_auth_principal_id"] == "bob"


@pytest.mark.parametrize(
    ("owner", "participant", "match"),
    [
        ({"type": "agent"}, ThreadParticipant(id="bob", role="member"), "owner"),
        ({"type": "user"}, ThreadParticipant(id="mallory", role="member"), "participant"),
    ],
)
def test_run_as_is_refused(
    native: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    owner: Any,
    participant: ThreadParticipant,
    match: str,
) -> None:
    _on_thread(native, monkeypatch)
    with pytest.raises(ValueError, match=match):
        asyncio.run(
            schedules.create(owner=owner, cron="0 9 * * *", prompt="hi", run_as=participant)
        )


def _execution(user_id: str = "alice") -> Any:
    return {
        "identity": user_id,
        "permissions": [],
        "mda_user_id": user_id,
        "mda_agent_auth_principal_id": user_id,
        "mda_source_provider": "slack",
        "mda_source_thread_id": "slack:original",
        "mda_scheduled_run": True,
    }


@pytest.mark.parametrize(
    ("user_id", "run_as", "accepted"),
    [("alice", None, True), ("bob", {"id": "bob", "role": "member"}, True), ("bob", None, False)],
    ids=["owner", "run-as", "neither"],
)
def test_schedule_handoff_runs_as_the_owner_or_its_run_as(
    monkeypatch: pytest.MonkeyPatch, user_id: str, run_as: object, accepted: bool
) -> None:
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "local-test-secret")
    access: Any = {"owner": "user:alice", "assistant": ASSISTANT, "execution": _execution(user_id)}
    if run_as is not None:
        access["run_as"] = run_as
    headers = {
        "x-mda-handoff-secret": "local-test-secret",
        _schedule_auth.SCHEDULE_HANDOFF_HEADER: json.dumps(access),
    }
    if not accepted:
        with pytest.raises(ValueError, match="not permitted"):
            schedule_handoff_user(headers, "/runs/crons", "POST")
        return
    user = schedule_handoff_user(headers, "/runs/crons", "POST")
    assert user is not None
    assert user["mda_user_id"] == user_id
