"""Unit tests for the root authoring API surface."""

from __future__ import annotations

import doctest
import os
import pydoc
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from typing import Any, cast

import pytest

import managed_deepagents
import managed_deepagents._connectors.mcp as mcp_mod
import managed_deepagents.namespaces as namespaces_mod
from managed_deepagents import (
    _extension,
    connectors,
    define_deep_agent,
    define_mcp,
    define_sandbox,
)


def test_mcp_authoring_without_optional_provider_dependencies() -> None:
    script = textwrap.dedent("""
        import sys
        # Core frameworks are required. Only MCP provider extras are absent.
        for name in ('fastmcp', 'mcp', 'key_value'):
            sys.modules[name] = None
        from managed_deepagents import define_mcp, HttpMcpServerConfig
        server = HttpMcpServerConfig(transport='http', url='https://example.com/mcp')
        definition = define_mcp(servers={'docs': server})
        assert definition.config['servers']['docs']['transport'] == 'http'
        from managed_deepagents._connectors.mcp.load import load_managed_mcp_tools
        try:
            load_managed_mcp_tools(definition)
        except RuntimeError as error:
            assert 'requires `langchain[mcp]` to load MCP tools' in str(error)
        else:
            raise AssertionError('missing runtime dependency accepted')
        try:
            define_mcp(servers={'docs': {'transport': 'http'}})
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError('missing URL accepted')
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_mcp_public_help_and_examples() -> None:
    failed, attempted = doctest.testmod(mcp_mod)
    assert failed == 0
    assert attempted >= 3
    rendered = pydoc.render_doc(define_mcp, renderer=pydoc.plaintext)
    for field in ("servers", "timeout", "connection", "throw_on_load_error", "on_connection_error"):
        assert field in rendered
    for name in mcp_mod.__all__:
        if name != "RemoteMcpServerConfig":
            assert getattr(mcp_mod, name).__doc__
    assert "RemoteMcpServerConfig" in pydoc.render_doc(mcp_mod, renderer=pydoc.plaintext)


def test_root_all_exports_only_authoring_surface() -> None:
    assert managed_deepagents.__all__ == [
        "INTERRUPT_CORRELATION_KEY",
        "AgentEntry",
        "AgentFactory",
        "ChannelContentMessage",
        "ChannelContext",
        "ChannelEvent",
        "ChannelInfo",
        "ChannelMessage",
        "ChannelNativeMessage",
        "ConnectionNotFoundError",
        "CredentialAuthorizationRequiredError",
        "CredentialStoreError",
        "DeepAgentDefinition",
        "GeneratedSkill",
        "HttpChannel",
        "HttpChannelInboundMessage",
        "HttpChannelParseResult",
        "HttpChannelPostInput",
        "HttpChannelRequest",
        "HttpMcpServerConfig",
        "HttpPostedMessage",
        "InvalidAgentAuthEndpointError",
        "JsonValue",
        "ManagedChannelContext",
        "ManagedDeepAgentRuntime",
        "ManagedRunContext",
        "ManagedRuntime",
        "ManagedServerInfo",
        "ManagedServerRuntime",
        "ManagedToolRuntime",
        "McpSelection",
        "McpServersConfig",
        "McpServersDefinition",
        "MemoryDefinition",
        "MemoryLayer",
        "MessageContent",
        "NormalizedChannelEvent",
        "OutboundMessage",
        "PostedChannelMessage",
        "PromptResponse",
        "RemoteMcpServerConfig",
        "RuntimeSchedules",
        "SandboxDefinition",
        "ScheduleNotFoundError",
        "ScheduleOwner",
        "SchedulePage",
        "SchedulePatch",
        "ScheduleRecord",
        "SkillSelection",
        "SlackPromptResponse",
        "SseMcpServerConfig",
        "auth",
        "basic",
        "bearer",
        "channels",
        "connections",
        "connectors",
        "define_deep_agent",
        "define_identity",
        "define_mcp",
        "define_memory",
        "define_sandbox",
        "define_schedule",
        "schedules",
    ]


def test_dynamic_wrapper_is_not_public() -> None:
    assert not hasattr(managed_deepagents, "dynamic")
    assert not hasattr(managed_deepagents, "DynamicDeepAgentDefinition")


def test_contribution_api_is_withheld_from_the_package_root() -> None:
    """Projects select primitives; they cannot contribute their own."""
    assert not hasattr(managed_deepagents, "define_extension")
    assert "define_extension" not in managed_deepagents.__all__
    assert callable(_extension.define_extension)


def test_connectors_mcp_is_a_deprecated_alias_of_define_mcp() -> None:
    assert callable(managed_deepagents.define_mcp)
    assert callable(managed_deepagents.connectors.mcp)
    assert not hasattr(managed_deepagents, "connect")
    assert "connect" not in managed_deepagents.__all__
    assert not hasattr(namespaces_mod, "connect")
    with pytest.warns(FutureWarning, match=r"define_mcp\(\.\.\.\) instead"):
        mcp = connectors.mcp(
            servers={
                "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
            },
        )
    assert mcp.kind == "mcp_servers"


def test_define_mcp_does_not_emit_the_connectors_deprecation() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        define_mcp(
            servers={
                "docs": {"transport": "http", "url": "https://docs.example.com/mcp"},
            },
        )
    assert not any("connectors.mcp" in str(warning.message) for warning in caught)


def test_every_shipped_resource_has_an_authoring_factory() -> None:
    assert callable(managed_deepagents.channels.slack)
    assert callable(define_mcp)
    assert callable(define_sandbox)
    assert callable(managed_deepagents.connections.get)
    assert not hasattr(managed_deepagents, "credential")
    assert callable(managed_deepagents.auth.supabase)
    assert sorted(
        name for name in vars(type(managed_deepagents.auth)) if not name.startswith("_")
    ) == [
        "langsmith_api_key",
        "supabase",
    ]


def test_define_deep_agent_keeps_only_provided_author_options() -> None:
    agent = define_deep_agent(
        model="openai:gpt-5",
        tools=[],
        name="research",
    )

    assert agent.kind == "deep-agent"
    assert agent.config == {
        "model": "openai:gpt-5",
        "tools": [],
        "name": "research",
    }


def test_define_deep_agent_rejects_positional_model() -> None:
    untyped_define_deep_agent = cast("Any", define_deep_agent)
    with pytest.raises(TypeError, match="positional"):
        untyped_define_deep_agent("openai:gpt-5")


def test_langsmith_sandbox_accepts_legacy_thread_scope() -> None:
    thread_sandbox = define_sandbox(scope="thread")

    assert thread_sandbox.kind == "sandbox"
    assert not hasattr(thread_sandbox, "provider")
    assert "scope" not in thread_sandbox.options

    upgraded = define_sandbox(scope="thread", idle_ttl_seconds=600, default_timeout=600)
    assert upgraded.options == {"idle_ttl_seconds": 600, "default_timeout": 600}

    with pytest.raises(TypeError, match='sandbox scope is managed as "thread"'):
        define_sandbox(scope="agent")


def test_langsmith_sandbox_normalizes_default_timeout_to_integer() -> None:
    sandbox = define_sandbox(default_timeout=600)

    assert sandbox.options["default_timeout"] == 600
    assert type(sandbox.options["default_timeout"]) is int


def test_define_deep_agent_deeply_snapshots_selected_sandbox_options() -> None:
    proxy_config: dict[str, Any] = {"rules": [{"headers": [{"name": "X-Mode", "value": "before"}]}]}
    sandbox = define_sandbox(proxy_config=proxy_config)
    definition = define_deep_agent(name="support", sandbox=sandbox)

    proxy_config["rules"][0]["headers"][0]["value"] = "after"

    selected = definition.config["sandbox"]
    assert selected is not sandbox
    assert selected.options["proxy_config"]["rules"][0]["headers"][0]["value"] == "before"


def test_langsmith_sandbox_accepts_bake_bases() -> None:
    assert define_sandbox(snapshot_name="base").options["snapshot_name"] == "base"
    assert define_sandbox(snapshot_id="snap-1").options["snapshot_id"] == "snap-1"
    assert (
        define_sandbox(docker_image="python:3.12-slim").options["docker_image"]
        == "python:3.12-slim"
    )
    assert (
        define_sandbox(
            docker_image="ghcr.io/acme/base:1",
            registry={
                "url": "ghcr.io",
                "username": "octocat",
                "password_env": "GHCR_TOKEN",
            },
        ).options["registry"]["url"]
        == "ghcr.io"
    )


def test_langsmith_sandbox_rejects_registry_without_docker_image() -> None:
    with pytest.raises(TypeError, match="registry requires docker_image"):
        define_sandbox(
            registry={
                "url": "ghcr.io",
                "username": "octocat",
                "password_env": "GHCR_TOKEN",
            }
        )


@pytest.mark.parametrize("field", ["url", "username", "password_env"])
@pytest.mark.parametrize("value", ["", "   "])
def test_langsmith_sandbox_rejects_blank_registry_fields(field: str, value: str) -> None:
    registry = {
        "url": "ghcr.io",
        "username": "octocat",
        "password_env": "GHCR_TOKEN",
    }
    registry[field] = value

    with pytest.raises(ValueError, match=field):
        define_sandbox(
            docker_image="ghcr.io/acme/base:1",
            registry=registry,
        )


def test_langsmith_sandbox_rejects_multiple_bake_bases() -> None:
    with pytest.raises(TypeError, match="mutually exclusive"):
        define_sandbox(snapshot_name="base", snapshot_id="snap-1")
    with pytest.raises(TypeError, match="mutually exclusive"):
        define_sandbox(snapshot_name="base", docker_image="python:3.12-slim")


def test_langsmith_sandbox_rejects_managed_options() -> None:
    with pytest.raises(TypeError, match="owned by the managed runtime"):
        define_sandbox(image_name="custom")
    with pytest.raises(TypeError, match="owned by the managed runtime"):
        define_sandbox(template_name="old")
