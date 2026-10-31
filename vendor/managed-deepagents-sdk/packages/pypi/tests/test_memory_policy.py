"""Memory policy decisions at the graph factory boundary."""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, NotRequired, Required, TypedDict, cast

import pytest
from langgraph_sdk.runtime import ServerRuntime
from pydantic import BaseModel
from typing_extensions import TypedDict as ExtensionTypedDict

from managed_deepagents import (
    DeepAgentDefinition,
    ManagedChannelContext,
    MemoryLayer,
    define_deep_agent,
    define_memory,
    runtime,
)
from managed_deepagents._channels.binding import _current_channel
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._memory.mounts import UnmountedMemoryBackend
from managed_deepagents._memory.policy import resolve_user_memory_access
from managed_deepagents._memory.user_memory import UserMemoryBackend, user_memory_repo_handle


def _mounted_routes(backend: Any) -> set[str]:
    """Route prefixes backed by storage; unmounted memory layers only reject access."""
    return {
        prefix
        for prefix, route in backend.routes.items()
        if not isinstance(route, UnmountedMemoryBackend)
    }


def _config(kind: str = "person", user_id: str = "alice") -> dict[str, Any]:
    return {
        "configurable": {
            "langgraph_auth_user": {
                "identity": user_id,
                "mda_user_id": user_id,
                "mda_user_kind": kind,
                "mda_source_provider": "slack",
            }
        }
    }


def _channel_context(channel_type: object = "im") -> dict[str, object]:
    channel = {
        "name": "support",
        "provider": "slack",
        "sourceThreadKey": "source",
        "deliveryId": "delivery",
        "transport": "direct",
        "target": {},
        "event": {"type": "message", "messages": []},
        "rawEvent": {"channel_type": channel_type},
    }
    return {"channel": channel, "mda_channel": channel}


def _channel_config() -> dict[str, Any]:
    config = _config()
    config["configurable"]["langgraph_auth_user"]["mda_source_thread_id"] = "source"
    return config


