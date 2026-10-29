"""Managed Deep Agents for the Python ecosystem.

This package provides two things:

* the ``define_deep_agent`` authoring interface (the v0 contract for MDA), and
* the prebuilt, cross-compiled ``mda`` CLI. Each published wheel bundles the
  binary for a single platform under ``_binaries/`` and exposes it through the
  ``mda`` console script.

Resources use ``define_*`` factories while provider collections use namespaces:
``define_sandbox()``, ``define_mcp()``, ``channels.slack(...)``,
``connections.get(...)``, and ``auth.supabase(...)``. Contributing a *new*
primitive is not part of the public surface.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from managed_deepagents._channels.actor import ChannelActor, GitHubActor
    from managed_deepagents._channels.github_types import (
        GitHubAppOptions,
        GitHubChannelThread,
        GitHubCheckRunPayload,
        GitHubCheckSuitePayload,
        GitHubEndpointEvent,
        GitHubEndpointRequest,
        GitHubEvent,
        GitHubEventCallback,
        GitHubEventResult,
        GitHubHandlers,
        GitHubIssueCommentPayload,
        GitHubIssuePayload,
        GitHubLabeledIssuePayload,
        GitHubLabeledPullRequestPayload,
        GitHubPayload,
        GitHubPullRequestPayload,
        GitHubPushPayload,
        GitHubReviewCommentPayload,
        GitHubReviewPayload,
        GitHubStatusPayload,
        GitHubWorkflowPayload,
        SupportedEventKey,
    )
    from managed_deepagents._channels.http import (
        ChannelName,
        HttpChannel,
        HttpChannelCancelMessage,
        HttpChannelInboundMessage,
        HttpChannelParseResult,
        HttpChannelPostInput,
        HttpChannelRequest,
        HttpDelivery,
        HttpFinish,
        HttpFinishedDelivery,
        HttpFinishedRun,
        HttpPostedMessage,
        HttpRunStatus,
        HttpSkipReason,
    )
    from managed_deepagents._channels.http.endpoint import (
        EndpointAnswer,
        EndpointEvent,
        EndpointRequest,
        EndpointThread,
        EventEndpoint,
    )
    from managed_deepagents._channels.http.provider import EventFinished
    from managed_deepagents._channels.linear_graphql import LinearRateLimitError
    from managed_deepagents._channels.linear_schema import (
        LinearCommentCreatePayload,
        LinearIssueCreatePayload,
        LinearIssueUpdatePayload,
        LinearPayload,
        LinearSessionCreatedPayload,
        LinearSessionPromptedPayload,
    )
    from managed_deepagents._channels.linear_types import (
        LinearActor,
        LinearAppOptions,
        LinearChannelThread,
        LinearCommentCreateEvent,
        LinearEndpointRequest,
        LinearEvent,
        LinearEventCallback,
        LinearEventKey,
        LinearEventResult,
        LinearHandlers,
        LinearIssueCreateEvent,
        LinearIssueUpdateEvent,
        LinearSessionCreatedEvent,
        LinearSessionEvent,
        LinearSessionPromptedEvent,
        LinearTeam,
    )
    from managed_deepagents._channels.run_input import (
        ChannelRunInput,
        ChannelRunOptions,
        ChannelRunResult,
    )
    from managed_deepagents._channels.runtime import RuntimeChannel
    from managed_deepagents._channels.slack.events import (
        SlackActor,
        SlackChannelThread,
        SlackEndpointRequest,
        SlackEvent,
        SlackEventCallback,
        SlackEventKey,
        SlackFile,
        SlackHandlers,
        SlackInteractionCallback,
        SlackInteractionEvent,
        SlackMessageEvent,
        SlackMessageKey,
    )
    from managed_deepagents._channels.slack.status import (
        SlackStatusChoice,
        SlackStatusChooser,
        SlackStatusContext,
        SlackStatusStep,
        SlackStatusStepStatus,
        SlackStatusValue,
    )
    from managed_deepagents._channels.types import (
        ChannelContentMessage,
        ChannelEvent,
        ChannelInfo,
        ChannelMessage,
        ChannelNativeMessage,
        ChannelReply,
        JsonValue,
        MessageContent,
        OutboundMessage,
        PostedChannelMessage,
    )
    from managed_deepagents._connections import (
        ConnectionNotFoundError,
        CredentialAuthorizationRequiredError,
        CredentialStoreError,
        InvalidAgentAuthEndpointError,
        connections,
    )
    from managed_deepagents._connectors.mcp import (
        HttpMcpServerConfig,
        McpServersConfig,
        McpServersDefinition,
        RemoteMcpServerConfig,
        SseMcpServerConfig,
    )
    from managed_deepagents._managed_thread import ManagedThread, ThreadOrigin, ThreadParticipant
    from managed_deepagents._managed_tools import (
        ManagedDeepAgentRuntime,
        ManagedRuntime,
        ManagedServerRuntime,
        ManagedToolRuntime,
    )
    from managed_deepagents._runtime_schedules import (
        RuntimeSchedules,
        ScheduleNotFoundError,
        ScheduleOwner,
        SchedulePage,
        SchedulePatch,
        ScheduleRecord,
        schedules,
    )
    from managed_deepagents.agent_entry import (
        AgentEntry,
        AgentFactory,
        SharedAgentFactory,
        SharedInputs,
        shared_factory,
    )
    from managed_deepagents.define_browser import BrowserDefinition, define_browser
    from managed_deepagents.define_deep_agent import (
        DeepAgentDefinition,
        GeneratedSkill,
        McpSelection,
        SkillSelection,
        define_deep_agent,
    )
    from managed_deepagents.define_memory import (
        MemoryDefinition,
        MemoryLayer,
        define_memory,
    )
    from managed_deepagents.identity import ManagedServerInfo, define_identity
    from managed_deepagents.namespaces import (
        auth,
        channels,
        define_mcp,
        define_sandbox,
    )
    from managed_deepagents.run_context import (
        INTERRUPT_CORRELATION_KEY,
        ChannelContext,
        ManagedChannelContext,
        ManagedRunContext,
        NormalizedChannelEvent,
        PromptResponse,
        SlackPromptResponse,
    )
    from managed_deepagents.sandbox import (
        SandboxDefinition,
        SandboxReplacement,
        basic,
        bearer,
        open_sandbox,
        replace_sandbox,
    )
    from managed_deepagents.schedules import define_schedule

# Each export loads on first use (PEP 562), so a service that only needs the
# endpoint types doesn't pay for deepagents and LangChain at startup.
_EXPORTS: dict[str, str] = {
    "AgentEntry": "managed_deepagents.agent_entry",
    "AgentFactory": "managed_deepagents.agent_entry",
    "BrowserDefinition": "managed_deepagents.define_browser",
    "ChannelActor": "managed_deepagents._channels.actor",
    "ChannelContentMessage": "managed_deepagents._channels.types",
    "ChannelContext": "managed_deepagents.run_context",
    "ChannelEvent": "managed_deepagents._channels.types",
    "ChannelInfo": "managed_deepagents._channels.types",
    "ChannelMessage": "managed_deepagents._channels.types",
    "ChannelName": "managed_deepagents._channels.http",
    "ChannelNativeMessage": "managed_deepagents._channels.types",
    "ChannelReply": "managed_deepagents._channels.types",
    "ChannelRunInput": "managed_deepagents._channels.run_input",
    "ChannelRunOptions": "managed_deepagents._channels.run_input",
    "ChannelRunResult": "managed_deepagents._channels.run_input",
    "ConnectionNotFoundError": "managed_deepagents._connections",
    "CredentialAuthorizationRequiredError": "managed_deepagents._connections",
    "CredentialStoreError": "managed_deepagents._connections",
    "DeepAgentDefinition": "managed_deepagents.define_deep_agent",
    "EndpointAnswer": "managed_deepagents._channels.http.endpoint",
    "EndpointEvent": "managed_deepagents._channels.http.endpoint",
    "EndpointRequest": "managed_deepagents._channels.http.endpoint",
    "EndpointThread": "managed_deepagents._channels.http.endpoint",
    "EventEndpoint": "managed_deepagents._channels.http.endpoint",
    "EventFinished": "managed_deepagents._channels.http.provider",
    "GeneratedSkill": "managed_deepagents.define_deep_agent",
    "GitHubActor": "managed_deepagents._channels.actor",
    "GitHubAppOptions": "managed_deepagents._channels.github_types",
    "GitHubChannelThread": "managed_deepagents._channels.github_types",
    "GitHubCheckRunPayload": "managed_deepagents._channels.github_types",
    "GitHubCheckSuitePayload": "managed_deepagents._channels.github_types",
    "GitHubEndpointEvent": "managed_deepagents._channels.github_types",
    "GitHubEndpointRequest": "managed_deepagents._channels.github_types",
    "GitHubEvent": "managed_deepagents._channels.github_types",
    "GitHubEventCallback": "managed_deepagents._channels.github_types",
    "GitHubEventResult": "managed_deepagents._channels.github_types",
    "GitHubHandlers": "managed_deepagents._channels.github_types",
    "GitHubIssueCommentPayload": "managed_deepagents._channels.github_types",
    "GitHubIssuePayload": "managed_deepagents._channels.github_types",
    "GitHubLabeledIssuePayload": "managed_deepagents._channels.github_types",
    "GitHubLabeledPullRequestPayload": "managed_deepagents._channels.github_types",
    "GitHubPayload": "managed_deepagents._channels.github_types",
    "GitHubPullRequestPayload": "managed_deepagents._channels.github_types",
    "GitHubPushPayload": "managed_deepagents._channels.github_types",
    "GitHubReviewCommentPayload": "managed_deepagents._channels.github_types",
    "GitHubReviewPayload": "managed_deepagents._channels.github_types",
    "GitHubStatusPayload": "managed_deepagents._channels.github_types",
    "GitHubWorkflowPayload": "managed_deepagents._channels.github_types",
    "HttpChannel": "managed_deepagents._channels.http",
    "HttpChannelCancelMessage": "managed_deepagents._channels.http",
    "HttpChannelInboundMessage": "managed_deepagents._channels.http",
    "HttpChannelParseResult": "managed_deepagents._channels.http",
    "HttpChannelPostInput": "managed_deepagents._channels.http",
    "HttpChannelRequest": "managed_deepagents._channels.http",
    "HttpDelivery": "managed_deepagents._channels.http",
    "HttpFinish": "managed_deepagents._channels.http",
    "HttpFinishedDelivery": "managed_deepagents._channels.http",
    "HttpFinishedRun": "managed_deepagents._channels.http",
    "HttpMcpServerConfig": "managed_deepagents._connectors.mcp",
    "HttpPostedMessage": "managed_deepagents._channels.http",
    "HttpRunStatus": "managed_deepagents._channels.http",
    "HttpSkipReason": "managed_deepagents._channels.http",
    "INTERRUPT_CORRELATION_KEY": "managed_deepagents.run_context",
    "InvalidAgentAuthEndpointError": "managed_deepagents._connections",
    "JsonValue": "managed_deepagents._channels.types",
    "LinearActor": "managed_deepagents._channels.linear_types",
    "LinearAppOptions": "managed_deepagents._channels.linear_types",
    "LinearChannelThread": "managed_deepagents._channels.linear_types",
    "LinearCommentCreateEvent": "managed_deepagents._channels.linear_types",
    "LinearCommentCreatePayload": "managed_deepagents._channels.linear_schema",
    "LinearEndpointRequest": "managed_deepagents._channels.linear_types",
    "LinearEvent": "managed_deepagents._channels.linear_types",
    "LinearEventCallback": "managed_deepagents._channels.linear_types",
    "LinearEventKey": "managed_deepagents._channels.linear_types",
    "LinearEventResult": "managed_deepagents._channels.linear_types",
    "LinearHandlers": "managed_deepagents._channels.linear_types",
    "LinearIssueCreateEvent": "managed_deepagents._channels.linear_types",
    "LinearIssueCreatePayload": "managed_deepagents._channels.linear_schema",
    "LinearIssueUpdateEvent": "managed_deepagents._channels.linear_types",
    "LinearIssueUpdatePayload": "managed_deepagents._channels.linear_schema",
    "LinearPayload": "managed_deepagents._channels.linear_schema",
    "LinearRateLimitError": "managed_deepagents._channels.linear_graphql",
    "LinearSessionCreatedEvent": "managed_deepagents._channels.linear_types",
    "LinearSessionCreatedPayload": "managed_deepagents._channels.linear_schema",
    "LinearSessionEvent": "managed_deepagents._channels.linear_types",
    "LinearSessionPromptedEvent": "managed_deepagents._channels.linear_types",
    "LinearSessionPromptedPayload": "managed_deepagents._channels.linear_schema",
    "LinearTeam": "managed_deepagents._channels.linear_types",
    "ManagedChannelContext": "managed_deepagents.run_context",
    "ManagedDeepAgentRuntime": "managed_deepagents._managed_tools",
    "ManagedRunContext": "managed_deepagents.run_context",
    "ManagedRuntime": "managed_deepagents._managed_tools",
    "ManagedServerInfo": "managed_deepagents.identity",
    "ManagedServerRuntime": "managed_deepagents._managed_tools",
    "ManagedThread": "managed_deepagents._managed_thread",
    "ManagedToolRuntime": "managed_deepagents._managed_tools",
    "McpSelection": "managed_deepagents.define_deep_agent",
    "McpServersConfig": "managed_deepagents._connectors.mcp",
    "McpServersDefinition": "managed_deepagents._connectors.mcp",
    "MemoryDefinition": "managed_deepagents.define_memory",
    "MemoryLayer": "managed_deepagents.define_memory",
    "MessageContent": "managed_deepagents._channels.types",
    "NormalizedChannelEvent": "managed_deepagents.run_context",
    "OutboundMessage": "managed_deepagents._channels.types",
    "PostedChannelMessage": "managed_deepagents._channels.types",
    "PromptResponse": "managed_deepagents.run_context",
    "RemoteMcpServerConfig": "managed_deepagents._connectors.mcp",
    "RuntimeChannel": "managed_deepagents._channels.runtime",
    "RuntimeSchedules": "managed_deepagents._runtime_schedules",
    "SandboxDefinition": "managed_deepagents.sandbox",
    "SandboxReplacement": "managed_deepagents.sandbox",
    "ScheduleNotFoundError": "managed_deepagents._runtime_schedules",
    "ScheduleOwner": "managed_deepagents._runtime_schedules",
    "SchedulePage": "managed_deepagents._runtime_schedules",
    "SchedulePatch": "managed_deepagents._runtime_schedules",
    "ScheduleRecord": "managed_deepagents._runtime_schedules",
    "SharedAgentFactory": "managed_deepagents.agent_entry",
    "SharedInputs": "managed_deepagents.agent_entry",
    "SkillSelection": "managed_deepagents.define_deep_agent",
    "SlackActor": "managed_deepagents._channels.slack.events",
    "SlackChannelThread": "managed_deepagents._channels.slack.events",
    "SlackEndpointRequest": "managed_deepagents._channels.slack.events",
    "SlackEvent": "managed_deepagents._channels.slack.events",
    "SlackEventCallback": "managed_deepagents._channels.slack.events",
    "SlackEventKey": "managed_deepagents._channels.slack.events",
    "SlackFile": "managed_deepagents._channels.slack.events",
    "SlackHandlers": "managed_deepagents._channels.slack.events",
    "SlackInteractionCallback": "managed_deepagents._channels.slack.events",
    "SlackInteractionEvent": "managed_deepagents._channels.slack.events",
    "SlackMessageEvent": "managed_deepagents._channels.slack.events",
    "SlackMessageKey": "managed_deepagents._channels.slack.events",
    "SlackPromptResponse": "managed_deepagents.run_context",
    "SlackStatusChoice": "managed_deepagents._channels.slack.status",
    "SlackStatusChooser": "managed_deepagents._channels.slack.status",
    "SlackStatusContext": "managed_deepagents._channels.slack.status",
    "SlackStatusStep": "managed_deepagents._channels.slack.status",
    "SlackStatusStepStatus": "managed_deepagents._channels.slack.status",
    "SlackStatusValue": "managed_deepagents._channels.slack.status",
    "SseMcpServerConfig": "managed_deepagents._connectors.mcp",
    "SupportedEventKey": "managed_deepagents._channels.github_types",
    "ThreadOrigin": "managed_deepagents._managed_thread",
    "ThreadParticipant": "managed_deepagents._managed_thread",
    "auth": "managed_deepagents.namespaces",
    "basic": "managed_deepagents.sandbox",
    "bearer": "managed_deepagents.sandbox",
    "channels": "managed_deepagents.namespaces",
    "connections": "managed_deepagents._connections",
    "define_browser": "managed_deepagents.define_browser",
    "define_deep_agent": "managed_deepagents.define_deep_agent",
    "define_identity": "managed_deepagents.identity",
    "define_mcp": "managed_deepagents.namespaces",
    "define_memory": "managed_deepagents.define_memory",
    "define_sandbox": "managed_deepagents.namespaces",
    "define_schedule": "managed_deepagents.schedules",
    "open_sandbox": "managed_deepagents.sandbox",
    "replace_sandbox": "managed_deepagents.sandbox",
    "schedules": "managed_deepagents._runtime_schedules",
    "shared_factory": "managed_deepagents.agent_entry",
}


# Hidden from type checkers: they read the exports from the imports above, and a
# module-level __getattr__ would make them accept any name, typed Any.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        try:
            module = _EXPORTS[name]
        except KeyError:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        value = getattr(importlib.import_module(module), name)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*globals(), *__all__})


#: Exports that share their name with a submodule of this package.
_SUBMODULE_EXPORTS: Final = frozenset(
    {"define_browser", "define_deep_agent", "define_memory", "schedules"}
)


class _Package(types.ModuleType):
    """Keeps an export that shares a submodule's name, such as schedules.

    Python binds a submodule to its package's attribute when it is first
    imported, so importing managed_deepagents.schedules would hide the
    schedules export until it is loaded. The export must win, as it did when
    every export was imported up front. Only that binding is skipped: every
    other assignment, of any value and by anyone, goes through.
    """

    def __setattr__(self, name: str, value: object) -> None:
        if name in _SUBMODULE_EXPORTS and value is sys.modules.get(f"{__name__}.{name}"):
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Package


__all__ = [
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
