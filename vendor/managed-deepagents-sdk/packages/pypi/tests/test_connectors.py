"""MCP authoring, native-client loading, and managed runtime integration tests."""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import urllib.error
import warnings
import weakref
from collections.abc import Coroutine, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT

from managed_deepagents import _credential_gate as gate
from managed_deepagents import connections, define_mcp
from managed_deepagents import runtime as runtime_mod
from managed_deepagents._agent_auth import AgentAuthContext
from managed_deepagents._connector import is_connector
from managed_deepagents._connectors import McpServersDefinition
from managed_deepagents._connectors.mcp import load as mcp_load
from managed_deepagents._connectors.mcp.langsmith_url import (
    is_langsmith_managed_tools_path,
    is_langsmith_managed_tools_url,
)
from managed_deepagents._connectors.mcp.load import (
    ManagedAuth,
    load_caller_mcp_tools,
    load_eager_mcp_tools,
)
from managed_deepagents._credential_gate import _Caller
from managed_deepagents._managed_tools import wrap_tools_with_managed_runtime
from managed_deepagents.define_deep_agent import DeepAgentDefinition
from tests.mcp_server import Server, serve


def _langsmith_mcp_url(server_id: str, origin: str = "https://api.smith.langchain.com") -> str:
    return f"{origin}/v1/managed-tools/servers/{server_id}/mcp"


_KIT_URL = (
    "https://api.smith.langchain.com/v1/managed-tools/kits/01a0f00e-4fe4-7c06-85d7-caa9359e9dff/mcp"
)
_KIT_CONTEXT = AgentAuthContext(
    base_url="https://api.smith.langchain.com",
    principal_id="01999999-9999-7999-8999-999999999999",
    api_key="workspace-key",
)


class TestLangSmithUrl:
    @pytest.mark.parametrize("resource", ["kits", "gateways"])
    def test_treats_default_https_port_and_hostname_case_as_the_same_origin(
        self, resource: str
    ) -> None:
        url = _langsmith_mcp_url("exa")
        assert is_langsmith_managed_tools_url(url, "https://api.smith.langchain.com")
        assert is_langsmith_managed_tools_url(
            "https://api.smith.langchain.com:443/v1/managed-tools/servers/exa/mcp",
            "https://api.smith.langchain.com",
        )
        assert is_langsmith_managed_tools_url(url, "https://API.SMITH.LANGCHAIN.COM:443")
        assert not is_langsmith_managed_tools_url(
            "https://evil.example/v1/managed-tools/servers/exa/mcp",
            "https://api.smith.langchain.com",
        )
        gateway = (
            f"https://api.smith.langchain.com/v1/managed-tools/{resource}/"
            "01a0f00e-4fe4-7c06-85d7-caa9359e9dff/mcp"
        )
        assert is_langsmith_managed_tools_url(gateway, "https://api.smith.langchain.com")
        assert is_langsmith_managed_tools_path(f"{gateway}?mode=search")
        assert is_langsmith_managed_tools_url(
            f"{gateway}?mode=search", "https://api.smith.langchain.com"
        )
        assert not is_langsmith_managed_tools_url(
            gateway.replace("https://api.smith.langchain.com", "https://evil.example")
            + "?mode=search",
            "https://api.smith.langchain.com",
        )