@pytest.fixture
def compiled(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    register_channel_messaging([SimpleNamespace(name="support", kind="slack")])

    class Agent:
        def with_config(self, config: object) -> Agent:
            return self

    class Backend:
        def __init__(self, repo: str) -> None:
            captured.setdefault("opened", []).append(repo)

        def download_files(self, paths: list[str]) -> list[object]:
            return [SimpleNamespace(content=b"instructions", error=None)]

    monkeypatch.setattr(runtime, "ContextHubBackend", Backend)
    monkeypatch.setattr(runtime, "CompositeBackend", SimpleNamespace)
    monkeypatch.setattr(runtime, "StateBackend", object)
    monkeypatch.setattr(runtime, "FilesystemPermission", lambda **kw: kw)
    monkeypatch.setattr(runtime, "create_deep_agent", lambda **kw: captured.update(kw) or Agent())
    return captured


def _compile(
    context: object,
    *,
    memory: object = None,
    config: object = None,
    schema: object = None,
    async_factory: bool = True,
) -> object:
    kwargs: dict[str, Any] = {
        "context_repo": "agent",
        "run_context": context,
        "memory": memory if memory is not None else define_memory(scope={"type": "user"}),
    }
    definition_config: dict[str, object] = {
        "model": "test",
        **({"context_schema": schema} if schema else {}),
    }
    definition = DeepAgentDefinition(definition_config)
    if async_factory:
        return asyncio.run(
            runtime.compile_managed_agent_async(definition, config or _config(), **kwargs)
        )
    return runtime.compile_managed_agent(definition, config or _config(), **kwargs)


@pytest.mark.parametrize(
    ("context", "allowed"),
    [
        (None, False),
        ({}, False),
        ({"provider": "slack", "raw_event": {"channel_type": "channel"}}, False),
        ({"provider": "slack", "raw_event": {}}, False),
        (_channel_context(), True),
    ],
)
@pytest.mark.parametrize("async_factory", [True, False])
def test_default_policy_requires_slack_dm_context(
    compiled: dict[str, Any], context: object, allowed: bool, async_factory: bool
) -> None:
    _compile(context, config=_channel_config(), async_factory=async_factory)
    assert ("/memories/user/" in _mounted_routes(compiled["backend"])) is allowed


def test_resume_without_current_raw_event_denies_stale_dm_memory(compiled: dict[str, Any]) -> None:
    config = _channel_config()
    config["configurable"]["mda_channel_binding"] = _channel_context()["channel"]
    context = ManagedChannelContext.model_validate(
        {
            "channel": {
                **{
                    key: value
                    for key, value in _channel_context()["channel"].items()
                    if key != "rawEvent"
                },
                "event": {"type": "interrupt_resume", "resume": {"interrupt": True}},
            }
        }
    )
    _compile(context, config=config)
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


def test_studio_bypasses_user_memory_policy(compiled: dict[str, Any]) -> None:
    calls: list[object] = []

    def allow(context: object) -> bool:
        calls.append(context)
        return False

    _compile(
        {},
        config={
            "configurable": {
                "langgraph_auth_user": {
                    "kind": "StudioUser",
                    "is_authenticated": True,
                    "ls_user_id": "alice",
                }
            }
        },
        memory=define_memory(user=MemoryLayer(allow=allow)),
    )
    assert "/memories/user/" in _mounted_routes(compiled["backend"])
    assert calls == []


def test_api_callers_cannot_claim_studio_policy_exception(compiled: dict[str, Any]) -> None:
    calls: list[object] = []

    def allow(context: object) -> bool:
        calls.append(context)
        return False

    _compile(
        {},
        config={
            "configurable": {
                "langgraph_auth_user": {"mda_user_id": "alice"},
                "mda_source_provider": "studio",
            }
        },
        memory=define_memory(user=MemoryLayer(allow=allow)),
    )
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])
    assert calls == [{}]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_custom_allow_runs_once_before_io_and_overrides_default(
    compiled: dict[str, Any],
    asynchronous: bool,
) -> None:
    context = {"provider": "slack", "raw_event": {"channel_type": "channel"}, "opt_in": True}
    calls: list[object] = []

    def allow(ctx: object) -> bool:
        assert "opened" not in compiled
        calls.append(ctx)
        return True

    async def async_allow(ctx: object) -> bool:
        await asyncio.sleep(0)
        return allow(ctx)

    _compile(
        context,
        memory=define_memory(
            scope={"type": "user", "allow": async_allow if asynchronous else allow}
        ),
    )
    assert calls == [context]
    assert "/memories/user/" in _mounted_routes(compiled["backend"])
    backend = compiled["backend"].routes["/memories/user/"]._backend
    assert backend._repo_handle().startswith("agent--u--")
    assert calls == [context]


@pytest.mark.parametrize("kind", ["service", "missing"])
def test_allow_cannot_override_trusted_person(compiled: dict[str, Any], kind: str) -> None:
    config = _config(kind) if kind != "missing" else {"configurable": {}}
    _compile(
        {}, config=config, memory=define_memory(scope={"type": "user", "allow": lambda ctx: True})
    )
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


def test_allow_cannot_override_asserted_id(compiled: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must match the authenticated principal"):
        _compile(
            {}, memory=define_memory(scope={"type": "user", "id": "bob", "allow": lambda ctx: True})
        )
    assert "opened" not in compiled


@pytest.mark.parametrize("asynchronous", [False, True])
def test_policy_errors_hide_context(compiled: dict[str, Any], asynchronous: bool) -> None:
    def allow(ctx: object) -> bool:
        raise RuntimeError("private context value")

    async def async_allow(ctx: object) -> bool:
        return allow(ctx)

    with pytest.raises(ValueError, match=r"User memory allow\(context\) failed") as error:
        _compile(
            {},
            memory=define_memory(
                scope={"type": "user", "allow": async_allow if asynchronous else allow}
            ),
        )
    assert "private context value" not in "".join(traceback.format_exception(error.value))
    assert "opened" not in compiled


@dataclass
class _Context:
    allowed: bool = True


class _ModelContext(BaseModel):
    allowed: bool = True


class _DictContext(TypedDict):
    allowed: bool


@pytest.mark.parametrize("schema", [_Context, _ModelContext, _DictContext])
def test_policy_receives_authored_context_schema(compiled: dict[str, Any], schema: type) -> None:
    seen: list[object] = []

    def allow(ctx: Any) -> bool:
        seen.append(ctx)
        return ctx["allowed"] if isinstance(ctx, dict) else ctx.allowed

    value = {"allowed": True} if schema is _DictContext else {}
    _compile(value, schema=schema, memory=define_memory(scope={"type": "user", "allow": allow}))
    assert len(seen) == 1
    assert seen[0] == {"allowed": True} if schema is _DictContext else isinstance(seen[0], schema)
    assert compiled["context_schema"] is schema


@pytest.mark.parametrize("schema", [_Context, _ModelContext])
def test_policy_preserves_existing_context_instances(
    compiled: dict[str, Any], schema: type
) -> None:
    context = schema()
    seen: list[object] = []
    _compile(
        context,
        schema=schema,
        memory=define_memory(scope={"type": "user", "allow": lambda ctx: seen.append(ctx) or True}),
    )
    assert seen[0] is context


def test_allowed_backend_cannot_switch_to_another_person(
    compiled: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _compile(_channel_context(), config=_channel_config())
    backend = compiled["backend"].routes["/memories/user/"]._backend
    monkeypatch.setattr(
        "managed_deepagents._memory.user_memory.resolve_runtime_configurable",
        lambda config: _config(user_id="bob")["configurable"],
    )
    with pytest.raises(ValueError, match="allowed caller"):
        backend._repo_handle()


def test_agent_memory_does_not_require_event_context(compiled: dict[str, Any]) -> None:
    _compile({}, memory=define_memory(scope={"type": "agent"}))
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])


