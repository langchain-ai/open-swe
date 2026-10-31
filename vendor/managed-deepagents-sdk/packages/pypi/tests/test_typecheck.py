from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _run_ty(path: Path) -> subprocess.CompletedProcess[str]:
    # Prefer the project venv's ty (has deepagents/langchain typing deps).
    venv_ty = Path(sys.executable).parent / "ty"
    ty = str(venv_ty) if venv_ty.is_file() else shutil.which("ty")
    if ty is None:
        pytest.skip("ty is not installed")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    return subprocess.run(
        [ty, "check", "--python", sys.executable, str(path)],
        check=False,
        cwd=Path.cwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _write_snippet(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "agent.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def test_define_deep_agent_accepts_typed_authoring_config(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import define_deep_agent

        agent = define_deep_agent(
            name="research",
            model="openai:gpt-5.5",
            tools=[],
            permissions=[],
        )
        no_managed_resources = define_deep_agent(
            name="no-managed-resources",
            instructions=None,
            mcp=None,
            sandbox=None,
            skills=None,
            memory=None,
        )
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


def test_package_rejects_names_it_does_not_export(tmp_path: Path) -> None:
    """Lazy loading doesn't make the package accept any name, typed Any."""
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import DefineDeepAgnet, define_deep_agent

        define_deep_agent(name=1)
        """,
    )

    result = _run_ty(path)

    assert "has no member `DefineDeepAgnet`" in result.stdout, result.stdout
    assert "Argument to function `define_deep_agent` is incorrect" in result.stdout, result.stdout


def test_factory_accepts_typed_selections_and_async_resolver(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import (
            AgentEntry,
            DeepAgentDefinition,
            GeneratedSkill,
            MemoryLayer,
            define_deep_agent,
            define_memory,
            define_mcp,
            define_sandbox,
        )
        skill: GeneratedSkill = {
            "name": "team-playbook",
            "description": "Handoffs",
            "markdown": "# Playbook",
        }
        local_skill = "skills/local-skill"
        accounts_mcp = define_mcp(
            servers={
                "accounts": {
                    "transport": "http",
                    "url": "https://example.com/mcp",
                }
            }
        )
        sandbox = define_sandbox(idle_ttl_seconds=600)

        from langgraph_sdk.runtime import ServerRuntime

        async def build_agent(
            runtime: ServerRuntime[dict[str, object] | None],
        ) -> DeepAgentDefinition:
            return define_deep_agent(
                name="support",
                instructions="",
                mcp=[accounts_mcp],
                memory=define_memory(agent=MemoryLayer()),
                sandbox=sandbox,
                skills=[local_skill, skill],
            )

        agent: AgentEntry = build_agent
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


def test_runtime_backend_public_types(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from deepagents.backends.protocol import BackendProtocol, ReadResult, WriteResult
        from managed_deepagents import ManagedDeepAgentRuntime

        async def use(runtime: ManagedDeepAgentRuntime) -> ReadResult:
            backend: BackendProtocol | None = runtime.backend
            assert backend is not None
            written: WriteResult = backend.write("/workspace/out.txt", "data")
            await backend.awrite("/workspace/out.txt", "data")
            backend.edit("/workspace/out.txt", "data", "updated")
            backend.grep("updated", "/workspace", "*.txt")
            backend.glob("*.txt", "/workspace")
            backend.ls("/workspace")
            return await backend.aread("/workspace/out.txt")
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


def test_runtime_schedules_public_types(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import (
            ManagedRuntime,
            ManagedToolRuntime,
            RuntimeSchedules,
            ScheduleRecord,
        )

        async def remind_me(prompt: str, runtime: ManagedToolRuntime) -> str:
            item: ScheduleRecord = await runtime.schedules.create(
                owner={"type": "user"}, cron="0 9 * * *", prompt=prompt
            )
            return item["id"]

        def middleware_schedules(runtime: ManagedRuntime) -> RuntimeSchedules:
            return runtime.schedules
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


def test_runtime_thread_public_types(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from typing_extensions import assert_type
        from managed_deepagents import (
            ChannelEvent, ManagedRuntime, ManagedServerRuntime, ManagedThread, ThreadParticipant,
        )

        def middleware(runtime: ManagedRuntime) -> None:
            assert_type(runtime.thread.participants, list[ThreadParticipant])
            if (origin := runtime.thread.origin) is not None:
                assert_type(origin.channel.event, ChannelEvent | None)

        def factory(runtime: ManagedServerRuntime) -> None:
            assert_type(runtime.thread, ManagedThread | None)
        """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout


def test_runtime_schedules_reject_invalid_owner(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import ManagedToolRuntime

        async def remind_me(runtime: ManagedToolRuntime) -> None:
            await runtime.schedules.create(owner={"type": "team"}, cron="0 9 * * *", prompt="x")
        """,
    )

    result = _run_ty(path)

    assert result.returncode != 0, result.stdout
    assert "invalid-argument-type" in result.stdout


def test_runtime_backend_rejects_binary_write(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import ManagedDeepAgentRuntime

        def use(runtime: ManagedDeepAgentRuntime) -> None:
            assert runtime.backend is not None
            runtime.backend.write("/workspace/out.bin", b"data")
        """,
    )

    result = _run_ty(path)

    assert result.returncode != 0, result.stdout
    assert "invalid-argument-type" in result.stdout


def test_define_deep_agent_accepts_langchain_and_deepagents_types(
    tmp_path: Path,
) -> None:
    pytest.importorskip("deepagents")
    pytest.importorskip("langchain_core")

    path = _write_snippet(
        tmp_path,
        """
        from typing import cast

        from deepagents import SubAgent
        from langchain.agents.middleware.types import AgentMiddleware
        from langchain_core.tools import BaseTool
        from managed_deepagents import define_deep_agent

        tools: list[BaseTool] = []
        middleware: list[AgentMiddleware] = []
        subagents: list[SubAgent] = [
            cast(
                SubAgent,
                {
                    "name": "researcher",
                    "description": "Research helper",
                    "system_prompt": "Research carefully.",
                    "tools": [],
                },
            )
        ]

        agent = define_deep_agent(
            name="research",
            model="openai:gpt-5.5",
            tools=tools,
            middleware=middleware,
            subagents=subagents,
        )
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "source",
    [
        'define_deep_agent(name="x", system_prompt="Use instructions.md instead")',
        'define_deep_agent(name="x", backend=object())',
        'define_deep_agent(name="x", memory=["/memories/agent/AGENTS.md"])',
        'define_deep_agent(name="x", backend=None)',
        'define_deep_agent(name="x", store=None)',
        'define_deep_agent(name="x", checkpointer=None)',
        'define_deep_agent(name="x", skills=[Path("skills/local-skill")])',
        'define_deep_agent(name="x", mcp=["accounts"])',
        'define_deep_agent(name="x", sandbox="managed")',
        'define_deep_agent(name="x", memory=["workspace"])',
        'define_deep_agent(name="x", system_prompt=None)',
        'define_deep_agent(name="x", state_schema=None)',
        'define_deep_agent("openai:gpt-5")',
        'define_deep_agent(name="x", not_a_real_key=True)',
        'define_deep_agent(model="openai:gpt-5")',
    ],
)
def test_define_deep_agent_rejects_invalid_authoring_config(
    tmp_path: Path,
    source: str,
) -> None:
    path = _write_snippet(
        tmp_path,
        f"""
        from pathlib import Path
        from managed_deepagents import define_deep_agent

        {source}
        """,
    )

    result = _run_ty(path)

    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize(
    "scope",
    [
        '{"type": "user"}',
        '[{"type": "agent"}]',
        '[{"type": "user"}]',
        '[{"type": "user", "id": "usr_1"}]',
        '[{"type": "agent"}, {"type": "user"}]',
        "[]",
        "",
    ],
)
def test_define_memory_accepts_the_launch_contract(tmp_path: Path, scope: str) -> None:
    argument = f"scope={scope}" if scope else ""
    path = _write_snippet(
        tmp_path,
        f"""
        from managed_deepagents import define_memory

        memory = define_memory({argument})
        """,
    )

    result = _run_ty(path)

    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "source",
    [
        # Per-layer options are reserved, not authorable yet.
        'define_memory(scope=[{"type": "agent", "access": "rw"}])',
        # The retired string shorthand is not a layer.
        'define_memory(scope=["agent"])',
        # An id belongs to the user layer; the deployment owns the agent one.
        'define_memory(scope=[{"type": "agent", "id": "usr_1"}])',
        # `scope` is keyword-only, like every other authoring primitive.
        'define_memory([{"type": "agent"}])',
    ],
)
def test_define_memory_rejects_reserved_layer_forms(tmp_path: Path, source: str) -> None:
    """Reserved *values* fail the type check.

    Reserved *keywords* (``scope``, ``access``, ``mount``, …) are legal to
    a type checker because the signature keeps ``**kwargs`` — the same trade
    ``define_identity`` makes to answer a hopeful guess with an explanation
    instead of "unexpected keyword argument". Those are covered at runtime in
    ``test_define_memory.py``.
    """
    path = _write_snippet(
        tmp_path,
        f"""
        from managed_deepagents import define_memory

        {source}
        """,
    )

    result = _run_ty(path)

    assert result.returncode != 0, result.stdout


def test_memory_allow_accepts_managed_and_custom_context_types(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from dataclasses import dataclass
        from managed_deepagents import (
            ManagedChannelContext, ManagedRunContext, define_memory,
        )

        context: ManagedRunContext = {"remember": True}

        def allow(ctx: ManagedRunContext) -> bool:
            return (
                isinstance(ctx, ManagedChannelContext)
                and ctx.channel["provider"] == "slack"
                and ctx.channel["event"]["type"] == "message"
            )

        @dataclass
        class CustomContext:
            remember: bool = False

        async def custom_allow(ctx: CustomContext) -> bool:
            return ctx.remember

        define_memory(scope={"type": "user", "allow": allow})
        define_memory(scope={"type": "user", "allow": custom_allow})
    """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "source",
    [
        'define_memory(scope={"type": "agent", "allow": lambda context: "yes"})',
        'define_memory(scope={"type": "user", "allow": lambda context: "yes"})',
        'define_memory(scope={"type": "user", "allow": True})',
        "def allow(value: ManagedChannelContext) -> bool: return value.server_info is not None",
        "def allow(ctx: ManagedChannelContext) -> bool: return ctx.channel.provider == 'slack'",
    ],
)
def test_memory_context_rejects_invalid_types(tmp_path: Path, source: str) -> None:
    path = _write_snippet(
        tmp_path,
        f"""
        from managed_deepagents import ManagedChannelContext, ManagedRunContext, define_memory
        {source}
    """,
    )
    result = _run_ty(path)
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("invalid", [False, True])
def test_named_memory_layer_context_types(tmp_path: Path, invalid: bool) -> None:
    result_type = "str" if invalid else "bool"
    result = '"yes"' if invalid else 'context["remember"]'
    path = _write_snippet(
        tmp_path,
        f"""
        from typing import TypedDict
        from managed_deepagents import MemoryLayer, define_memory

        class Context(TypedDict):
            remember: bool

        async def allow(context: Context) -> {result_type}:
            return {result}

        memory = define_memory(agent=MemoryLayer(), user=MemoryLayer(allow=allow))
    """,
    )
    checked = _run_ty(path)
    if invalid:
        assert checked.returncode != 0, checked.stdout
        assert "invalid-argument-type" in checked.stdout
    else:
        assert checked.returncode == 0, checked.stdout


def test_http_channel_accepts_minimal_spec(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import (
            channels, HttpChannelRequest, HttpChannelParseResult,
            HttpChannelPostInput, HttpPostedMessage,
        )

        async def post(message: HttpChannelPostInput) -> HttpPostedMessage:
            if message["type"] == "content":
                content = message["content"]
            else:
                native = message["native"]
            return {"id": "reply", "url": "https://example.com/reply"}

        def parse(request: HttpChannelRequest) -> HttpChannelParseResult:
            return {"type": "message", "message": {
                "user_id": "caller", "thread_id": "11111111-1111-4111-8111-111111111111",
                "content": "hello", "target": {"conversation": "provider-id"},
            }, "raw_event": {"event": ["message", None, 1, True]}}

        async def post_messages() -> None:
            await post({"type": "content", "content": "hello", "target": "one"})
            await post({"type": "native", "native": {"blocks": []}, "target": "one"})
        channel = channels.http(provider="orders", verify=lambda request: True,
            parse=parse, post=post)
    """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "expression",
    [
        'post({"target": "one"})',
        'post({"content": "hello", "target": "one"})',
        'post({"type": "native", "content": "hello", "target": "one"})',
        'runtime.channel.post({"content": "hello"})',
        'runtime.channel.post({"type": "native", "content": "hello"})',
        'post({"type": "content", "content": "hello", "native": {}, "target": "one"})',
        'post({"type": "content", "content": "hello"})',
        'runtime.channel.post({"type": "content", "content": "hello", "target": "other"})',
        'runtime.channel.post({"type": "content", "content": "hello", "native": {}})',
        "runtime.channel.post({})",
    ],
)
def test_http_channel_rejects_invalid_posts(tmp_path: Path, expression: str) -> None:
    path = _write_snippet(
        tmp_path,
        f"""
        from collections.abc import Awaitable, Callable
        from managed_deepagents import (
            HttpChannelPostInput, HttpPostedMessage, ManagedDeepAgentRuntime,
        )
        async def use(post: Callable[[HttpChannelPostInput], Awaitable[HttpPostedMessage]],
                      runtime: ManagedDeepAgentRuntime) -> None:
            assert runtime.channel is not None
            assert runtime.channel.post is not None
            await {expression}
    """,
    )
    checked = _run_ty(path)
    assert checked.returncode != 0, checked.stdout
    assert "invalid-argument-type" in checked.stdout or "no-matching-overload" in checked.stdout


def test_mcp_tool_loading_preserves_types(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from collections.abc import Sequence
        from langchain_core.tools import BaseTool
        from typing_extensions import assert_type
        from managed_deepagents import McpServersDefinition
        from managed_deepagents._connectors.mcp.load import (
            load_managed_mcp_tools, load_eager_mcp_tools,
        )
        from managed_deepagents._connectors.tool_selection import select_tools
        from managed_deepagents._memory.async_util import await_if_needed

        def check(definition: McpServersDefinition) -> None:
            tools = load_managed_mcp_tools(definition)
            assert_type(tools, list[BaseTool])
            assert_type(definition.tools(), Sequence[BaseTool])
            assert_type(await_if_needed(load_eager_mcp_tools(definition)), list[BaseTool])
            assert_type(await_if_needed(tools), list[BaseTool])
            assert_type(select_tools("docs", {}, tools), list[BaseTool])
        """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout
    path.write_text(
        path.read_text().replace("assert_type(tools, list[BaseTool])", "assert_type(tools, str)")
    )
    broken = _run_ty(path)
    assert broken.returncode != 0, broken.stdout
    assert "type-assertion-failure" in broken.stdout


def test_mcp_checked_types_and_examples(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import (
            connections, define_mcp, HttpMcpServerConfig, SseMcpServerConfig,
            RemoteMcpServerConfig, McpServersConfig, McpServersDefinition,
        )
        http: HttpMcpServerConfig = {
            "transport": "http", "url": "https://x/mcp", "timeout": 30}
        sse: SseMcpServerConfig = {
            "transport": "sse", "url": "https://x/sse", "sse_read_timeout": 300}
        server: RemoteMcpServerConfig = http
        config: McpServersConfig = {"servers": {"http": http, "legacy": sse}}
        mcp: McpServersDefinition = define_mcp(**config)
        mcp = define_mcp(servers={"notion": {"transport": "http", "url": "https://x/mcp",
            "connection": connections.get("notion", {"type": "user"}), "include_tools": [],
            "exclude_tools": [], "timeout": 10}}, throw_on_load_error=False,
            on_connection_error="throw")
    """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout
    # Deliberately break the return-type assertion: prove the public factory is checked.
    path.write_text(path.read_text().replace("mcp: McpServersDefinition", "mcp: str"))
    broken = _run_ty(path)
    assert broken.returncode != 0, broken.stdout
    assert "invalid-assignment" in broken.stdout


@pytest.mark.parametrize(
    "source",
    [
        "define_mcp()",
        "define_mcp(mcp_servers={})",
        'define_mcp(servers={"x": {"transport": "http"}})',
        'define_mcp(servers={"x": {"url": "https://x/mcp"}})',
        'define_mcp(servers={"x": {"transport": "stdio", "url": "https://x/mcp"}})',
        (
            'define_mcp(servers={"x": {"transport": "http", '
            '"url": "https://x/mcp", "sse_read_timeout": 5}})'
        ),
        (
            'define_mcp(servers={"x": {"transport": "http", '
            '"url": "https://x/mcp", "default_tool_timeout": 5}})'
        ),
        'define_mcp(servers={"x": {"transport": "http", "url": "https://x/mcp", "auth": "oauth"}})',
        "define_mcp(servers={}, prefix_tool_name_with_server_name=True)",
        'define_mcp(servers={}, on_connection_error="tool_error")',
        'HttpMcpServerConfig(url="https://x/mcp")',
    ],
)
def test_mcp_rejects_invalid_types(tmp_path: Path, source: str) -> None:
    path = _write_snippet(
        tmp_path, f"from managed_deepagents import define_mcp, HttpMcpServerConfig\n{source}"
    )
    result = _run_ty(path)
    assert result.returncode != 0, result.stdout


def test_plain_factories_accept_native_server_runtime(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from langgraph_sdk.runtime import ServerRuntime
        from managed_deepagents import (
            AgentEntry, DeepAgentDefinition, define_deep_agent
        )

        def agent(runtime: ServerRuntime[dict[str, object] | None]) -> DeepAgentDefinition:
            runtime.ensure_user().identity
            runtime.store.get(("factory",), "key")
            runtime.access_context
            execution = runtime.execution_runtime
            if execution is not None:
                execution.context
            return define_deep_agent(name="plain")

        async def async_agent(
            runtime: ServerRuntime[dict[str, object] | None],
        ) -> DeepAgentDefinition:
            return agent(runtime)

        entry: AgentEntry = agent
        async_entry: AgentEntry = async_agent
        """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout


def test_managed_factory_context_is_application_data_or_absent(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from typing import TypedDict, assert_type
        from managed_deepagents import (
            AgentEntry, DeepAgentDefinition, ManagedServerRuntime, define_deep_agent
        )

        class AppContext(TypedDict):
            account: str

        def agent(runtime: ManagedServerRuntime) -> DeepAgentDefinition:
            execution = runtime.execution_runtime
            if execution is not None:
                assert_type(execution.context, dict[str, object] | None)
            return define_deep_agent(name="support")

        entry: AgentEntry = agent

        def typed_agent(runtime: ManagedServerRuntime[AppContext]) -> DeepAgentDefinition:
            execution = runtime.execution_runtime
            if execution is not None:
                assert_type(execution.context, AppContext | None)
                if execution.context is not None:
                    assert_type(execution.context["account"], str)
            return define_deep_agent(name="typed-support")
        """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout


def test_factory_rejects_old_context_signature(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from managed_deepagents import (
            AgentEntry, DeepAgentDefinition, ManagedRunContext, define_deep_agent
        )

        def agent(context: ManagedRunContext) -> DeepAgentDefinition:
            return define_deep_agent(name="old")

        entry: AgentEntry = agent
        """,
    )
    result = _run_ty(path)
    assert result.returncode != 0, result.stdout
    assert "invalid-assignment" in result.stdout, result.stdout


def test_runtime_adaptation_preserves_native_context_and_state(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from dataclasses import dataclass
        from typing import TypedDict
        from typing_extensions import assert_type
        from langchain.tools import ToolRuntime
        from langgraph.runtime import Runtime
        from managed_deepagents import ManagedRuntime, ManagedToolRuntime
        from managed_deepagents._managed_tools import adapt_managed_runtime

        @dataclass
        class Context:
            team: str
        class State(TypedDict):
            count: int

        def middleware(runtime: Runtime[Context]) -> None:
            managed = adapt_managed_runtime(runtime, {})
            assert_type(managed, ManagedRuntime[Context])
            assert_type(managed.context.team, str)

        def tool(runtime: ToolRuntime[Context, State]) -> None:
            managed = adapt_managed_runtime(runtime, {})
            assert_type(managed, ManagedToolRuntime[Context, State])
            assert_type(managed.context.team, str)
            assert_type(managed.state["count"], int)
        """,
    )
    checked = _run_ty(path)
    assert checked.returncode == 0, checked.stdout
    path.write_text(
        path.read_text().replace(
            "assert_type(managed.context.team, str)", "assert_type(managed.context.team, int)"
        )
    )
    broken = _run_ty(path)
    assert broken.returncode != 0, broken.stdout
    assert "type-assertion-failure" in broken.stdout


def test_managed_runtime_preserves_native_context_state_and_fields(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from collections.abc import Callable
        from dataclasses import dataclass
        from typing import TypedDict
        from typing_extensions import assert_type
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime, ServerInfo
        from langgraph.store.base import BaseStore
        from langgraph.runtime import ExecutionInfo
        from langgraph.types import StreamWriter
        from langgraph_sdk.auth.types import BaseUser
        from managed_deepagents import (
            ManagedRuntime, ManagedToolRuntime, ManagedDeepAgentRuntime, ManagedServerInfo,
        )

        @dataclass
        class Context:
            team: str
        class State(TypedDict):
            count: int

        def middleware(runtime: ManagedRuntime[Context]) -> Runtime[Context]:
            assert_type(runtime.context, Context)
            assert_type(runtime.context.team, str)
            assert_type(runtime.store, BaseStore | None)
            assert_type(runtime.stream_writer, StreamWriter)
            assert_type(runtime.execution_info, ExecutionInfo | None)
            assert_type(runtime.heartbeat, Callable[[], None])
            return runtime

        def tool(runtime: ManagedToolRuntime[Context, State]) -> int:
            assert_type(runtime.context, Context)
            assert_type(runtime.state, State)
            assert_type(runtime.config, RunnableConfig)
            assert_type(runtime.store, BaseStore | None)
            legacy: ManagedDeepAgentRuntime[Context, State] = runtime
            assert_type(legacy.context, Context)
            if runtime.server_info is not None:
                native: ServerInfo = runtime.server_info
                assert_type(runtime.server_info.assistant_id, str)
                assert_type(runtime.server_info.user, BaseUser | None)
            return runtime.state["count"]
        """,
    )
    result = _run_ty(path)
    assert result.returncode == 0, result.stdout
    path.write_text(
        path.read_text().replace(
            "assert_type(runtime.context, Context)", "assert_type(runtime.context, str)"
        )
    )
    broken = _run_ty(path)
    assert broken.returncode != 0, broken.stdout
    assert "type-assertion-failure" in broken.stdout


@pytest.mark.parametrize(
    "expression",
    [
        "runtime.context.team = 42",
        'runtime.state["count"] = "wrong"',
        'runtime.config["tags"] = 42',
        'runtime.store = "store"',
        "runtime.server_info.user = {}",
    ],
)
def test_managed_tool_runtime_rejects_incorrect_native_types(
    tmp_path: Path,
    expression: str,
) -> None:
    path = _write_snippet(
        tmp_path,
        f"""
        from dataclasses import dataclass
        from typing import TypedDict
        from managed_deepagents import ManagedToolRuntime
        @dataclass
        class Context:
            team: str
        class State(TypedDict):
            count: int
        def use(runtime: ManagedToolRuntime[Context, State]) -> None:
            assert runtime.server_info is not None
            {expression}
    """,
    )
    checked = _run_ty(path)
    assert checked.returncode != 0, checked.stdout


def test_native_middleware_override_retains_context_after_managed_guard(tmp_path: Path) -> None:
    path = _write_snippet(
        tmp_path,
        """
        from dataclasses import dataclass
        from typing_extensions import assert_type
        from langchain.agents.middleware import AgentMiddleware, AgentState, before_model
        from langgraph.runtime import Runtime
        from managed_deepagents import ManagedRuntime
        @dataclass
        class Context:
            team: str
        class State(AgentState):
            count: int
        class Audit(AgentMiddleware[State, Context]):
            def before_model(self, state: State, runtime: Runtime[Context]) -> None:
                if isinstance(runtime, ManagedRuntime):
                    assert_type(runtime.context, Context)
                    assert_type(runtime.context.team, str)
                    if runtime.server_info and runtime.server_info.principal:
                        assert_type(runtime.server_info.principal.id, str)
        @before_model
        def free_hook(state: State, runtime: Runtime[Context]) -> None:
            if isinstance(runtime, ManagedRuntime):
                assert_type(runtime.context, Context)
    """,
    )
    checked = _run_ty(path)
    assert checked.returncode == 0, checked.stdout
    path.write_text(
        path.read_text().replace(
            "assert_type(runtime.context, Context)", "assert_type(runtime.context, str)"
        )
    )
    broken = _run_ty(path)
    assert broken.returncode != 0, broken.stdout
    assert "type-assertion-failure" in broken.stdout
