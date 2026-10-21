"""Provider-neutral channel manifest — compile/deploy/runtime contract."""

from __future__ import annotations

from typing import Literal, TypedDict

from managed_deepagents._channels.types import JsonValue

__all__ = [
    "ChannelManifest",
    "JsonObject",
]

JsonObject = dict[str, JsonValue]


class _OptionalChannelManifest(TypedDict, total=False):
    eventsPath: str
    transport: Literal["trigger_server"]
    userCredentials: list[str]
    userConnections: list[str]
    agentConnections: list[str]
    needsDeveloperPrincipal: bool


class ChannelManifest(_OptionalChannelManifest):
    """Compile/deploy envelope for a connector Events / messaging declaration."""

    name: str
    provider: str
    autoReply: bool
    requiredPermissions: list[str]
    requiredEnv: list[str]
    providerConfig: JsonObject