def test_async_allow_uses_the_factory_event_loop(compiled: dict[str, Any]) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        loop.call_soon(future.set_result, True)

        def allow(ctx: object) -> Any:
            assert asyncio.get_running_loop() is loop
            return future

        await runtime.compile_managed_agent_async(
            DeepAgentDefinition({"model": "test"}),
            _config(),
            context_repo="agent",
            memory=define_memory(scope={"type": "user", "allow": allow}),
            run_context={},
        )

    asyncio.run(run())
    assert "/memories/user/" in _mounted_routes(compiled["backend"])


def test_rechecks_policy_for_each_run(compiled: dict[str, Any]) -> None:
    seen: list[object] = []

    def allow(ctx: Any) -> bool:
        seen.append(ctx)
        return ctx["allowed"]

    memory = define_memory(scope=[{"type": "agent"}, {"type": "user", "allow": allow}])
    _compile({"allowed": True}, memory=memory)
    first = compiled["backend"]
    _compile({"allowed": False}, memory=memory)
    assert len(seen) == 2
    assert "/memories/user/" in _mounted_routes(first)
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])


def test_policy_context_does_not_include_server_configurable(compiled: dict[str, Any]) -> None:
    seen: list[object] = []
    _compile(
        None,
        memory=define_memory(
            scope={"type": "user", "allow": lambda ctx: seen.append(ctx) or False}
        ),
    )
    assert seen == [None]


@pytest.mark.parametrize(
    ("audience", "allowed"), [("private", False), ("public", False), (None, False)]
)
def test_default_denies_missing_event_even_with_removed_audience_stamp(
    compiled: dict[str, Any],
    audience: str | None,
    allowed: bool,
) -> None:
    config = _config()
    config["configurable"]["langgraph_auth_user"]["mda_source_audience"] = audience
    _compile(None, config=config)
    assert ("/memories/user/" in _mounted_routes(compiled["backend"])) is allowed


def test_shared_slack_event_overrides_trusted_private_audience(
    compiled: dict[str, Any],
) -> None:
    config = _config()
    config["configurable"]["langgraph_auth_user"]["mda_source_audience"] = "private"
    _compile({"provider": "slack", "raw_event": {"channel_type": "channel"}}, config=config)
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


def test_schema_inspection_does_not_call_user_policy(compiled: dict[str, Any]) -> None:
    def allow(context: object) -> bool:
        raise AssertionError("schema inspection must not execute policy")

    asyncio.run(
        runtime.compile_managed_agent_async(
            DeepAgentDefinition({"model": "test"}),
            _config(),
            context_repo="agent",
            memory=define_memory(scope=[{"type": "agent"}, {"type": "user", "allow": allow}]),
            is_run=False,
        )
    )
    assert "backend" not in compiled
    assert "memory" not in compiled


