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

from managed_deepagents._channels.http import (
    HttpChannel,
    HttpChannelInboundMessage,
    HttpChannelParseResult,
    HttpChannelPostInput,
    HttpChannelRequest,
    HttpPostedMessage,
)
from managed_deepagents._channels.types import (
    ChannelContentMessage,
    ChannelEvent,
    ChannelInfo,
    ChannelMessage,
    ChannelNativeMessage,
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
    connectors,
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

__all__ = [
    "INTERRUPT_CORRELATION_KEY",
    "AgentEntry",
    "AgentFactory",
    "BrowserDefinition",
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
    "SandboxReplacement",
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
]

from managed_deepagents._connectors.mcp import (
    HttpMcpServerConfig,
    McpServersConfig,
    McpServersDefinition,
    RemoteMcpServerConfig,
    SseMcpServerConfig,
)
