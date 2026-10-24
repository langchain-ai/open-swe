"""Name → messaging transport registry.

Filled when channels (and connectors that still contribute messaging, e.g.
GitHub) are mounted / collected. ``resolve_runtime_channel`` and schedule
delivery look up transports by file-stem name.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from managed_deepagents._channels.source_providers import register_declared_source_providers

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from managed_deepagents._channels.http.types import HttpDelivery
    from managed_deepagents._channels.manifest import JsonObject
    from managed_deepagents._channels.runtime import ChannelMessagingAdapter
    from managed_deepagents._channels.types import JsonValue

MessagingFactory = Callable[[Mapping[str, str]], "ChannelMessagingAdapter | None"]
#: Tells a channel's conversation that its thread waits on a decision, with a
#: Studio link to the delivery's thread. Internal: GitHub and Linear have one.
PausedNotice = Callable[["JsonValue", "HttpDelivery"], "Awaitable[None]"]

_provider_by_name: dict[str, str] = {}
_provider_config_by_name: dict[str, JsonObject] = {}
_replies_none: set[str] = set()


def channel_provider_for_name(name: str) -> str | None:
    return _provider_by_name.get(name)


def channel_provider_config_for_name(name: str) -> JsonObject | None:
    return _provider_config_by_name.get(name)


def channel_auto_replies_for_name(name: str) -> bool:
    """Whether the channel posts a run's final answer: ``False`` only for one
    declared with ``reply="none"``, which leaves replying to the agent."""
    return name not in _replies_none


_by_name: dict[str, MessagingFactory] = {}
_paused_notice_by_name: dict[str, PausedNotice] = {}

__all__ = [
    "channel_auto_replies_for_name",
    "channel_provider_config_for_name",
    "messaging_transport_for_name",
    "paused_notice_for_name",
    "register_channel_messaging",
    "register_connector_messaging",
]


def _register_messaging_sources(sources: Sequence[object]) -> None:
    register_declared_source_providers(sources)
    for source in sources:
        name = getattr(source, "name", None)
        messaging = getattr(source, "messaging", None)
        if not isinstance(name, str) or not name:
            continue

        if callable(messaging):

            def _factory(
                env: Mapping[str, str],
                *,
                _messaging: Callable[..., object] = messaging,
            ) -> ChannelMessagingAdapter | None:
                return cast("ChannelMessagingAdapter | None", _messaging(env))

            _by_name[name] = _factory
        else:
            _by_name.pop(name, None)
        _register_paused_notice(name, source)
        kind = getattr(source, "kind", None)
        if isinstance(kind, str) and kind.strip():
            _provider_by_name[name] = kind
        _provider_config_by_name.pop(name, None)
        if getattr(source, "reply", None) == "none":
            _replies_none.add(name)
        else:
            _replies_none.discard(name)
        requirements = getattr(source, "requirements", None)
        if callable(requirements):
            manifest = requirements(name)
            if isinstance(manifest, Mapping):
                _register_manifest(name, manifest)


def _register_manifest(name: str, manifest: Mapping[str, object]) -> None:
    provider = manifest.get("provider")
    if isinstance(provider, str) and provider.strip():
        _provider_by_name[name] = provider
    config = manifest.get("providerConfig")
    if isinstance(config, Mapping):
        _provider_config_by_name[name] = cast("JsonObject", config)


def _register_paused_notice(name: str, source: object) -> None:
    paused_notice = getattr(source, "paused_notice", None)
    if callable(paused_notice):
        _paused_notice_by_name[name] = paused_notice
    else:
        _paused_notice_by_name.pop(name, None)


def register_channel_messaging(channels: Sequence[object]) -> None:
    """Register messaging factories from collected channels."""
    _register_messaging_sources(channels)


def register_connector_messaging(connectors: Sequence[object]) -> None:
    """Register messaging factories from collected connectors (e.g. GitHub)."""
    _register_messaging_sources(connectors)


def paused_notice_for_name(name: str) -> PausedNotice | None:
    """The channel's notice that its thread waits on a decision, if it has one."""
    return _paused_notice_by_name.get(name)


def messaging_transport_for_name(
    name: str, env: Mapping[str, str] | None = None
) -> ChannelMessagingAdapter | None:
    """Build a transport for a channel/connector file-stem name."""
    factory = _by_name.get(name)
    if factory is None:
        return None
    return factory(os.environ if env is None else env)