def test_policy_accepts_typing_extensions_context_schema(compiled: dict[str, Any]) -> None:
    class Context(ExtensionTypedDict):
        allowed: bool

    seen: list[object] = []
    context = {"allowed": True}
    _compile(
        context,
        schema=Context,
        memory=define_memory(
            scope={
                "type": "user",
                "allow": lambda ctx: seen.append(ctx) or True,
            }
        ),
    )
    assert seen == [context]


@pytest.mark.parametrize("schema", [_Context, _ModelContext, _DictContext])
def test_policy_uses_native_schema_coercion(compiled: dict[str, Any], schema: type) -> None:
    seen: list[object] = []
    context = {
        "allowed": True,
        "provider": "slack",
        "raw_event": {"channel_type": "im"},
        "extra": "hidden",
    }
    _compile(
        context,
        schema=schema,
        memory=define_memory(
            scope={
                "type": "user",
                "allow": lambda ctx: seen.append(ctx) or True,
            }
        ),
    )
    expected = (
        [] if schema is _Context else [context if schema is _DictContext else schema(allowed=True)]
    )
    assert seen == expected
    assert context["extra"] == "hidden"


@pytest.mark.parametrize("schema", [_Context, _ModelContext, _DictContext])
def test_channel_default_uses_managed_schema_instead_of_authored_schema(
    compiled: dict[str, Any], schema: type
) -> None:
    _compile(_channel_context(), schema=schema, config=_channel_config())
    assert "/memories/user/" in _mounted_routes(compiled["backend"])


def test_empty_context_schema_preserves_fields(compiled: dict[str, Any]) -> None:
    class EmptyContext(TypedDict):
        pass

    _compile(_channel_context(), schema=EmptyContext, config=_channel_config())
    assert "/memories/user/" in _mounted_routes(compiled["backend"])


@pytest.mark.parametrize("provider", ["http", "custom", None])
def test_http_cannot_claim_default_slack_memory(
    compiled: dict[str, Any], provider: str | None
) -> None:
    config = _config()
    config["configurable"]["langgraph_auth_user"]["mda_source_provider"] = provider
    _compile({"provider": "slack", "raw_event": {"channel_type": "im"}}, config=config)
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


@pytest.mark.parametrize("channel_type", ["mpim", "channel", "group", "unknown", None, {}, True])
def test_default_rejects_non_dm_events(compiled: dict[str, Any], channel_type: object) -> None:
    _compile(
        {
            "provider": "slack",
            "raw_event": {"channel_type": channel_type},
            "source": {"audience": "private"},
        }
    )
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


def test_default_ignores_removed_audience_field(compiled: dict[str, Any]) -> None:
    _compile({"source": {"audience": "private"}})
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


@pytest.mark.parametrize("agent_allowed", [False, True])
@pytest.mark.parametrize("user_allowed", [False, True])
def test_named_memory_policies_are_independent(
    compiled: dict[str, Any], agent_allowed: bool, user_allowed: bool
) -> None:
    calls: list[str] = []
    context = {"remember": True}

    def agent_allow(ctx: object) -> bool:
        assert ctx == context
        calls.append("agent")
        return agent_allowed

    async def user_allow(ctx: object) -> bool:
        assert ctx == context
        calls.append("user")
        return user_allowed

    _compile(
        context,
        memory=define_memory(
            agent=MemoryLayer(allow=agent_allow), user=MemoryLayer(allow=user_allow)
        ),
    )
    routes = _mounted_routes(compiled["backend"])
    assert ("/memories/agent/" in routes) is agent_allowed
    assert ("/memories/user/" in routes) is user_allowed
    assert calls == ["agent", "user"]
    paths = [
        *(["/memories/agent/AGENTS.md"] if agent_allowed else []),
        *(["/memories/user/AGENTS.md"] if user_allowed else []),
    ]
    assert compiled.get("memory") == (paths or None)


def test_agent_policy_runs_for_service_callers(compiled: dict[str, Any]) -> None:
    _compile(
        {"remember": True},
        config=_config("service"),
        memory=define_memory(
            agent=MemoryLayer(allow=lambda ctx: ctx["remember"]),
            user=MemoryLayer(allow=lambda ctx: True),
        ),
    )
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