class TestDefineMcpServers:
    def test_returns_a_tagged_definition_with_defaults(self) -> None:
        mcp = define_mcp(
            servers={
                "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
            },
        )
        assert isinstance(mcp, McpServersDefinition)
        assert mcp.kind == "mcp_servers"
        # It is a branded managed connector with a tools hook.
        assert is_connector(mcp)
        assert callable(mcp.tools)
        assert mcp.config["throw_on_load_error"] is True
        assert mcp.config["on_connection_error"] == "throw"
        assert set(mcp.config) == {"servers", "throw_on_load_error", "on_connection_error"}

    def test_reports_unique_user_connection_slugs_for_deploy_validation(self) -> None:
        mcp = define_mcp(
            servers={
                "notion": {
                    "transport": "http",
                    "url": "https://mcp.notion.com/mcp",
                    "connection": connections.get("notion", {"type": "user"}),
                },
                "also_notion": {
                    "transport": "http",
                    "url": "https://mcp.notion.com/other",
                    "connection": connections.get("notion", {"type": "user"}),
                },
                "tavily": {
                    "transport": "http",
                    "url": "https://mcp.tavily.com/mcp",
                    "connection": connections.get("tavily", {"type": "agent"}),
                },
            },
        )

        requirements = mcp.requirements("mcp")
        assert requirements["userConnections"] == ["notion"]
        assert requirements["agentConnections"] == ["tavily"]
        assert requirements["userCredentials"] == ["notion"]

    @pytest.mark.parametrize(
        ("url", "needs_developer_principal"),
        [
            (_langsmith_mcp_url("parallel"), True),
            (_langsmith_mcp_url("parallel", "https://dev.api.smith.langchain.com"), True),
            ("https://docs.example.com/mcp", False),
        ],
    )
    def test_reports_developer_principal_requirement(
        self, url: str, needs_developer_principal: bool
    ) -> None:
        mcp = define_mcp(
            servers={
                "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
                "tools": {"transport": "http", "url": url},
            },
        )

        requirements = mcp.requirements("mcp")

        assert requirements.get("needsDeveloperPrincipal", False) is needs_developer_principal
        assert requirements["userConnections"] == []

    def test_does_not_warn_when_servers_is_used(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            define_mcp(
                servers={
                    "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
                },
            )
        assert not any("use servers instead" in str(warning.message) for warning in caught)

    def test_accepts_sse_servers(self) -> None:
        mcp = define_mcp(
            servers={
                "legacy": {"transport": "sse", "url": "https://legacy.example.com/mcp"},
            },
        )
        assert mcp.config["servers"]["legacy"]["transport"] == "sse"

    def test_accepts_langsmith_managed_tools_url(self) -> None:
        mcp = define_mcp(
            servers={
                "notion": {
                    "transport": "http",
                    "url": _langsmith_mcp_url("68a8938f-f90d-40a9-a1db-0c6263696e70"),
                    "include_tools": ["search"],
                },
            },
        )
        assert mcp.config["servers"]["notion"] == {
            "transport": "http",
            "url": _langsmith_mcp_url("68a8938f-f90d-40a9-a1db-0c6263696e70"),
            "include_tools": ["search"],
        }

    def test_rejects_langsmith_url_with_connection_or_headers(self) -> None:
        with pytest.raises(ValueError, match="cannot declare headers or connection"):
            define_mcp(
                servers={
                    "notion": {
                        "transport": "http",
                        "url": _langsmith_mcp_url("notion"),
                        "connection": connections.get("notion", {"type": "user"}),
                    },
                },
            )
        with pytest.raises(ValueError, match="cannot declare headers or connection"):
            define_mcp(
                servers={
                    "notion": {
                        "transport": "http",
                        "url": _langsmith_mcp_url("notion"),
                        "headers": {"Authorization": "Bearer stolen"},
                    },
                },
            )
        with pytest.raises(ValueError, match="cannot declare headers or connection"):
            define_mcp(
                servers={
                    "notion": {
                        "transport": "http",
                        "url": _langsmith_mcp_url("notion"),
                        "headers": {},
                    },
                },
            )

    def test_rejects_langsmith_url_that_is_not_http(self) -> None:
        with pytest.raises(ValueError, match='must declare transport "http"'):
            define_mcp(
                servers={
                    "notion": {
                        "transport": "sse",
                        "url": _langsmith_mcp_url("notion"),
                    },
                },
            )

    def test_preserves_per_server_tool_selection(self) -> None:
        mcp = define_mcp(
            servers={
                "docs": {
                    "transport": "http",
                    "url": "https://docs.example.com/mcp",
                    "connection": connections.get("notion", {"type": "user"}),
                    "include_tools": ["search"],
                    "exclude_tools": ["debug"],
                    "timeout": 30,
                },
            },
        )
        docs = mcp.config["servers"]["docs"]
        assert docs["connection"].slug == "notion"
        assert docs["connection"].owner == {"type": "user"}
        assert docs["include_tools"] == ["search"]
        assert docs["exclude_tools"] == ["debug"]
        assert docs["timeout"] == 30

    def test_rejects_non_positive_timeout(self) -> None:
        for timeout in (0, -1.0, float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError, match="timeout"):
                define_mcp(
                    servers={
                        "docs": {
                            "transport": "http",
                            "url": "https://docs.example.com/mcp",
                            "timeout": timeout,
                        },
                    },
                )

    def test_rejects_empty_servers(self) -> None:
        with pytest.raises(ValueError, match="requires at least one entry"):
            define_mcp(servers={})

    def test_rejects_non_boolean_options(self) -> None:
        # Pydantic lax booleans would otherwise coerce 0 to False.
        servers = {"docs": {"transport": "http", "url": "https://docs.example.com/mcp"}}
        with pytest.raises(ValueError, match="throw_on_load_error"):
            define_mcp(servers=servers, throw_on_load_error=0)

    def test_rejects_stdio_by_transport(self) -> None:
        with pytest.raises(ValueError, match="stdio transport, which is not"):
            define_mcp(
                servers={"math": {"transport": "stdio", "command": "python"}},
            )

    def test_rejects_stdio_by_command(self) -> None:
        with pytest.raises(ValueError, match="stdio transport, which is not"):
            define_mcp(servers={"math": {"command": "python"}})

    def test_rejects_unknown_transport(self) -> None:
        with pytest.raises(ValueError, match="must declare a remote transport"):
            define_mcp(servers={"docs": {"transport": "grpc", "url": "x"}})

    def test_rejects_missing_url(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty URL"):
            define_mcp(servers={"docs": {"transport": "http"}})

    @pytest.mark.parametrize("mode", ["ignore", "tool_error"])
    def test_rejects_unsupported_connection_error_mode(self, mode: str) -> None:
        with pytest.raises(ValueError, match='only supports on_connection_error="throw"'):
            define_mcp(
                on_connection_error=mode,
                servers={
                    "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
                },
            )

    def test_rejects_invalid_tool_selector_shapes(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty tool name"):
            define_mcp(
                servers={
                    "docs": {
                        "transport": "http",
                        "url": "https://docs.example.com/mcp",
                        "include_tools": ["search", ""],
                    },
                },
            )

    def test_rejects_invalid_connection_reference(self) -> None:
        with pytest.raises(ValueError, match="ConnectionReference"):
            define_mcp(
                servers={
                    "docs": {
                        "transport": "http",
                        "url": "https://docs.example.com/mcp",
                        "connection": "user",
                    },
                },
            )

    def test_rejects_overlapping_include_and_exclude_tool_selectors(self) -> None:
        with pytest.raises(ValueError, match="both `include_tools` and `exclude_tools`"):
            define_mcp(
                servers={
                    "docs": {
                        "transport": "http",
                        "url": "https://docs.example.com/mcp",
                        "include_tools": ["search"],
                        "exclude_tools": ["search"],
                    },
                },
            )

    @pytest.mark.parametrize(
        "field", ["mcp_servers", "prefix_tool_name_with_server_name", "use_standard_content_blocks"]
    )
    def test_removed_global_fields(self, field: str) -> None:
        with pytest.raises(ValueError, match=f"{field} was removed in 0.9"):
            define_mcp(
                **{"servers": {"docs": {"transport": "http", "url": "https://x/mcp"}}, field: True}
            )

    @pytest.mark.parametrize(
        "field", ["credential", "default_tool_timeout", "automatic_sse_fallback", "reconnect"]
    )
    def test_removed_server_fields(self, field: str) -> None:
        with pytest.raises(ValueError, match=f"{field} was removed in 0.9"):
            define_mcp(servers={"docs": {"transport": "http", "url": "https://x/mcp", field: True}})

    @pytest.mark.parametrize(
        "field", ["auth", "session_kwargs", "httpx_client_factory", "verify", "terminate_on_close"]
    )
    def test_unknown_native_policy_fields(self, field: str) -> None:
        with pytest.raises(ValueError, match="Extra inputs"):
            define_mcp(servers={"docs": {"transport": "http", "url": "https://x/mcp", field: True}})

    @pytest.mark.parametrize("value", [True, False, 0, -1, float("nan"), float("inf"), "2", None])
    @pytest.mark.parametrize("field", ["timeout", "sse_read_timeout"])
    def test_timeout_validation(self, field: str, value: object) -> None:
        with pytest.raises(ValueError, match="positive finite seconds"):
            define_mcp(servers={"docs": {"transport": "sse", "url": "https://x/sse", field: value}})

    @pytest.mark.parametrize(
        "server",
        [
            {"url": "https://x/mcp"},
            {"transport": "http", "url": "file:///app/server.py"},
            {"transport": "http", "url": "https://user:secret@x/mcp"},
            {"transport": "http", "url": "https://x/mcp", "sse_read_timeout": 30},
            {"transport": "http", "url": "https://x/mcp", "include_tools": ["a", "a"]},
        ],
    )
    def test_invalid_declarations(self, server: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="invalid configuration"):
            define_mcp(servers={"docs": server})

    def test_rejects_connection_with_authorization_header(self) -> None:
        for headers in ({"Authorization": "synthetic"}, {"authorization": "synthetic"}):
            with pytest.raises(ValueError, match=r"connection.*Authorization"):
                define_mcp(
                    servers={
                        "docs": {
                            "transport": "http",
                            "url": "https://x/mcp",
                            "headers": headers,
                            "connection": connections.get("notion", {"type": "user"}),
                        }
                    }
                )


@pytest.fixture
def fake_native_loading(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Keep the existing preparation tests independent of native transport I/O."""

    async def load(servers: Mapping[str, object], **_kwargs: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=f"{name}_search") for name in servers]

    loader = AsyncMock(side_effect=load)
    monkeypatch.setattr(mcp_load, "load_tools", loader)
    return loader


class TestManagedAuth:
    def test_retries_401_with_a_newer_token(self) -> None:
        async def run() -> None:
            token = AsyncMock(side_effect=["synthetic-old", "synthetic-new"])
            requests: list[tuple[str, bytes]] = []

            async def handle(request: httpx2.Request) -> httpx2.Response:
                requests.append((request.headers["Authorization"], await request.aread()))
                return httpx2.Response(
                    401 if len(requests) == 1 else 200, stream=httpx2.ByteStream(b"response")
                )

            auth = ManagedAuth("https://example.com/mcp", token=token)
            async with httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), auth=auth
            ) as client:
                response = await client.post("https://example.com/mcp", content=b"request")

            assert response.status_code == 200
            assert requests == [
                ("Bearer synthetic-old", b"request"),
                ("Bearer synthetic-new", b"request"),
            ]
            assert token.await_count == 2

        scenario(run())

    @pytest.mark.parametrize("refresh", ["unchanged", "missing", "rejected", "error"])
    def test_rejected_credentials_fail_without_further_retries(self, refresh: str) -> None:
        async def run() -> None:
            failure = RuntimeError("synthetic store failure")
            next_token = {
                "unchanged": "synthetic-old",
                "missing": "",
                "rejected": "synthetic-new",
                "error": failure,
            }[refresh]
            token = AsyncMock(side_effect=["synthetic-old", next_token])
            responses: list[httpx2.Response] = []

            async def handle(request: httpx2.Request) -> httpx2.Response:
                response = httpx2.Response(401, request=request)
                responses.append(response)
                return response

            auth = ManagedAuth("https://example.com/mcp", token=token, connection_slug="notion")
            async with httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), auth=auth
            ) as client:
                with pytest.raises(
                    RuntimeError, match=r'MCP connection "notion".*reconnect'
                ) as exc:
                    await client.post("https://example.com/mcp")

            assert len(responses) == (2 if refresh == "rejected" else 1)
            assert all(response.is_closed for response in responses)
            assert token.await_count == 2
            assert exc.value.__cause__ is (failure if refresh == "error" else None)

        scenario(run())

    @pytest.mark.parametrize("status", [200, 403])
    def test_other_responses_do_not_refresh_tokens(self, status: int) -> None:
        async def run() -> None:
            token = AsyncMock(return_value="synthetic-token")
            auth = ManagedAuth("https://example.com/mcp", token=token)
            async with httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda request: httpx2.Response(status)), auth=auth
            ) as client:
                response = await client.post("https://example.com/mcp")
            assert response.status_code == status
            assert token.await_count == 1

        scenario(run())


class TestLoadManagedMcpTools:
    def test_prefixes_tool_names_per_server_by_default(self) -> None:
        async def run() -> None:
            async with serve() as server:
                mcp = define_mcp(
                    servers={
                        "docs": {"transport": "http", "url": server.url, "include_tools": ["echo"]},
                        "wiki": {"transport": "http", "url": server.url, "include_tools": ["echo"]},
                    },
                )
                tools = await load_eager_mcp_tools(mcp)
                names = sorted(tool.name for tool in tools)
                assert names == ["docs_echo", "wiki_echo"]

        scenario(run())

    def test_cache_does_not_retain_dynamic_declarations(
        self, fake_native_loading: AsyncMock
    ) -> None:
        mcp = define_mcp(
            servers={"docs": {"transport": "http", "url": "https://x/mcp"}},
        )
        declaration = weakref.ref(mcp)
        mcp_load.load_managed_mcp_tools(mcp)

        del mcp
        gc.collect()

        assert declaration() is None

    def test_loads_connected_langsmith_server_with_caller_headers(
        self, fake_native_loading: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server_id = "68a8938f-f90d-40a9-a1db-0c6263696e70"
        monkeypatch.setattr(
            mcp_load,
            "request_agent_auth_json",
            lambda *_args, **_kwargs: (
                200,
                {"jsonrpc": "2.0", "id": "mda-auth-probe", "result": {}},
            ),
        )
        mcp = define_mcp(
            servers={
                "notion": {
                    "transport": "http",
                    "url": _langsmith_mcp_url(server_id, "https://dev.api.smith.langchain.com"),
                    "include_tools": ["search"],
                    "timeout": 120,
                }
            },
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.requires_identity is False
        loaded = consumer.load(
            {},
            AgentAuthContext(
                base_url="https://dev.api.smith.langchain.com",
                principal_id="01999999-9999-7999-8999-999999999999",
                api_key="workspace-key",
                workspace_id="01888888-8888-7888-8888-888888888888",
            ),
        )

        assert [tool.name for tool in loaded] == ["notion_search"]

        assert fake_native_loading.await_count == 1
        assert fake_native_loading.await_args.args[0] == {
            "notion": {
                "transport": "http",
                "url": _langsmith_mcp_url(server_id, "https://dev.api.smith.langchain.com"),
                "include_tools": ["search"],
                "timeout": 120,
            },
        }
        auth = fake_native_loading.await_args.kwargs["auth"]["notion"]

        async def headers() -> dict[str, str]:
            request = httpx2.Request(
                "POST", _langsmith_mcp_url(server_id, "https://dev.api.smith.langchain.com")
            )
            flow = auth.async_auth_flow(request)
            try:
                return dict((await anext(flow)).headers)
            finally:
                await flow.aclose()

        actual = asyncio.run(headers())
        expected = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-langsmith-product": "mda",
            "x-api-key": "workspace-key",
            "x-tenant-id": "01888888-8888-7888-8888-888888888888",
            "x-langsmith-identity": "01999999-9999-7999-8999-999999999999",
        }
        assert {name: actual[name] for name in expected} == expected

    def test_probes_and_loads_api_key_server_without_a_credential(
        self, fake_native_loading: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests: list[str] = []

        def request(
            method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            requests.append(method)
            return 200, {"jsonrpc": "2.0", "id": "mda-auth-probe", "result": {}}

        monkeypatch.setattr(
            mcp_load,
            "request_agent_auth_json",
            request,
        )
        mcp = define_mcp(servers={"exa": {"transport": "http", "url": _langsmith_mcp_url("exa")}})
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        loaded = consumer.load(
            {},
            AgentAuthContext(
                base_url="https://api.smith.langchain.com",
                principal_id="01999999-9999-7999-8999-999999999999",
                api_key="workspace-key",
            ),
        )

        assert [tool.name for tool in loaded] == ["exa_search"]
        assert requests == ["POST"]

    @pytest.mark.parametrize("error", [urllib.error.URLError("offline"), TimeoutError("timed out")])
    @pytest.mark.parametrize("throw_on_load_error", [True, False])
    def test_langsmith_probe_respects_load_error_policy(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        error: OSError,
        throw_on_load_error: bool,
    ) -> None:
        def request(_method: str, url: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
            if "/exa/" in url:
                raise error
            return 200, {}

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={
                "exa": {"transport": "http", "url": _langsmith_mcp_url("exa")},
                "notion": {"transport": "http", "url": _langsmith_mcp_url("notion")},
            },
            throw_on_load_error=throw_on_load_error,
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        context = AgentAuthContext(
            base_url="https://api.smith.langchain.com",
            principal_id="01999999-9999-7999-8999-999999999999",
            api_key="workspace-key",
        )
        if throw_on_load_error:
            assert consumer.collect_pending_credentials is not None
            with pytest.raises(type(error)) as caught:
                consumer.collect_pending_credentials(context)
            assert caught.value is error
            with pytest.raises(type(error)) as caught:
                consumer.load({}, context)
            # asyncio.to_thread may recreate TimeoutError during exception transfer.
            assert str(caught.value) == str(error)
            fake_native_loading.assert_not_awaited()
        else:
            assert consumer.collect_pending_credentials is not None
            with pytest.warns(
                RuntimeWarning, match='MCP server "exa" failed to load and was skipped'
            ) as emitted:
                pending = consumer.collect_pending_credentials(context)
            assert str(emitted[0].message) == (
                f'MCP server "exa" failed to load and was skipped: {error}'
            )
            assert pending == []
            with pytest.warns(
                RuntimeWarning, match='MCP server "exa" failed to load and was skipped'
            ) as emitted:
                loaded = consumer.load({}, context)
            assert str(emitted[0].message) == (
                f'MCP server "exa" failed to load and was skipped: {error}'
            )
            assert [tool.name for tool in loaded] == ["notion_search"]
            assert list(fake_native_loading.await_args.args[0]) == ["notion"]

    @pytest.mark.parametrize("throw_on_load_error", [True, False])
    @pytest.mark.parametrize("verification_url", ["https://idp.example/connect", None])
    def test_missing_langsmith_authorization_stops_load(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        throw_on_load_error: bool,
        verification_url: str | None,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "verification_url": verification_url,
            }

        monkeypatch.setattr(
            mcp_load,
            "request_agent_auth_json",
            request,
        )
        mcp = define_mcp(
            servers={"notion": {"transport": "http", "url": _langsmith_mcp_url("notion")}},
            throw_on_load_error=throw_on_load_error,
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])

        with pytest.raises(
            RuntimeError,
            match=(
                r"Connect at https://idp\.example/connect, or in LangSmith Tools"
                if verification_url
                else "requires credentials; connect it in LangSmith Tools"
            ),
        ):
            consumer.load(
                {},
                AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    principal_id="01999999-9999-7999-8999-999999999999",
                    api_key="workspace-key",
                ),
            )

        fake_native_loading.assert_not_awaited()

    def test_langsmith_oauth_428_raises_credential_interrupt(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "verification_url": "https://idp.example/connect",
                "auth_id": "sess-notion",
                "slug": "mt-notion",
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        interrupts: list[object] = []

        class _InterruptedError(RuntimeError):
            pass

        def interrupt(payload: object) -> None:
            interrupts.append(payload)
            raise _InterruptedError

        monkeypatch.setattr(
            "managed_deepagents._connection_authorization.graph_interrupt", interrupt
        )

        mcp = define_mcp(
            servers={"notion": {"transport": "http", "url": _langsmith_mcp_url("notion")}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])

        with pytest.raises(_InterruptedError):
            consumer.load(
                {},
                AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    principal_id="01999999-9999-7999-8999-999999999999",
                    api_key="workspace-key",
                ),
            )

        assert interrupts == [
            {
                "type": "credential_authorization_required",
                "message": "Connect the following integrations to continue.",
                "credentials": [
                    {
                        "slug": "mt-notion",
                        "kind": "oauth2",
                        "connect_url": "https://idp.example/connect",
                        "auth_id": "sess-notion",
                        "provider": "notion",
                    }
                ],
            }
        ]
        fake_native_loading.assert_not_awaited()

    def test_langsmith_oauth_428_collects_all_servers_before_interrupt(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bodies = {
            _langsmith_mcp_url("notion"): {
                "verification_url": "https://idp.example/notion",
                "auth_id": "sess-notion",
                "slug": "mt-notion",
            },
            _langsmith_mcp_url("linear"): {
                "verification_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
                "slug": "mt-linear",
            },
        }

        def request(
            _method: str, url: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, bodies[url]

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        interrupts: list[object] = []

        class _InterruptedError(RuntimeError):
            pass

        def interrupt(payload: object) -> None:
            interrupts.append(payload)
            raise _InterruptedError

        monkeypatch.setattr(
            "managed_deepagents._connection_authorization.graph_interrupt", interrupt
        )

        mcp = define_mcp(
            servers={
                "notion": {"transport": "http", "url": _langsmith_mcp_url("notion")},
                "linear": {"transport": "http", "url": _langsmith_mcp_url("linear")},
            },
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.collect_pending_credentials is not None

        pending = consumer.collect_pending_credentials(
            AgentAuthContext(
                base_url="https://api.smith.langchain.com",
                principal_id="01999999-9999-7999-8999-999999999999",
                api_key="workspace-key",
            )
        )
        assert pending == [
            {
                "slug": "mt-notion",
                "kind": "oauth2",
                "connect_url": "https://idp.example/notion",
                "auth_id": "sess-notion",
                "provider": "notion",
            },
            {
                "slug": "mt-linear",
                "kind": "oauth2",
                "connect_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
                "provider": "linear",
            },
        ]

        with pytest.raises(_InterruptedError):
            consumer.load(
                {},
                AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    principal_id="01999999-9999-7999-8999-999999999999",
                    api_key="workspace-key",
                ),
            )

        assert interrupts[0]["credentials"] == pending  # type: ignore[index]
        fake_native_loading.assert_not_awaited()

    def test_langsmith_428_labels_credential_with_display_name(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "kind": "oauth",
                "verification_url": "https://idp.example/connect",
                "auth_id": "sess-notion",
                "slug": "mt-notion",
                "display_name": "Notion",
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={"docs": {"transport": "http", "url": _langsmith_mcp_url("notion")}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.collect_pending_credentials is not None

        pending = consumer.collect_pending_credentials(_KIT_CONTEXT)

        assert [credential["provider"] for credential in pending] == ["Notion"]

    def test_langsmith_secret_428_raises_secret_credential_interrupt(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "status": 428,
                "kind": "secret",
                "slug": "mt-datadog",
                "display_name": "Datadog",
                "verification_url": "https://smith.example/agent-auth-secret-input/sess-datadog",
                "auth_id": "sess-datadog",
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        interrupts: list[object] = []

        class _InterruptedError(RuntimeError):
            pass

        def interrupt(payload: object) -> None:
            interrupts.append(payload)
            raise _InterruptedError

        monkeypatch.setattr(
            "managed_deepagents._connection_authorization.graph_interrupt", interrupt
        )
        mcp = define_mcp(
            servers={"datadog": {"transport": "http", "url": _langsmith_mcp_url("datadog")}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])

        with pytest.raises(_InterruptedError):
            consumer.load({}, _KIT_CONTEXT)

        assert interrupts == [
            {
                "type": "credential_authorization_required",
                "message": "Connect the following integrations to continue.",
                "credentials": [
                    {
                        "slug": "mt-datadog",
                        "kind": "secret",
                        "connect_url": "https://smith.example/agent-auth-secret-input/sess-datadog",
                        "auth_id": "sess-datadog",
                        "provider": "Datadog",
                    }
                ],
            }
        ]
        fake_native_loading.assert_not_awaited()

    def test_langsmith_gateway_428_collects_every_oauth_credential(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "credentials": [
                    {
                        "kind": "oauth",
                        "slug": "mt-notion",
                        "display_name": "Notion",
                        "verification_url": "https://idp.example/notion",
                        "auth_id": "sess-notion",
                    },
                    {
                        "kind": "oauth",
                        "slug": "mt-linear",
                        "display_name": "Linear",
                        "verification_url": "https://idp.example/linear",
                        "auth_id": "sess-linear",
                    },
                ]
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={"tools": {"transport": "http", "url": _KIT_URL}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.collect_pending_credentials is not None

        pending = consumer.collect_pending_credentials(_KIT_CONTEXT)

        assert pending == [
            {
                "slug": "mt-notion",
                "kind": "oauth2",
                "connect_url": "https://idp.example/notion",
                "auth_id": "sess-notion",
                "provider": "Notion",
            },
            {
                "slug": "mt-linear",
                "kind": "oauth2",
                "connect_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
                "provider": "Linear",
            },
        ]

    def test_langsmith_gateway_428_collects_secrets_that_carry_a_link(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "credentials": [
                    {
                        "kind": "oauth",
                        "slug": "mt-linear",
                        "display_name": "Linear",
                        "verification_url": "https://idp.example/linear",
                        "auth_id": "sess-linear",
                    },
                    {
                        "kind": "secret",
                        "slug": "mt-datadog",
                        "display_name": "Datadog",
                        "verification_url": (
                            "https://smith.example/agent-auth-secret-input/sess-datadog"
                        ),
                        "auth_id": "sess-datadog",
                    },
                ]
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={"tools": {"transport": "http", "url": _KIT_URL}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.collect_pending_credentials is not None

        pending = consumer.collect_pending_credentials(_KIT_CONTEXT)

        assert pending == [
            {
                "slug": "mt-linear",
                "kind": "oauth2",
                "connect_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
                "provider": "Linear",
            },
            {
                "slug": "mt-datadog",
                "kind": "secret",
                "connect_url": "https://smith.example/agent-auth-secret-input/sess-datadog",
                "auth_id": "sess-datadog",
                "provider": "Datadog",
            },
        ]

    def test_langsmith_gateway_428_with_an_unlinked_secret_names_it_without_interrupting(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 428, {
                "credentials": [
                    {
                        "kind": "oauth",
                        "slug": "mt-linear",
                        "display_name": "Linear",
                        "verification_url": "https://idp.example/linear",
                        "auth_id": "sess-linear",
                    },
                    {"kind": "secret", "slug": "mt-builtin-exa", "display_name": "Exa"},
                ]
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        interrupts: list[object] = []
        monkeypatch.setattr(
            "managed_deepagents._connection_authorization.graph_interrupt", interrupts.append
        )
        mcp = define_mcp(
            servers={"tools": {"transport": "http", "url": _KIT_URL}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])

        with pytest.raises(RuntimeError, match="requires credentials for Exa; connect them"):
            consumer.load({}, _KIT_CONTEXT)

        assert interrupts == []
        fake_native_loading.assert_not_awaited()

    def test_langsmith_partial_kit_collects_linked_missing_credentials_from_meta(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 200, {
                "result": {
                    "_meta": {
                        "com.langchain.langsmith/missingCredentials": [
                            {
                                "kind": "oauth",
                                "slug": "mt-linear",
                                "display_name": "Linear",
                                "verification_url": "https://idp.example/linear",
                                "auth_id": "sess-linear",
                            },
                            {"kind": "secret", "slug": "mt-builtin-exa", "display_name": "Exa"},
                        ]
                    },
                    "tools": [{"name": "github_create_issue", "inputSchema": {"type": "object"}}],
                },
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={"tools": {"transport": "http", "url": _KIT_URL}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        assert consumer.collect_pending_credentials is not None

        pending = consumer.collect_pending_credentials(_KIT_CONTEXT)

        assert pending == [
            {
                "slug": "mt-linear",
                "kind": "oauth2",
                "connect_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
                "provider": "Linear",
            }
        ]
        assert (
            'skipped missing credential for LangSmith MCP server "tools" '
            "(provider=Exa): missing verification_url, auth_id"
        ) in caplog.text
        assert "https://" not in caplog.text

    def test_langsmith_partial_kit_interrupts_before_loading_connected_tools(
        self,
        fake_native_loading: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def request(
            _method: str, *_args: object, **_kwargs: object
        ) -> tuple[int, dict[str, object]]:
            return 200, {
                "result": {
                    "_meta": {
                        "com.langchain.langsmith/missingCredentials": [
                            {
                                "kind": "oauth",
                                "slug": "mt-linear",
                                "display_name": "Linear",
                                "verification_url": "https://idp.example/linear",
                                "auth_id": "sess-linear",
                            }
                        ]
                    },
                    "tools": [{"name": "github_create_issue", "inputSchema": {"type": "object"}}],
                },
            }

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        interrupts: list[object] = []

        class _InterruptedError(RuntimeError):
            pass

        def interrupt(payload: object) -> None:
            interrupts.append(payload)
            raise _InterruptedError

        monkeypatch.setattr(
            "managed_deepagents._connection_authorization.graph_interrupt", interrupt
        )
        mcp = define_mcp(
            servers={"tools": {"transport": "http", "url": _KIT_URL}},
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])

        with pytest.raises(_InterruptedError):
            consumer.load({}, _KIT_CONTEXT)

        assert interrupts == [
            {
                "type": "credential_authorization_required",
                "message": "Connect the following integrations to continue.",
                "credentials": [
                    {
                        "slug": "mt-linear",
                        "kind": "oauth2",
                        "connect_url": "https://idp.example/linear",
                        "auth_id": "sess-linear",
                        "provider": "Linear",
                    }
                ],
            }
        ]
        fake_native_loading.assert_not_awaited()

    def test_does_not_send_workspace_credentials_to_another_origin(
        self, fake_native_loading: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests: list[str] = []

        def request(*_args: object, **_kwargs: object) -> tuple[int, dict[str, object]]:
            requests.append("POST")
            return 200, {}

        monkeypatch.setattr(mcp_load, "request_agent_auth_json", request)
        mcp = define_mcp(
            servers={
                "evil": {
                    "transport": "http",
                    "url": _langsmith_mcp_url("exa", "https://evil.example"),
                }
            },
        )
        [consumer] = mcp_load.mcp_credential_consumers([mcp])
        with pytest.warns(RuntimeWarning, match="not a LangSmith managed-tools URL"):
            loaded = consumer.load(
                {},
                AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    principal_id="01999999-9999-7999-8999-999999999999",
                    api_key="workspace-key",
                ),
            )
        assert loaded == []
        assert requests == []
        assert fake_native_loading.await_args.args[0] == {}

    def test_loads_each_mcp_connector_declaration_independently(
        self, fake_native_loading: AsyncMock
    ) -> None:
        docs = define_mcp(
            servers={"docs": {"transport": "http", "url": "https://docs.example.com/mcp"}},
        )
        wiki = define_mcp(
            servers={"wiki": {"transport": "http", "url": "https://wiki.example.com/mcp"}},
        )

        docs_tools = mcp_load.load_managed_mcp_tools(docs)
        wiki_tools = mcp_load.load_managed_mcp_tools(wiki)

        assert [tool.name for tool in docs_tools] == ["docs_search"]
        assert [tool.name for tool in wiki_tools] == ["wiki_search"]
        assert fake_native_loading.await_count == 2
        assert fake_native_loading.await_args_list[0].args[0] == docs.config["servers"]
        assert fake_native_loading.await_args_list[1].args[0] == wiki.config["servers"]

    @pytest.mark.parametrize("transport", ["http", "sse"])
    def test_sends_an_agent_connection_as_a_bearer_header(
        self, monkeypatch: pytest.MonkeyPatch, transport: str
    ) -> None:
        resolved: list[str] = []

        async def _resolve(slug: str) -> str:
            resolved.append(slug)
            return "synthetic-anonymous-1"

        monkeypatch.setattr(mcp_load.connections, "resolve_deployment_secret", _resolve)

        async def run() -> None:
            async with serve(transport=transport) as server:
                mcp = define_mcp(
                    servers={
                        "docs": {
                            "transport": transport,
                            "url": server.url,
                            "connection": connections.get("docs", {"type": "agent"}),
                            "include_tools": ["whoami"],
                        },
                    },
                )
                tools = await load_eager_mcp_tools(mcp)
                assert [tool.name for tool in tools] == ["docs_whoami"]
                assert text(await invoke(tools, "docs_whoami")) == "anonymous:1"
                assert set(resolved) == {"docs"}
                assert server.observation.calls["whoami"] == 1

        scenario(run())

    def test_defers_user_connection_servers_until_per_run_mint_is_available(
        self, fake_native_loading: AsyncMock
    ) -> None:
        mcp = define_mcp(
            servers={
                "docs": {
                    "transport": "http",
                    "url": "https://docs.example.com/mcp",
                },
                "notion": {
                    "transport": "http",
                    "url": "https://mcp.notion.com/mcp",
                    "connection": connections.get("notion", {"type": "user"}),
                },
            },
        )

        tools = mcp_load.load_managed_mcp_tools(mcp)

        assert [tool.name for tool in tools] == ["docs_search"]
        assert fake_native_loading.await_args.args[0] == {
            "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
        }


class TestMcpCredentialConsumers:
    def test_reports_one_consumer_per_declaration_that_needs_a_grant(self) -> None:
        mcp = define_mcp(
            servers={
                "notion": {
                    "transport": "http",
                    "url": "https://mcp.notion.com/mcp",
                    "connection": connections.get("notion", {"type": "user"}),
                },
                "also_notion": {
                    "transport": "http",
                    "url": "https://mcp.notion.com/other",
                    "connection": connections.get("notion", {"type": "user"}),
                },
                "linear": {
                    "transport": "http",
                    "url": "https://mcp.linear.app/mcp",
                    "connection": connections.get("linear", {"type": "user"}),
                },
            },
        )

        consumers = mcp_load.mcp_credential_consumers([mcp])

        assert len(consumers) == 1
        assert consumers[0].declaration is mcp
        assert tuple(c.slug for c in consumers[0].credentials) == ("notion", "notion", "linear")

    def test_ignores_declarations_that_need_no_grant(self) -> None:
        plain = define_mcp(
            servers={"docs": {"transport": "http", "url": "https://docs.example.com/mcp"}},
        )
        agent_owned = define_mcp(
            servers={
                "docs": {
                    "transport": "http",
                    "url": "https://docs.example.com/mcp",
                    "connection": connections.get("docs", {"type": "agent"}),
                },
            },
        )

        assert mcp_load.mcp_credential_consumers([plain, agent_owned, object(), None]) == []


def scenario(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(asyncio.wait_for(coro, timeout=25))


def declaration(server: Server, **fields: Any) -> Any:
    return define_mcp(
        servers={
            "under_score": {
                "transport": "sse" if server.url.endswith("sse") else "http",
                "url": server.url,
                **fields,
            }
        }
    )


async def invoke(
    tools: list[object], name: str, args: dict[str, object] | None = None
) -> ToolMessage:
    tool = next(tool for tool in tools if getattr(tool, "name", None) == name)
    result = await cast("Any", tool).ainvoke(
        {"type": "tool_call", "id": "test-call", "name": name, "args": args or {}}
    )
    assert isinstance(result, ToolMessage)
    return result


def text(result: ToolMessage) -> str:
    return (
        result.content
        if isinstance(result.content, str)
        else "".join(block.get("text", "") for block in result.content if isinstance(block, dict))
    )


@pytest.fixture
def clear_catalog_cache() -> None:
    asyncio.run(mcp_load._catalog_cache.clear())


class State(TypedDict):
    result: str


def graph_factory(params: dict[str, Any], checkpointer: Any = None) -> Any:
    async def call(state: State) -> State:
        return {
            "result": text(
                await invoke(params["tools"], "under_score_echo", {"value": state["result"]})
            )
        }

    graph = StateGraph(State)
    graph.add_node("call", call)
    graph.add_edge(START, "call")
    graph.add_edge("call", END)
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.usefixtures("clear_catalog_cache")
class TestNativeMcp:
    """Production loader coverage using real HTTP/SSE clients."""

    def test_cache_survives_new_clients_and_event_loops(self) -> None:
        async def run() -> None:
            async with serve() as server:
                definition = declaration(server)
                previous = None
                for _ in range(3):
                    # Model the existing synchronous/eval factory bridge as well as async loading.
                    tools = await asyncio.to_thread(mcp_load.load_managed_mcp_tools, definition)
                    assert previous is None or tools[0] is not previous[0]
                    previous = tools
                    assert (
                        text(await invoke(tools, "under_score_echo", {"value": "detached"}))
                        == "detached"
                    )
                assert server.observation.wire_methods["tools/list"] == 1

        scenario(run())

    def test_cache_isolates_verified_callers_and_credential_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def run() -> None:
            async with serve() as server:
                token = "synthetic-alice-1"
                monkeypatch.setattr(
                    mcp_load, "resolve_user_connection_grants", lambda *_a, **_k: {"notion": token}
                )
                definition = declaration(
                    server, connection=connections.get("notion", {"type": "user"})
                )
                for principal, current in (
                    ("alice", "1"),
                    ("alice", "1"),
                    ("bob", "1"),
                    ("alice", "2"),
                ):
                    token = f"synthetic-{principal}-{current}"
                    context = AgentAuthContext(
                        base_url="https://api.smith.langchain.com",
                        agent_id="deployment",
                        workspace_id="workspace",
                        principal_id=principal,
                    )
                    await load_caller_mcp_tools(definition, {"notion": token}, context)
                assert server.observation.wire_methods["tools/list"] == 3

        scenario(run())

    @pytest.mark.parametrize("transport", ["http", "sse"])
    def test_transport_selection_and_raw_filters(self, transport: str) -> None:
        async def run() -> None:
            async with serve(transport=transport, extra_name="with_under_score") as server:
                tools = await load_eager_mcp_tools(
                    declaration(server, include_tools=["with_under_score"])
                )
                assert [getattr(tool, "name", None) for tool in tools] == [
                    "under_score_with_under_score"
                ]
                assert (
                    text(await invoke(tools, "under_score_with_under_score", {"value": "routed"}))
                    == "routed"
                )
                assert await load_eager_mcp_tools(declaration(server, include_tools=[])) == []
                tools = await load_eager_mcp_tools(
                    declaration(server, exclude_tools=["with_under_score"])
                )
                names = [getattr(tool, "name", None) for tool in tools]
                assert "under_score_with_under_score" not in names
                assert "under_score_echo" in names
                assert text(await invoke(tools, "under_score_echo", {"value": "kept"})) == "kept"
                with pytest.raises(ValueError, match="missing"):
                    await load_eager_mcp_tools(declaration(server, include_tools=["missing"]))
                with pytest.raises(ValueError, match="missing"):
                    await load_eager_mcp_tools(declaration(server, exclude_tools=["missing"]))

        scenario(run())

    @pytest.mark.parametrize("second_transport", ["http", "sse"])
    def test_native_collisions_and_independent_tool_calls(self, second_transport: str) -> None:
        async def run() -> None:
            async with (
                serve(extra_name="b_c") as first,
                serve(extra_name="c", transport=second_transport) as second,
            ):
                collision = define_mcp(
                    servers={
                        "a": {"transport": "http", "url": first.url},
                        "a_b": {"transport": second_transport, "url": second.url},
                    }
                )
                with pytest.raises(ValueError, match="tool name collision"):
                    await load_eager_mcp_tools(collision)
                peers = define_mcp(
                    servers={
                        "a": {"transport": "http", "url": first.url},
                        "b": {"transport": second_transport, "url": second.url},
                    }
                )
                tools = await load_eager_mcp_tools(peers)
                before = second.observation.attempts
                second.observation.offline = True
                assert text(await invoke(tools, "a_echo", {"value": "ok"})) == "ok"
                assert second.observation.attempts == before

        scenario(run())

    @pytest.mark.parametrize("selection", [{"exclude_tools": ["b_c"]}, {"include_tools": []}])
    def test_unselected_tools_do_not_cause_collisions(
        self, selection: dict[str, list[str]]
    ) -> None:
        async def run() -> None:
            async with serve(extra_name="b_c") as first, serve(extra_name="c") as second:
                definition = define_mcp(
                    servers={
                        "a": {"transport": "http", "url": first.url, **selection},
                        "a_b": {"transport": "http", "url": second.url, "include_tools": ["c"]},
                    }
                )
                tools = await load_eager_mcp_tools(definition)
                assert sum(tool.name == "a_b_c" for tool in tools) == 1
                assert text(await invoke(tools, "a_b_c", {"value": "selected"})) == "selected"

        scenario(run())

    @pytest.mark.parametrize("timer", ["protocol", "http"])
    def test_protocol_request_and_http_read_timeouts_are_independent(
        self, monkeypatch: pytest.MonkeyPatch, timer: str
    ) -> None:
        original = mcp_load._http_client_factory

        def factory(
            headers: Any = None,
            timeout: Any = None,
            auth: Any = None,
            follow_redirects: bool = False,
        ) -> Any:
            budget = timeout or httpx2.Timeout(30)
            return original(
                headers,
                httpx2.Timeout(
                    connect=budget.connect,
                    write=budget.write,
                    pool=budget.pool,
                    read=2 if timer == "protocol" else 0.1,
                ),
                auth,
                follow_redirects,
            )

        monkeypatch.setattr(mcp_load, "_http_client_factory", factory)

        async def run() -> None:
            async with serve() as server:
                tools = await load_eager_mcp_tools(
                    declaration(server, timeout=0.1 if timer == "protocol" else 2)
                )
                if timer == "protocol":
                    with pytest.raises(MCPError) as caught:
                        await invoke(tools, "under_score_slow")
                    assert caught.value.code == REQUEST_TIMEOUT
                else:
                    started = asyncio.get_running_loop().time()
                    with pytest.raises((MCPError, RuntimeError, httpx2.TimeoutException)):
                        await invoke(tools, "under_score_slow")
                    assert asyncio.get_running_loop().time() - started < 0.75
                assert server.observation.slow_entered.is_set()

        scenario(run())

    def test_optional_discovery_failure_recovers_and_invalid_selections_surface(self) -> None:
        async def run() -> None:
            async with serve() as first, serve() as second:
                definition = define_mcp(
                    servers={
                        "a": {"transport": "http", "url": first.url},
                        "b": {"transport": "http", "url": second.url},
                    },
                    throw_on_load_error=False,
                )
                second.observation.offline = True
                with pytest.warns(RuntimeWarning, match='server "b"'):
                    tools = await load_eager_mcp_tools(definition)
                assert "a_echo" in [getattr(t, "name", None) for t in tools]
                assert "b_echo" not in [getattr(t, "name", None) for t in tools]
                second.observation.offline = False
                assert "b_echo" in [
                    getattr(t, "name", None) for t in await load_eager_mcp_tools(definition)
                ]
                await mcp_load._catalog_cache.clear()
                second.observation.fail_catalog = True
                with pytest.warns(RuntimeWarning, match='server "b"'):
                    tools = await load_eager_mcp_tools(definition)
                assert "a_echo" in [getattr(t, "name", None) for t in tools]
                assert "b_echo" not in [getattr(t, "name", None) for t in tools]
                with pytest.raises(Exception, match="unhandled errors"):
                    await load_eager_mcp_tools(define_mcp(servers=definition.config["servers"]))
                with pytest.raises(ValueError, match="missing"):
                    await load_eager_mcp_tools(declaration(first, include_tools=["missing"]))

        scenario(run())

    @pytest.mark.parametrize("status", [401, 403, 428])
    def test_auth_rejection_follows_load_policy_and_never_executes_tool(self, status: int) -> None:
        async def run() -> None:
            async with serve() as server:
                definition = declaration(server)
                tools = await load_eager_mcp_tools(definition)
                server.observation.auth_denied = True
                server.observation.auth_status = status
                with pytest.raises((RuntimeError, MCPError, httpx2.HTTPError)):
                    await load_eager_mcp_tools(definition)
                optional = define_mcp(
                    servers=definition.config["servers"], throw_on_load_error=False
                )
                with pytest.warns(RuntimeWarning, match='server "under_score"'):
                    assert await load_eager_mcp_tools(optional) == []
                before = server.observation.calls["echo"]
                with pytest.raises((RuntimeError, MCPError, httpx2.HTTPError)):
                    await invoke(tools, "under_score_echo", {"value": "rejected"})
                assert server.observation.calls["echo"] == before

        scenario(run())

    @pytest.mark.parametrize("failure", ["timeout", "cancel", "tool_error", "disconnect"])
    def test_native_calls_close_http_clients(
        self, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        original = mcp_load._http_client_factory
        clients = []

        def factory(*args: Any, **kwargs: Any) -> httpx2.AsyncClient:
            client = original(*args, **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(mcp_load, "_http_client_factory", factory)

        async def run() -> None:
            async with serve() as server:
                timeout = 0.15 if failure == "timeout" else 5
                tools = await load_eager_mcp_tools(declaration(server, timeout=timeout))
                assert all(client.is_closed for client in clients)

                async def call() -> None:
                    if failure == "tool_error":
                        assert (await invoke(tools, "under_score_fail")).status == "error"
                    elif failure == "disconnect":
                        server.observation.offline = True
                        with pytest.raises((RuntimeError, MCPError, httpx2.HTTPError)):
                            await invoke(tools, "under_score_echo", {"value": "offline"})
                    elif failure == "timeout":
                        with pytest.raises((MCPError, httpx2.TimeoutException)):
                            await invoke(tools, "under_score_slow")
                    else:
                        await invoke(tools, "under_score_slow")

                task = asyncio.create_task(call())
                if failure == "cancel":
                    await asyncio.wait_for(server.observation.slow_entered.wait(), 3)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    await task
                # Native cancellation cleanup may finish in its shielded teardown task.
                async with asyncio.timeout(3):
                    while not all(client.is_closed for client in clients):  # noqa: ASYNC110
                        await asyncio.sleep(0.01)

        scenario(run())

    def test_native_elicitation_interrupt_resume_with_detached_tools(self) -> None:
        async def run() -> None:
            async with serve() as server:
                checkpointer = InMemorySaver()
                config = {"configurable": {"thread_id": "mcp-resume"}}
                for payload in (
                    {"result": ""},
                    Command(
                        resume={
                            "responses": {
                                "answer": {"action": "accept", "content": {"value": "resumed"}}
                            }
                        }
                    ),
                ):

                    async def call(_state: State) -> State:
                        # Entry happens in a LangGraph middleware/node task, exit
                        # happens later in the factory task through adapter owners.
                        tools = await load_eager_mcp_tools(declaration(server))
                        return {"result": text(await invoke(tools, "under_score_ask"))}

                    graph = StateGraph(State)
                    graph.add_node("call", call)
                    graph.add_edge(START, "call")
                    graph.add_edge("call", END)
                    result = await graph.compile(checkpointer=checkpointer).ainvoke(payload, config)
                    if isinstance(payload, dict):
                        assert result["__interrupt__"][0].value["type"] == "mcp_elicitation"
                    else:
                        assert result["result"] == "resumed"
                assert server.observation.wire_methods["tools/list"] == 1

        scenario(run())

    def test_managed_user_tokens_rotate_and_resume_scopes_are_isolated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def run() -> None:
            async with serve() as server:
                context = AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    agent_id="deployment",
                    workspace_id="workspace",
                    principal_id="alice",
                )
                tokens = {"notion": "synthetic-alice-1"}
                monkeypatch.setattr(
                    "managed_deepagents._connectors.mcp.load.resolve_user_connection_grants",
                    lambda *_args, **_kwargs: tokens.copy(),
                )
                definition = declaration(
                    server,
                    connection=connections.get("notion", {"type": "user"}),
                    headers={"x-spike-principal": "alice", "x-spike-generation": "1"},
                )
                tools = await load_caller_mcp_tools(definition, tokens, context)
                assert text(await invoke(tools, "under_score_whoami")) == "alice:1"
                tokens["notion"] = "synthetic-alice-2"
                server.observation.generation = "2"
                assert text(await invoke(tools, "under_score_whoami")) == "alice:2"
                # Also assert the auth object delegates rather than retaining a token.
                auth = ManagedAuth(
                    server.url, token=lambda: asyncio.sleep(0, result=tokens["notion"])
                )
                request = httpx2.Request("POST", server.url)
                flow = auth.async_auth_flow(request)
                sent = await flow.__anext__()
                assert sent.headers["Authorization"] == "Bearer synthetic-alice-2"
                await flow.aclose()
                await load_caller_mcp_tools(definition, tokens, context)
                assert server.observation.lists["alice"] == 2

        scenario(run())

    @pytest.mark.parametrize(
        ("transport", "ttl"), [("http", None), ("sse", None), ("http", 0), ("http", 200)]
    )
    def test_native_cache_hints_and_fallback_expire(
        self, monkeypatch: pytest.MonkeyPatch, transport: str, ttl: int | None
    ) -> None:
        monkeypatch.setattr(mcp_load, "CACHE_TTL_MS", 200)

        async def run() -> None:
            async with serve(
                ttl=ttl, transport=transport, legacy_only=transport == "http" and ttl is None
            ) as server:
                definition = declaration(server)
                await load_eager_mcp_tools(definition)
                first = server.observation.lists["anonymous"]
                await load_eager_mcp_tools(definition)
                second = server.observation.lists["anonymous"]
                assert second > first if ttl == 0 else second == first
                await asyncio.sleep(0.25)
                await load_eager_mcp_tools(definition)
                assert server.observation.lists["anonymous"] > second

        scenario(run())

    def test_native_catalog_store_eviction_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mcp_load, "CACHE_MAX_ENTRIES", 2)
        monkeypatch.setattr(mcp_load, "_catalog_cache", mcp_load._SharedCacheStore())

        async def run() -> None:
            async with serve() as server:
                for name in ("a", "b", "c", "a"):
                    await load_eager_mcp_tools(
                        define_mcp(
                            servers={
                                name: {
                                    "transport": "http",
                                    "url": server.url,
                                    "headers": {"x-spike-principal": name},
                                }
                            }
                        )
                    )
                assert server.observation.wire_methods["tools/list"] == 4

        scenario(run())

    def test_native_multimodal_and_structured_artifact_through_managed_wrapper(self) -> None:

        async def run() -> None:
            async with serve() as server:
                tools = wrap_tools_with_managed_runtime(
                    await load_eager_mcp_tools(declaration(server))
                )
                message = await invoke(list(tools), "under_score_rich")
                assert isinstance(message.content, list)
                assert any(
                    isinstance(block, dict) and block.get("type") == "image"
                    for block in message.content
                )
                assert message.artifact is not None
                assert message.artifact["structured_content"] == {"answer": 42}

        scenario(run())

    def test_origin_bound_native_auth_never_sends_platform_headers_elsewhere(self) -> None:
        async def run() -> None:
            auth = ManagedAuth(
                "https://api.smith.langchain.com/mcp",
                headers={"x-api-key": "synthetic", "x-langsmith-identity": "alice"},
            )
            request = httpx2.Request("POST", "https://evil.example/mcp")
            flow = auth.async_auth_flow(request)
            with pytest.raises(ValueError, match="another origin"):
                await flow.__anext__()
            assert "x-api-key" not in request.headers
            assert "x-langsmith-identity" not in request.headers
            await flow.aclose()

        scenario(run())

    @pytest.mark.parametrize("failure", ["grant", 401, 403, 428])
    def test_revoked_grant_fails_without_reauthorization_or_replay(
        self,
        monkeypatch: pytest.MonkeyPatch,
        failure: str | int,
    ) -> None:
        async def run() -> None:
            async with serve() as server:
                context = AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    workspace_id="workspace",
                    agent_id="deployment",
                    principal_id="alice",
                )
                granted = True
                pending = {
                    "slug": "notion",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/connect",
                    "auth_id": "session",
                }

                def resolve(*_args: Any, interrupt: Any = None) -> dict[str, str]:
                    if not granted and failure == "grant":
                        interrupt(
                            {
                                "type": "credential_authorization_required",
                                "message": "Connect",
                                "credentials": [pending],
                            }
                        )
                    return {"notion": "synthetic-alice-1"}

                monkeypatch.setattr(mcp_load, "resolve_user_connection_grants", resolve)
                tools = await load_caller_mcp_tools(
                    declaration(server, connection=connections.get("notion", {"type": "user"})),
                    {"notion": "synthetic-alice-1"},
                    context,
                )
                granted = False
                if isinstance(failure, int):
                    server.observation.auth_status = failure
                    server.observation.auth_denied = True
                before = server.observation.calls["echo"]

                async def call(_state: State) -> State:
                    return {
                        "result": text(
                            await invoke(tools, "under_score_echo", {"value": "no replay"})
                        )
                    }

                graph = StateGraph(State)
                graph.add_node("call", call)
                graph.add_edge(START, "call")
                graph.add_edge("call", END)
                with pytest.raises((RuntimeError, MCPError, httpx2.HTTPError)):
                    await graph.compile(checkpointer=InMemorySaver()).ainvoke(
                        {"result": ""}, {"configurable": {"thread_id": "revoked"}}
                    )
                assert server.observation.calls["echo"] == before

        scenario(run())

    def test_grant_lost_during_deferred_discovery_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def run() -> None:
            async with serve() as server:
                context = AgentAuthContext(
                    base_url="https://api.smith.langchain.com",
                    workspace_id="workspace",
                    agent_id="deployment",
                    principal_id="alice",
                )
                reads = 0
                pending = {
                    "slug": "notion",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/connect",
                    "auth_id": "session",
                }

                def resolve(*_args: Any, interrupt: Any = None) -> dict[str, str]:
                    nonlocal reads
                    reads += 1
                    if reads > 1:
                        interrupt({"credentials": [pending]})
                    return {"notion": "synthetic-alice-1"}

                monkeypatch.setattr(mcp_load, "resolve_user_connection_grants", resolve)

                async def discover(_state: State) -> State:
                    await load_caller_mcp_tools(
                        declaration(server, connection=connections.get("notion", {"type": "user"})),
                        {"notion": "synthetic-alice-1"},
                        context,
                    )
                    return {"result": "unexpected success"}

                graph = StateGraph(State)
                graph.add_node("discover", discover)
                graph.add_edge(START, "discover")
                graph.add_edge("discover", END)
                with pytest.raises((RuntimeError, MCPError, httpx2.HTTPError)):
                    await graph.compile(checkpointer=InMemorySaver()).ainvoke(
                        {"result": ""}, {"configurable": {"thread_id": "discovery-grant-race"}}
                    )
                assert reads >= 2

        scenario(run())

    @pytest.mark.parametrize("failure", ["cancel", "initialization"])
    def test_native_discovery_closes_http_clients(
        self, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        original = mcp_load._http_client_factory
        clients = []

        def factory(*args: Any, **kwargs: Any) -> httpx2.AsyncClient:
            client = original(*args, **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(mcp_load, "_http_client_factory", factory)

        async def run() -> None:
            async with serve() as server:
                if failure == "initialization":
                    server.observation.discover_delay = 0.3
                    monkeypatch.setattr(mcp_load, "INIT_TIMEOUT_SECONDS", 0.05)
                    with pytest.raises(RuntimeError, match="initialize"):
                        await load_eager_mcp_tools(declaration(server))
                else:
                    server.observation.catalog_delay = 1
                    task = asyncio.create_task(load_eager_mcp_tools(declaration(server)))
                    async with asyncio.timeout(3):
                        while not server.observation.lists["anonymous"]:  # noqa: ASYNC110
                            await asyncio.sleep(0.01)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                async with asyncio.timeout(3):
                    while not all(client.is_closed for client in clients):  # noqa: ASYNC110
                        await asyncio.sleep(0.01)
                assert clients

        scenario(run())

    def test_shared_token_still_partitions_principals_and_workspaces(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token = "synthetic-shared-workspace-token"
        monkeypatch.setattr(
            mcp_load, "resolve_user_connection_grants", lambda *_a, **_k: {"notion": token}
        )

        async def run() -> None:
            async with serve() as server:
                definition = declaration(
                    server, connection=connections.get("notion", {"type": "user"})
                )
                for workspace, principal in (
                    ("one", "alice"),
                    ("one", "alice"),
                    ("one", "bob"),
                    ("two", "alice"),
                ):
                    context = AgentAuthContext(
                        base_url="https://api.smith.langchain.com",
                        agent_id="deployment",
                        workspace_id=workspace,
                        principal_id=principal,
                        api_key=token,
                    )
                    await load_caller_mcp_tools(definition, {"notion": token}, context)
                assert server.observation.wire_methods["tools/list"] == 3

        scenario(run())

    def test_native_tools_support_concurrent_calls_and_independent_worker_preparation(self) -> None:
        async def run() -> None:
            async with serve() as server:
                definition = declaration(server)
                tools = await load_eager_mcp_tools(definition)
                refreshed = await load_eager_mcp_tools(definition)
                assert refreshed[0] is not tools[0]

                values = ["one", "two", "three", "four"]
                messages = await asyncio.gather(
                    *(invoke(tools, "under_score_echo", {"value": value}) for value in values)
                )
                assert [text(message) for message in messages] == values

                def worker(value: str) -> str:
                    async def call() -> str:
                        prepared = await load_eager_mcp_tools(definition)
                        return text(await invoke(prepared, "under_score_echo", {"value": value}))

                    return asyncio.run(call())

                assert (
                    await asyncio.gather(*(asyncio.to_thread(worker, value) for value in values))
                    == values
                )
                assert server.observation.calls["echo"] == 8
                assert server.observation.wire_methods["tools/list"] == 1

        scenario(run())

    def test_http_auto_negotiation_falls_back_to_legacy_and_uses_native_cache(self) -> None:
        async def run() -> None:
            async with serve(legacy_only=True) as server:
                for _ in range(2):
                    tools = await load_eager_mcp_tools(declaration(server))
                    assert (
                        text(await invoke(tools, "under_score_echo", {"value": "legacy"}))
                        == "legacy"
                    )
                assert server.observation.wire_methods["server/discover"] >= 2
                assert server.observation.wire_methods["initialize"] >= 2
                # SDK 2.2 applies our explicit fallback TTL to legacy catalogs.
                assert server.observation.lists["anonymous"] == 1

        scenario(run())

    def test_modern_missing_cache_fields_follows_load_error_policy(self) -> None:
        async def run() -> None:
            async with serve(ttl=None) as server:
                definition = declaration(server)
                with pytest.raises(Exception, match="TaskGroup"):
                    await load_eager_mcp_tools(definition)
                optional = define_mcp(
                    servers=definition.config["servers"], throw_on_load_error=False
                )
                with pytest.warns(RuntimeWarning, match='server "under_score"'):
                    assert await load_eager_mcp_tools(optional) == []

        scenario(run())


@pytest.mark.usefixtures("clear_catalog_cache")
class TestMcpRuntime:
    """Managed graph and eval entrypoints using native MCP tools."""

    def test_managed_factory_loads_detached_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured = {}

        def create(**params: Any) -> Any:
            captured.update(params)
            return graph_factory(params)

        monkeypatch.setattr(runtime_mod, "create_deep_agent", create)

        async def run() -> None:
            async with serve() as server:
                definition = DeepAgentDefinition({"model": "synthetic"})
                connector = declaration(server)
                graph = await runtime_mod.compile_managed_agent_async(
                    definition, connectors=[connector], report_telemetry=False
                )
                assert (await graph.ainvoke({"result": "executed"}))["result"] == "executed"
                # Native tools remain usable after factory compilation/discovery has returned.
                assert (
                    text(await invoke(captured["tools"], "under_score_echo", {"value": "detached"}))
                    == "detached"
                )

        scenario(run())

    def test_deferred_credential_gate_interrupt_resume_and_native_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        context = AgentAuthContext(
            base_url="https://api.smith.langchain.com",
            workspace_id="workspace",
            agent_id="deployment",
            principal_id="alice",
        )
        caller = _Caller(context=context, identity={"user": {"id": "alice", "kind": "person"}})
        monkeypatch.setattr(gate, "_auth_context", lambda _runtime: caller)
        pending = [
            {
                "slug": "notion",
                "kind": "oauth2",
                "connect_url": "https://idp.example/connect",
                "auth_id": "session",
            }
        ]
        monkeypatch.setattr(
            gate, "collect_pending_user_connection_credentials", lambda *_args: pending.copy()
        )
        monkeypatch.setattr(
            gate,
            "resolve_user_connection_grants",
            lambda *_args, **_kwargs: {"notion": "synthetic-alice-1"},
        )
        monkeypatch.setattr(
            "managed_deepagents._connectors.mcp.load.resolve_user_connection_grants",
            lambda *_args, **_kwargs: {"notion": "synthetic-alice-1"},
        )
        checkpoint = InMemorySaver()
        captures = []

        def create(**params: Any) -> Any:
            middleware = next(m for m in params["middleware"] if m.name == "mda-credential-gate")

            async def call(_state: State) -> State:
                runtime = Runtime()
                await middleware.abefore_agent({}, runtime)
                request = ModelRequest(
                    model=FakeListChatModel(responses=["ok"]), messages=[], runtime=runtime
                )

                async def receive(model_request: Any) -> State:
                    captures.append(model_request.tools)
                    return {
                        "result": text(
                            await invoke(
                                model_request.tools, "under_score_echo", {"value": "deferred"}
                            )
                        )
                    }

                result = await middleware.awrap_model_call(request, receive)
                await middleware.awrap_model_call(request, receive)
                return result

            graph = StateGraph(State)
            graph.add_node("call", call)
            graph.add_edge(START, "call")
            graph.add_edge("call", END)
            return graph.compile(checkpointer=checkpoint)

        monkeypatch.setattr(runtime_mod, "create_deep_agent", create)

        async def run() -> None:
            async with serve() as server:
                connector = declaration(
                    server, connection=connections.get("notion", {"type": "user", "id": "alice"})
                )
                definition = DeepAgentDefinition({"model": "synthetic"})
                config = {"configurable": {"thread_id": "grant-resume"}}
                graph = await runtime_mod.compile_managed_agent_async(
                    definition, config, connectors=[connector], report_telemetry=False
                )
                result = await graph.ainvoke({"result": ""}, config)
                assert (
                    result["__interrupt__"][0].value["type"] == "credential_authorization_required"
                )
                assert server.observation.attempts == 0
                pending.clear()
                graph = await runtime_mod.compile_managed_agent_async(
                    definition, config, connectors=[connector], report_telemetry=False
                )
                result = await graph.ainvoke(Command(resume={}), config)
                assert result["result"] == "deferred"
                assert captures[0][0] is not captures[1][0]
                assert server.observation.wire_methods["tools/list"] == 1

        scenario(run())

    def test_harbor_existing_factory_invokes_detached_native_tools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner_path = (
            Path(__file__).parents[3] / "crates/mda/harbor/mda_harbor/runners/python_runner.py"
        )
        spec = importlib.util.spec_from_file_location("mda_test_python_runner", runner_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def create(**params: Any) -> Any:
            async def call(_state: dict[str, Any]) -> dict[str, Any]:
                message = await invoke(params["tools"], "under_score_echo", {"value": "eval"})
                return {"messages": [message]}

            graph = StateGraph(dict)
            graph.add_node("call", call)
            graph.add_edge(START, "call")
            graph.add_edge("call", END)
            return graph.compile()

        monkeypatch.setattr(runtime_mod, "create_deep_agent", create)

        async def run() -> None:
            async with serve() as server:
                project = tmp_path / "project"
                project.mkdir()
                (project / "langgraph.json").write_text('{"graphs":{"agent":"./entry.py:agent"}}')
                (project / "entry.py").write_text(
                    "from managed_deepagents import define_mcp\n"
                    "from managed_deepagents.define_deep_agent import DeepAgentDefinition\n"
                    "from managed_deepagents.runtime import compile_managed_agent_for_eval\n"
                    f'server = {{"transport":"http", "url":{server.url!r}}}\n'
                    'connector = define_mcp(servers={"under_score":server})\n'
                    "def agent(config):\n"
                    '    definition = DeepAgentDefinition({"model":"synthetic"})\n'
                    "    return compile_managed_agent_for_eval(\n"
                    "        definition, config, connectors=[connector], channels=[])\n"
                )

                def trial() -> None:
                    graph = module._load_graph(
                        project,
                        "agent",
                        model=None,
                        app_dir=str(tmp_path),
                        session_id="eval",
                        identity=None,
                    )
                    result = module._invoke_graph(
                        graph,
                        "execute",
                        app_dir=str(tmp_path),
                        session_id="eval",
                        identity=None,
                    )
                    assert text(result["messages"][0]) == "eval"

                await asyncio.to_thread(trial)

        scenario(run())

    def test_mcp_caller_native_cache_refresh_retry_and_revocation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        context = AgentAuthContext(
            base_url="https://api.smith.langchain.com",
            workspace_id="workspace",
            agent_id="deployment",
            principal_id="alice",
        )
        caller = _Caller(context=context, identity={"user": {"id": "alice", "kind": "person"}})
        monkeypatch.setattr(gate, "_auth_context", lambda _runtime: caller)
        token = "synthetic-alice-1"
        pending: list[dict[str, str]] = []
        monkeypatch.setattr(
            gate, "collect_pending_user_connection_credentials", lambda *_a: pending.copy()
        )
        for module in (gate, mcp_load):
            monkeypatch.setattr(
                module, "resolve_user_connection_grants", lambda *_a, **_k: {"notion": token}
            )

        def interrupt(_payload: object) -> None:
            raise RuntimeError("synthetic grant required")

        monkeypatch.setattr(gate, "_interrupt", interrupt)

        async def run() -> None:
            nonlocal token
            async with serve() as server:
                definition = declaration(
                    server,
                    connection=connections.get("notion", {"type": "user"}),
                    headers={"x-spike-principal": "alice"},
                )

                async def prepare() -> list[object]:
                    middleware = gate.create_credential_gate_middleware(
                        mcp_load.mcp_credential_consumers([definition])
                    )
                    await middleware.abefore_agent({}, SimpleNamespace(config={}))
                    request = SimpleNamespace(runtime=None, tools=[], override=SimpleNamespace)

                    async def receive(request: Any) -> list[object]:
                        return request.tools

                    return await middleware.awrap_model_call(request, receive)

                first = await prepare()
                assert (await prepare())[0] is not first[0]
                assert (id(definition), context.principal_id) not in gate._tool_cache
                assert server.observation.wire_methods["tools/list"] == 1
                token = "synthetic-alice-2"
                server.observation.generation = "2"
                # Previously prepared tools also read the current LangSmith grant.
                assert text(await invoke(first, "under_score_whoami")) == "alice:2"
                refreshed = await prepare()
                assert refreshed[0] is not first[0]
                assert server.observation.wire_methods["tools/list"] == 2
                server.observation.offline = True
                with pytest.raises(MCPError):
                    await prepare()
                server.observation.offline = False
                retried = await prepare()
                assert text(await invoke(retried, "under_score_whoami")) == "alice:2"
                attempts = server.observation.attempts
                pending.append(
                    {
                        "slug": "notion",
                        "kind": "oauth2",
                        "connect_url": "https://idp.example/connect",
                    }
                )
                with pytest.raises(RuntimeError, match="synthetic grant required"):
                    await prepare()
                assert server.observation.attempts == attempts

        scenario(run())
