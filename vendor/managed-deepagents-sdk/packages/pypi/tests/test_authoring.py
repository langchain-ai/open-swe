"""Unit tests for the root authoring API surface."""

from __future__ import annotations

import doctest
import os
import pkgutil
import pydoc
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any, cast

import pytest

import managed_deepagents
import managed_deepagents._connectors.mcp as mcp_mod
import managed_deepagents.namespaces as namespaces_mod
from managed_deepagents import (
    _extension,
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


def test_mcp_import_suppresses_only_its_beta_warning() -> None:
    script = textwrap.dedent("""
        import sys
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            import managed_deepagents
            # Exports load on first use: MCP support, and langchain.mcp with it.
            managed_deepagents.define_mcp
            assert 'langchain.mcp' in sys.modules
            assert not any('`langchain.mcp` is in beta.' in str(w.message) for w in caught)
            warnings.warn('Another beta warning', UserWarning)
            warnings.warn('`langchain.mcp` is in beta.', UserWarning)
            assert [str(w.message) for w in caught[-2:]] == [
                'Another beta warning', '`langchain.mcp` is in beta.'
            ]
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
        "BrowserDefinition",
        "ChannelActor",
        "ChannelContentMessage",
        "ChannelContext",
        "ChannelEvent",
        "ChannelInfo",
        "ChannelMessage",
        "ChannelName",
        "ChannelNativeMessage",
        "ChannelReply",
        "ChannelRunInput",
        "ChannelRunOptions",
        "ChannelRunResult",
        "ConnectionNotFoundError",
        "CredentialAuthorizationRequiredError",
        "CredentialStoreError",
        "DeepAgentDefinition",
        "EndpointAnswer",
        "EndpointEvent",
        "EndpointRequest",
        "EndpointThread",
        "EventEndpoint",
        "EventFinished",
        "GeneratedSkill",
        "GitHubActor",
        "GitHubAppOptions",
        "GitHubChannelThread",
        "GitHubCheckRunPayload",
        "GitHubCheckSuitePayload",
        "GitHubEndpointEvent",
        "GitHubEndpointRequest",
        "GitHubEvent",
        "GitHubEventCallback",
        "GitHubEventResult",
        "GitHubHandlers",
        "GitHubIssueCommentPayload",
        "GitHubIssuePayload",
        "GitHubLabeledIssuePayload",
        "GitHubLabeledPullRequestPayload",
        "GitHubPayload",
        "GitHubPullRequestPayload",
        "GitHubPushPayload",
        "GitHubReviewCommentPayload",
        "GitHubReviewPayload",
        "GitHubStatusPayload",
        "GitHubWorkflowPayload",
        "HttpChannel",
        "HttpChannelCancelMessage",
        "HttpChannelInboundMessage",
        "HttpChannelParseResult",
        "HttpChannelPostInput",
        "HttpChannelRequest",
        "HttpDelivery",
        "HttpFinish",
        "HttpFinishedDelivery",
        "HttpFinishedRun",
        "HttpMcpServerConfig",
        "HttpPostedMessage",
        "HttpRunStatus",
        "HttpSkipReason",
        "InvalidAgentAuthEndpointError",
        "JsonValue",
        "LinearActor",
        "LinearAppOptions",
        "LinearChannelThread",
        "LinearCommentCreateEvent",
        "LinearCommentCreatePayload",
        "LinearEndpointRequest",
        "LinearEvent",
        "LinearEventCallback",
        "LinearEventKey",
        "LinearEventResult",
        "LinearHandlers",
        "LinearIssueCreateEvent",
        "LinearIssueCreatePayload",
        "LinearIssueUpdateEvent",
        "LinearIssueUpdatePayload",
        "LinearPayload",
        "LinearRateLimitError",
        "LinearSessionCreatedEvent",
        "LinearSessionCreatedPayload",
        "LinearSessionEvent",
        "LinearSessionPromptedEvent",
        "LinearSessionPromptedPayload",
        "LinearTeam",
        "ManagedChannelContext",
        "ManagedDeepAgentRuntime",
        "ManagedRunContext",
        "ManagedRuntime",
        "ManagedServerInfo",
        "ManagedServerRuntime",
        "ManagedThread",
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
        "ResolveUser",
        "ResolveUserEndpointRequest",
        "ResolveUserRequest",
        "ResolveUserResult",
        "RuntimeChannel",
        "RuntimeSchedules",
        "SandboxDefinition",
        "SandboxReplacement",
        "ScheduleNotFoundError",
        "ScheduleOwner",
        "SchedulePage",
        "SchedulePatch",
        "ScheduleRecord",
        "SharedAgentFactory",
        "SharedInputs",
        "SkillSelection",
        "SlackActor",
        "SlackChannelThread",
        "SlackEndpointRequest",
        "SlackEvent",
        "SlackEventCallback",
        "SlackEventKey",
        "SlackFile",
        "SlackHandlers",
        "SlackInteractionCallback",
        "SlackInteractionEvent",
        "SlackMessageEvent",
        "SlackMessageKey",
        "SlackPromptResponse",
        "SlackStatusChoice",
        "SlackStatusChooser",
        "SlackStatusContext",
        "SlackStatusStep",
        "SlackStatusStepStatus",
        "SlackStatusValue",
        "SseMcpServerConfig",
        "SupportedEventKey",
        "ThreadOrigin",
        "ThreadParticipant",
        "auth",
        "basic",
        "bearer",
        "channels",
        "connections",
        "define_browser",
        "define_deep_agent",
        "define_identity",
        "define_mcp",
        "define_memory",
        "define_sandbox",
        "define_schedule",
        "open_sandbox",
        "replace_sandbox",
        "schedules",
        "shared_factory",
    ]


def test_dynamic_wrapper_is_not_public() -> None:
    assert not hasattr(managed_deepagents, "dynamic")
    assert not hasattr(managed_deepagents, "DynamicDeepAgentDefinition")


def test_contribution_api_is_withheld_from_the_package_root() -> None:
    """Projects select primitives; they cannot contribute their own."""
    assert not hasattr(managed_deepagents, "define_extension")
    assert "define_extension" not in managed_deepagents.__all__
    assert callable(_extension.define_extension)


def test_connectors_namespace_was_removed() -> None:
    assert callable(managed_deepagents.define_mcp)
    assert not hasattr(managed_deepagents, "connectors")
    assert "connectors" not in managed_deepagents.__all__
    assert not hasattr(namespaces_mod, "connectors")
    assert not hasattr(managed_deepagents, "connect")
    assert "connect" not in managed_deepagents.__all__
    assert not hasattr(namespaces_mod, "connect")


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


def test_langsmith_sandbox_rejects_the_removed_scope_option() -> None:
    sandbox = define_sandbox()
    assert sandbox.kind == "sandbox"
    assert not hasattr(sandbox, "provider")
    assert sandbox.options == {}

    untyped_define_sandbox = cast("Any", define_sandbox)
    for scope in ("thread", "agent"):
        with pytest.raises(TypeError, match="sandbox scope was removed"):
            untyped_define_sandbox(scope=scope)


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


def test_endpoint_types_validate_with_pydantic() -> None:
    import datetime as dt  # noqa: PLC0415

    from pydantic import TypeAdapter, ValidationError  # noqa: PLC0415

    from managed_deepagents import (  # noqa: PLC0415
        EndpointAnswer,
        GitHubEndpointRequest,
        LinearEndpointRequest,
    )

    answers = TypeAdapter(EndpointAnswer | None)
    answer = answers.validate_python(
        {"input": "Plan it", "metadata": {"issue": "ENG-1"}, "webhook": "https://x.example/done"}
    )
    assert answer is not None
    assert answer["input"] == "Plan it"
    # The whole answer is JSON, and only the run options MDA takes.
    for invalid in (
        {"input": "x", "metadata": {"at": dt.datetime.now(tz=dt.UTC)}},
        {"input": "x", "config": {}},
    ):
        with pytest.raises(ValidationError):
            answers.validate_python(invalid)

    # One receiver takes every key: the key picks the payload's type.
    requests = TypeAdapter(LinearEndpointRequest)
    thread = {"id": "t-1", "channel": {"name": "linear"}}
    session = {
        "type": "AgentSessionEvent",
        "action": "prompted",
        "organizationId": "org-1",
        "webhookTimestamp": 1,
        "agentSession": {"id": "s-1", "issue": None},
        "agentActivity": {"content": {"type": "prompt", "body": "go"}},
    }
    request = requests.validate_python(
        {
            "event": {"key": "AgentSessionEvent.prompted", "deliveryId": "d-1", "payload": session},
            "thread": thread,
        }
    )
    assert request["event"]["deliveryId"] == "d-1"
    with pytest.raises(ValidationError):
        requests.validate_python(
            {
                "event": {"key": "Issue.create", "deliveryId": "d-2", "payload": session},
                "thread": thread,
            }
        )

    # A GitHub event also carries the fields a handler gets, such as the handles
    # it mentions.
    github = TypeAdapter(GitHubEndpointRequest)
    comment = {
        "action": "created",
        "issue": {"number": 1, "title": "Bug", "body": None},
        "comment": {"id": 2, "body": "@openswe look"},
        "repository": {
            "id": 3,
            "name": "api",
            "full_name": "acme/api",
            "private": False,
            "owner": {"login": "acme"},
        },
        "sender": {"id": 4, "login": "ada", "type": "User"},
        "installation": {"id": 5},
    }
    event = {"key": "issue_comment.created", "deliveryId": "d-3", "payload": comment}
    fields = {
        "action": "created",
        "repository": {
            "id": 3,
            "owner": "acme",
            "name": "api",
            "fullName": "acme/api",
            "private": False,
        },
        "sender": {"id": 4, "login": "ada", "type": "User"},
        "mentions": ["@openswe"],
    }
    request = github.validate_python({"event": {**event, **fields}, "thread": thread})
    assert request["event"].get("mentions") == ["@openswe"]
    # An older deployment sends none of them; a newer receiver still takes it.
    older = github.validate_python({"event": event, "thread": thread})
    assert older["event"].get("mentions", []) == []
    with pytest.raises(ValidationError):
        github.validate_python({"event": {**event, "mentions": "@openswe"}, "thread": thread})


def test_endpoint_types_import_without_the_agent_stack() -> None:
    """A web service can import them without the startup cost of deepagents."""
    code = (
        "import sys; "
        "from managed_deepagents import EndpointAnswer, EndpointRequest, "
        "GitHubIssueCommentPayload, LinearSessionCreatedPayload, SlackEndpointRequest; "
        "print(sorted(m for m in ('deepagents', 'langchain', 'langgraph', 'anthropic') "
        "if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_every_export_loads_on_first_use() -> None:
    """__all__ and the lazy export map list the same names, and each one resolves."""
    exports = managed_deepagents._EXPORTS
    assert sorted(managed_deepagents.__all__) == sorted(exports)
    for name, module in exports.items():
        assert getattr(managed_deepagents, name) is getattr(sys.modules[module], name), name


def test_exports_named_like_a_submodule_are_listed() -> None:
    """The package keeps exactly these exports from being hidden by their submodule."""
    submodules = {m.name for m in pkgutil.iter_modules(managed_deepagents.__path__)}
    assert submodules & set(managed_deepagents._EXPORTS) == managed_deepagents._SUBMODULE_EXPORTS


@pytest.mark.parametrize("name", ["define_deep_agent", "define_memory", "schedules"])
def test_importing_a_submodule_first_keeps_the_export(name: str) -> None:
    """Importing managed_deepagents.<name> first still leaves the export, not the submodule."""
    code = (
        "import sys, types; "
        f"import managed_deepagents.{name}; "
        f"from managed_deepagents import {name} as export; "
        f"submodule = sys.modules['managed_deepagents.{name}']; "
        "print(export is not submodule and not isinstance(export, types.ModuleType))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "True"


def test_assigning_an_export_goes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only Python's binding of a same-named submodule is skipped, never an assignment."""
    replacement = types.ModuleType("replacement")
    monkeypatch.setattr(managed_deepagents, "schedules", replacement)
    assert managed_deepagents.schedules is replacement
    monkeypatch.setattr(managed_deepagents, "define_memory", replacement)
    assert managed_deepagents.define_memory is replacement