def test_inspection_skips_agent_policy(compiled: dict[str, Any]) -> None:
    def allow(ctx: object) -> bool:
        raise AssertionError("must not call policy during inspection")

    asyncio.run(
        runtime.compile_managed_agent_async(
            DeepAgentDefinition({"model": "test"}),
            _config(),
            context_repo="agent",
            memory=define_memory(agent=MemoryLayer(allow=allow)),
            is_run=False,
        )
    )
    assert "backend" not in compiled
    assert "memory" not in compiled


def test_factory_inspection_skips_memory_policy(
    compiled: dict[str, Any],
) -> None:
    calls: list[object] = []

    def build(runtime: ServerRuntime[object]) -> DeepAgentDefinition:
        assert runtime.execution_runtime is None
        assert runtime.access_context == "assistants.read"
        return define_deep_agent(name="inspection")

    asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            _config(),
            context_repo="agent",
            memory=define_memory(agent=MemoryLayer(allow=lambda ctx: calls.append(ctx) or True)),
            is_run=False,
        )
    )

    assert calls == []
    assert "memory" not in compiled


def test_dynamic_agent_selects_memory_from_context(compiled: dict[str, Any]) -> None:
    def build(server_runtime: ServerRuntime[dict[str, object]]) -> DeepAgentDefinition:
        execution = server_runtime.execution_runtime
        context = execution.context if execution is not None else {}
        return define_deep_agent(
            name="dynamic-memory",
            memory=define_memory(agent=MemoryLayer()) if context.get("remember") is True else None,
        )

    asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            _config(),
            context_repo="agent",
            memory=define_memory(user=MemoryLayer()),
            run_context={"remember": True},
        )
    )
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])

    asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            _config(),
            context_repo="agent",
            memory=define_memory(agent=MemoryLayer()),
            run_context={"remember": False},
        )
    )
    assert "/memories/agent/" not in _mounted_routes(compiled["backend"])


def test_dynamic_user_selection_replaces_default_policy_after_identity(
    compiled: dict[str, Any],
) -> None:
    def build(_runtime: ServerRuntime[dict[str, object]]) -> DeepAgentDefinition:
        return define_deep_agent(
            name="dynamic-user-memory",
            memory=define_memory(user=MemoryLayer()),
        )

    asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            _config(),
            context_repo="agent",
            run_context={},
        )
    )
    assert "/memories/user/" in _mounted_routes(compiled["backend"])


def test_dynamic_user_selection_cannot_grant_service_principal(
    compiled: dict[str, Any],
) -> None:
    def build(_runtime: ServerRuntime[dict[str, object]]) -> DeepAgentDefinition:
        return define_deep_agent(
            name="dynamic-user-memory",
            memory=define_memory(user=MemoryLayer()),
        )

    asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            _config("service"),
            context_repo="agent",
            run_context={},
        )
    )
    assert "/memories/user/" not in _mounted_routes(compiled["backend"])


@pytest.mark.parametrize("result", [None, "yes", 1, {}])
def test_agent_policy_rejects_non_boolean(compiled: dict[str, Any], result: object) -> None:
    with pytest.raises(ValueError, match=r"Agent memory allow\(context\) must return a boolean"):
        _compile({}, memory=define_memory(agent=MemoryLayer(allow=cast("Any", lambda ctx: result))))


def test_agent_policy_hides_callback_errors(compiled: dict[str, Any]) -> None:
    async def allow(context: object) -> bool:
        raise RuntimeError("private context details")

    with pytest.raises(ValueError, match=r"^Agent memory allow\(context\) failed$"):
        _compile({}, memory=define_memory(agent=MemoryLayer(allow=allow)))


def test_agent_policy_changes_on_resume(compiled: dict[str, Any]) -> None:
    calls: list[object] = []

    def allow(context: dict[str, object]) -> bool:
        calls.append(context)
        return context.get("remember") is True

    memory = define_memory(agent=MemoryLayer(allow=allow))
    _compile({"remember": True}, memory=memory)
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])
    _compile({"remember": False}, memory=memory)
    assert "/memories/agent/" not in _mounted_routes(compiled["backend"])
    assert calls == [{"remember": True}, {"remember": False}]


@dataclass
class _RequiredContext:
    project: str


class _RequiredModelContext(BaseModel):
    project: str


class _OptionalDictContext(TypedDict):
    project: NotRequired[str]


class _RequiredDictContext(TypedDict, total=False):
    project: Required[str]


class _OptionalExtensionDictContext(ExtensionTypedDict):
    project: NotRequired[str]


class _RequiredExtensionDictContext(ExtensionTypedDict, total=False):
    project: Required[str]


class _OptionalAnnotatedDictContext(TypedDict):
    project: Annotated[NotRequired[str], "Project name"]


class _RequiredAnnotatedDictContext(TypedDict, total=False):
    project: Annotated[Required[str], "Project name"]


@pytest.mark.parametrize("schema", [_RequiredContext, _RequiredModelContext])
@pytest.mark.parametrize("layer", ["agent", "user"])
def test_invalid_channel_context_denies_only_the_layer(
    compiled: dict[str, Any], schema: type, layer: str
) -> None:
    def allow(ctx: object) -> bool:
        raise AssertionError("invalid context must not reach the callback")

    _compile(
        {"provider": "slack", "raw_event": {"channel_type": "im"}},
        schema=schema,
        memory=define_memory(**{layer: MemoryLayer(allow=allow)}),
    )
    assert f"/memories/{layer}/" not in _mounted_routes(compiled["backend"])


@pytest.mark.parametrize("base", [TypedDict, ExtensionTypedDict])
@pytest.mark.parametrize("required", [False, True])
def test_typed_dict_keeps_native_passthrough_context(
    compiled: dict[str, Any], base: Any, required: bool
) -> None:
    context_schema = base("Context", {"project": str}, total=required)
    calls: list[object] = []

    def allow(context: dict[str, object]) -> bool:
        calls.append(context)
        return True

    _compile(
        {"provider": "slack", "raw_event": {"channel_type": "im"}},
        schema=context_schema,
        memory=define_memory(agent=MemoryLayer(allow=allow)),
    )
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])
    assert calls == [{"provider": "slack", "raw_event": {"channel_type": "im"}}]


@pytest.mark.parametrize(
    ("schema", "required"),
    [
        (_OptionalDictContext, False),
        (_RequiredDictContext, True),
        (_OptionalExtensionDictContext, False),
        (_RequiredExtensionDictContext, True),
        (_OptionalAnnotatedDictContext, False),
        (_RequiredAnnotatedDictContext, True),
    ],
)
def test_typed_dict_markers_do_not_add_runtime_validation(
    compiled: dict[str, Any], schema: type, required: bool
) -> None:
    calls: list[object] = []

    def allow(context: dict[str, object]) -> bool:
        calls.append(context)
        return True

    _compile({}, schema=schema, memory=define_memory(agent=MemoryLayer(allow=allow)))
    assert "/memories/agent/" in _mounted_routes(compiled["backend"])
    assert calls == [{}]


def test_local_developer_memory_policy_and_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MDA_LOCAL_DEV", "1")
    monkeypatch.setenv("MDA_AGENT_AUTH_PRINCIPAL_ID", "developer-alice")
    config = _config("service", "mda:local-dev")
    calls: list[object] = []

    def allow(context: object) -> bool:
        calls.append(context)
        return False

    user_id = asyncio.run(
        resolve_user_memory_access({"type": "user", "allow": allow}, config, {}, None)
    ).user_id
    assert user_id == "developer-alice"
    assert not calls
    with pytest.raises(ValueError, match="id must match"):
        asyncio.run(resolve_user_memory_access({"type": "user", "id": "bob"}, config, {}, None))
    backend = UserMemoryBackend(
        deploy_handle="local",
        filesystem_root=str(tmp_path),
        run_config=config,
        allowed_user_id=user_id,
    )
    backend.write("/AGENTS.md", "test memory")
    assert (
        tmp_path / "__users__" / user_memory_repo_handle("local", "developer-alice") / "AGENTS.md"
    ).read_text() == "test memory"
    monkeypatch.setenv("MDA_AGENT_AUTH_PRINCIPAL_ID", "developer-bob")
    with pytest.raises(ValueError, match="allowed caller"):
        backend.read("/AGENTS.md")


@pytest.mark.parametrize(
    ("local", "principal", "user_id"),
    [
        ("", "developer-alice", "mda:local-dev"),
        ("1", "", "mda:local-dev"),
        ("1", "developer-alice", "other-service"),
    ],
)
def test_developer_memory_requires_local_authentication(
    monkeypatch: pytest.MonkeyPatch, local: str, principal: str, user_id: str
) -> None:
    monkeypatch.setenv("MDA_LOCAL_DEV", local)
    monkeypatch.setenv("MDA_AGENT_AUTH_PRINCIPAL_ID", principal)
    assert (
        asyncio.run(
            resolve_user_memory_access(
                {"type": "user", "allow": lambda _: True}, _config("service", user_id), {}, None
            )
        ).user_id
        is None
    )


_DM = _channel_context()
_USER_LAYER: MemoryLayer = {"type": "user"}


# A bare ``None`` return collapsed these four into one, so adoption measured
# from the presence of ``memory.py`` could not tell them apart.
@pytest.mark.usefixtures("compiled")
class TestUserMemoryDenialReasons:
    def _resolve(
        self,
        context: object,
        config: dict[str, Any] | None = None,
        layer: MemoryLayer | None = None,
    ) -> Any:
        return asyncio.run(
            resolve_user_memory_access(
                cast("Any", _USER_LAYER if layer is None else layer),
                _channel_config() if config is None else config,
                context,
                None,
            )
        )

    def test_grants_with_no_reason(self) -> None:
        access = self._resolve(_DM)

        assert access.user_id == "alice"
        assert access.denied_reason is None

    def test_gives_a_github_sender_user_memory_only_where_allowed(self) -> None:
        config: dict[str, Any] = {
            "configurable": {
                "langgraph_auth_user": {
                    "identity": "github:7",
                    "mda_user_id": "github:7",
                    "mda_source_provider": "github",
                    "mda_source_thread_id": "github:3/1",
                }
            }
        }
        # An issue thread is shared, like a Slack channel.
        assert self._resolve(None, config).denied_reason == "not_slack_dm"
        layer = cast("MemoryLayer", {"type": "user", "allow": lambda context: True})
        assert self._resolve(None, config, layer).user_id == "github:7"

    def test_reports_a_principal_that_is_not_a_person(self) -> None:
        access = self._resolve(_DM, _config(kind="service"))

        assert access.user_id is None
        assert access.denied_reason == "not_a_person"

    @pytest.mark.parametrize(
        "context",
        [
            _channel_context("channel"),
            None,
        ],
    )
    def test_reports_a_non_dm_as_not_slack_dm(self, context: object) -> None:
        assert self._resolve(context).denied_reason == "not_slack_dm"

    def test_reports_a_custom_policy_that_returned_false(self) -> None:
        layer = cast("MemoryLayer", {"type": "user", "allow": lambda _ctx: False})

        access = self._resolve(_DM, layer=layer)

        assert access.user_id is None
        assert access.denied_reason == "policy_denied"

    def test_an_absent_layer_is_not_a_denial(self) -> None:
        access = asyncio.run(resolve_user_memory_access(None, _config(), _DM, None))

        assert access.user_id is None
        assert access.denied_reason is None


@pytest.mark.parametrize("channel_type", ["im", "channel"])
@pytest.mark.parametrize("service", [False, True])
def test_scheduled_channel_keeps_memory_policy(
    compiled: dict[str, Any], channel_type: str, service: bool
) -> None:
    from managed_deepagents._memory.policy import resolve_user_memory_access  # noqa: PLC0415
    from managed_deepagents.run_context import ManagedChannelContext  # noqa: PLC0415

    context = _channel_context(channel_type)
    cast("dict[str, object]", context["channel"])["event"] = {
        "type": "schedule",
        "new_thread": True,
    }
    config = _channel_config()
    if service:
        config["configurable"]["langgraph_auth_user"]["mda_user_kind"] = "service"
    access = asyncio.run(
        resolve_user_memory_access({"type": "user"}, config, context, ManagedChannelContext)
    )
    assert (access.user_id == "alice") is (channel_type == "im" and not service)


def test_default_policy_does_not_use_an_outer_delivery_when_context_is_missing() -> None:
    token = _current_channel.set(_channel_context()["channel"])
    try:
        access = asyncio.run(
            resolve_user_memory_access(
                {"type": "user"}, _channel_config(), None, ManagedChannelContext
            )
        )
        assert access.denied_reason == "not_slack_dm"
    finally:
        _current_channel.reset(token)
